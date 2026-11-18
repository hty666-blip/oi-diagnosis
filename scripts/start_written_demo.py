"""在独立的本机 MySQL 容器中启动初赛演示，退出时清理本次环境。"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = ROOT / "web"
EXAMPLES = ROOT / "examples" / "written"
DATABASE = "oi_written_demo"
DB_USER = "oi_demo"
IMAGE = "mysql:8.4"
CONTAINER_LABEL = "io.oi-diagnosis.written-demo"
ASSIGNMENTS = "/written/assignments"
WRONG_QUESTION = "demo-j-tree-bounds"
NEW_QUESTION = "demo-j-tree-depth"
SHORT_QUESTION = "demo-short-tree-level"

sys.path.insert(0, str(BACKEND))


class DemoError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


def command(args: list[str], *, env: dict | None = None, cwd: Path = ROOT,
            timeout: int = 45, log: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, check=False, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if log is not None:
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        if args[:2] == ["docker", "run"]:
            raise DemoError("独立 MySQL 容器未能启动，请检查 Docker Desktop 和 mysql:8.4 镜像。")
        raise DemoError(f"命令未完成（{Path(args[0]).name}，退出码 {result.returncode}）。")
    return result.stdout.strip()


def port_available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def demo_env(password: str, mysql_port: int, log_path: Path, *, enable_model: bool) -> dict:
    database = URL.create("mysql+asyncmy", DB_USER, password, "127.0.0.1", mysql_port,
                          DATABASE, {"charset": "utf8mb4", "connect_timeout": "3"})
    config = {
        **os.environ,
        "PYTHONUTF8": "1",
        "APP_ENV": "dev",
        "DATABASE_URL": database.render_as_string(hide_password=False),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "JWT_ALG": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "60",
        "LLM_ENABLED": str(enable_model).lower(),
        "JUDGE_WORKER_ENABLED": "false",
        "MAINTENANCE_WORKER_ENABLED": "false",
        "BACKUP_VERIFY_DATABASE_URL": "",
        "BACKUP_DIRECTORY": str(log_path / "backups"),
    }
    if not enable_model:
        config.update(LLM_API_BASE="", LLM_API_KEY="", LLM_MODEL_FAST="", LLM_MODEL_REASONING="")
    return config


async def wait_database(env: dict) -> None:
    engine = create_async_engine(env["DATABASE_URL"], hide_parameters=True)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                # TCP 可连不等于 MySQL 已完成握手和认证。
                async with asyncio.timeout(4):
                    async with engine.connect() as conn:
                        db, version = (await conn.execute(text("SELECT DATABASE(), VERSION()"))).one()
                        require(db == DATABASE and version.startswith("8.4."), "数据库不是独立演示 MySQL 8.4。")
                        return
            except (OperationalError, TimeoutError):
                await asyncio.sleep(1)
        raise DemoError("独立 MySQL 未就绪。")
    finally:
        await engine.dispose()


def start_service(args: list[str], *, env: dict, cwd: Path, log_path: Path) -> subprocess.Popen:
    with log_path.open("wb") as log:
        return subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)


def stop_service(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stop_container(name: str) -> None:
    label = subprocess.run(
        ["docker", "inspect", "--format", f'{{{{index .Config.Labels "{CONTAINER_LABEL}"}}}}', name],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if label.returncode == 0 and label.stdout.strip() == name:
        command(["docker", "stop", "-t", "5", name])


def wait_http(url: str, process: subprocess.Popen, *, check_db: bool = False) -> None:
    end = time.monotonic() + 30
    with httpx.Client(trust_env=False, timeout=2) as client:
        while process.poll() is None and time.monotonic() < end:
            try:
                ready = client.get(url)
                if ready.is_success and (not check_db or ready.json().get("db") == "up"):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
    raise DemoError(f"演示服务未就绪：{url}。请查看本次运行日志。")


class API:
    def __init__(self, client: httpx.AsyncClient, token: str = ""):
        self.client = client
        self.token = token

    async def call(self, method: str, path: str, *, expect: int | None = None, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers = {**headers, "Authorization": f"Bearer {self.token}"}
        result = await self.client.request(method, path, headers=headers, **kwargs)
        if expect is not None:
            require(result.status_code == expect, f"{method} {path} 应返回 {expect}，实际 {result.status_code}。")
        elif not result.is_success:
            raise DemoError(f"{method} {path} 失败（HTTP {result.status_code}）。")
        return result.json()


async def login(client: httpx.AsyncClient, username: str, password: str, role: str) -> API:
    from app.core.security import decode_token

    receipt = await API(client).call("POST", "/auth/login", json={"username": username, "password": password})
    token = decode_token(receipt["access_token"])
    # 防止端口竞争或错误连接时把发布请求发给另一套正在运行的应用。
    require(token is not None and token["role"] == role and token["username"] == username,
            "当前 API 未使用本次演示独有的签名，停止发布。")
    return API(client, receipt["access_token"])


async def bootstrap_users(env: dict, accounts: list[dict]) -> int:
    # 只在本进程创建的容器内引导首批账号；发布一律走实际 API。
    from app.core.security import hash_password
    from app.domain.models import ClassTeacher, SchoolClass, User
    from app.infra.db import configure_database_engine

    engine = create_async_engine(env["DATABASE_URL"], hide_parameters=True)
    configure_database_engine(engine)
    try:
        async with AsyncSession(engine) as session, session.begin():
            count = await session.scalar(text("SELECT COUNT(*) FROM app_user"))
            require(count == 0, "演示库非空，停止写入。")
            group = SchoolClass(name="二叉树演示班")
            session.add(group)
            await session.flush()
            for account in accounts:
                student = account["role"] == "student"
                user = User(username=account["username"], display_name=account["name"], role=account["role"],
                            password_hash=hash_password(account["password"]),
                            class_id=group.id if student else None, class_name=group.name if student else "")
                session.add(user)
                if account["role"] == "teacher":
                    await session.flush()
                    session.add(ClassTeacher(class_id=group.id, teacher_id=user.id))
            return group.id
    finally:
        await engine.dispose()


def lecture_sections() -> dict[str, str]:
    source = (EXAMPLES / "知识条目.md").read_text(encoding="utf-8")
    _, *parts = re.split(r"^## +", source, flags=re.MULTILINE)
    sections = {}
    for part in parts:
        title, _, body = part.partition("\n")
        title = title.strip()
        require(bool(title and body.strip()) and title not in sections, "原创讲义标题重复或正文为空。")
        sections[title] = body.strip()
    return sections


async def publish_pack(admin: API, name: str, expected: int) -> tuple[str, dict[str, int]]:
    base = "/admin/written-bank"
    archive = (EXAMPLES / name).read_bytes()
    preview = await admin.call("POST", base + "/imports/preview", content=archive,
                               headers={"Content-Type": "application/zip"})
    batch = preview["id"]
    collection = f"{base}/batches/{batch}"
    await admin.call("POST", collection + "/stage")
    candidate = await admin.call("GET", collection + "/questions")
    require(candidate["total"] == expected and len(candidate["items"]) == expected,
            f"原创包 {name} 的题数有变化，请核对演示清单。")
    published = {}
    for row in candidate["items"]:
        external = row["external_id"]
        path = f"{collection}/questions/{external}"
        detail = await admin.call("GET", path)
        original = detail["record"]
        review = detail["review"]
        require(original["source_kind"] == "original_demo" and review["revision"] == 0,
                "只自动发布仓库中的未改动原创演示题。")
        approval = await admin.call("POST", path + "/review", json={
            "expected_revision": review["revision"], "request_id": str(uuid4()),
            "action": "approve", "rights_confirmed": True,
            "note": "演示初始化：按仓库原创题样例和评分规则核对。",
            "draft": {**review["draft"], "answer_reference": "仓库已校对的原创题参考解答和计分规则。"},
        })
        published[external] = approval["review"]["publication"]["id"]
    return batch, published


async def publish_lectures(admin: API, batch: str) -> None:
    base = "/admin/written-knowledge"
    topics = await admin.call("GET", base + "/topics", params={"batch_id": batch})
    sections = lecture_sections()
    require(len(sections) == 8 and set(sections) == {topic["name"] for topic in topics["items"]},
            "8 篇讲义须与原创题包的叶子目录一一对应。")
    for topic in topics["items"]:
        title = topic["name"]
        article = {"title": title, "body": sections[title], "source": f"本项目原创演示讲义 · {title}"}
        entry = await admin.call("POST", base, json={"batch_id": batch, "point_id": topic["id"], "article": article})
        await admin.call("POST", f"{base}/{entry['id']}", json={
            "expected_version": entry["edit_version"],
            "action": "publish", "article": article, "review_note": "按原创演示讲义发布。",
        })


async def publish_assignment(teacher: API, group: int, title: str, rows: list[dict]) -> int:
    result = await teacher.call("POST", ASSIGNMENTS, json={
        "title": title, "class_id": group, "request_id": str(uuid4()),
        "due_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        "questions": [{"id": q["id"], "version_id": q["version_id"]} for q in rows],
    })
    return result["id"]


async def prepare(env: dict, api_url: str, *, check_only: bool) -> None:
    # Security bootstrap imports project settings after selecting the disposable database.
    for key in ("DATABASE_URL", "JWT_SECRET", "JWT_ALG"):
        os.environ[key] = env[key]
    async with httpx.AsyncClient(base_url=f"{api_url}/api/v1", timeout=40, trust_env=False) as client:
        accounts = {row["username"]: row for row in await API(client).call("GET", "/auth/demo-accounts")}
        roles = {"admin": "admin", "teacher01": "teacher", "student01": "student", "student02": "student"}
        require({name: row["role"] for name, row in accounts.items()} == roles, "演示账号清单与预期不符。")
        group = await bootstrap_users(env, list(accounts.values()))
        admin, teacher, student, peer = [
            await login(client, name, accounts[name]["password"], role) for name, role in roles.items()
        ]
        batch, objective = await publish_pack(admin, "oi-written-demo.zip", 16)
        _, subjective = await publish_pack(admin, "oi-written-short-demo.zip", 1)
        await publish_lectures(admin, batch)
        sample = {}
        for external in (WRONG_QUESTION, NEW_QUESTION, SHORT_QUESTION):
            question_id = (objective | subjective)[external]
            sample[external] = await student.call("GET", f"/written/questions/{question_id}")
        objective_task = await publish_assignment(teacher, group, "二叉树知识专项",
                                                  [sample[WRONG_QUESTION], sample[NEW_QUESTION]])
        short_task = await publish_assignment(teacher, group, "二叉树简答练习", [sample[SHORT_QUESTION]])
        lectures = await student.call("GET", "/written/knowledge")
        require(len(lectures["items"]) == 8, "学生没有读到全部 8 篇讲义。")
        papers = await student.call("GET", "/written/papers")
        questions = await student.call("GET", "/written/questions")
        require(len(questions["items"]) == 17 and len(papers["items"]) == 3, "学生题库或试卷数量异常。")
        require(len(await student.call("GET", ASSIGNMENTS)) == 2, "学生没有读到两份专项。")
        for paper_id in ("demo-j-paper", "demo-s-paper"):
            paper = await student.call("GET", f"/written/papers/{batch}/{paper_id}")
            require(paper["paper"]["mock_available"] and len(paper["items"]) == 8, "原创客观题卷不能模拟。")
        print("演示已准备：J/S 各 8 道客观题、1 道简答、8 篇讲义、2 份班级专项。", flush=True)
        if check_only:
            await exercise(student, peer, teacher, sample, batch, objective_task, short_task)


async def exercise(student: API, peer: API, teacher: API, rows: dict,
                   batch: str, objective_task: int, short_task: int) -> None:
    async def submit(external: str, assignment: int, response: dict):
        question = rows[external]
        return await student.call("POST", "/written/attempts", json={
            "question_id": question["id"], "version_id": question["version_id"],
            "assignment_id": assignment, "request_id": str(uuid4()), "response": response,
        })

    wrong = await submit(WRONG_QUESTION, objective_task, {"kind": "options", "option_ids": ["A"]})
    require(wrong["correct"] is False and wrong["earned_score"] == 0, "客观题错项没有按规则记分。")
    wrong_path = f"/written/attempts/{wrong['id']}"
    await peer.call("GET", wrong_path, expect=404)
    helper = await student.call("POST", wrong_path + "/help", json={
        "request_id": str(uuid4()), "message": "第 5 层最多 16 个，为什么整棵树是 31 个？",
    })
    require(helper["tutor"]["source"] == "reviewed" and bool(helper["tutor"]["reply"]),
            "关闭模型后的审核解析不可用。")
    require(helper.get("knowledge", {}).get("title") == "二叉树层数与节点数",
            "错题证据未关联本次发布的二叉树讲义。")
    fixed = await submit(NEW_QUESTION, objective_task, {"kind": "boolean", "value": False})
    route = await student.call("GET", "/written/progress")
    require(fixed["correct"] and any(item["status"] == "reviewed" for item in route["items"]),
            "同知识点的新题未更新学习路线。")
    matrix = await teacher.call("GET", f"{ASSIGNMENTS}/{objective_task}")
    require(sorted((row["submitted_count"], row["correct_count"]) for row in matrix["rows"]) == [(0, 0), (2, 1)],
            "专项学情与学生作答不一致。")

    exam = await student.call("POST", f"/written/exams/start/{batch}/demo-j-paper")
    exam_path = f"/written/exams/{exam['id']}"
    exam_question = exam_path + f"/questions/{exam['items'][0]['question_id']}"
    before = await student.call("GET", exam_question)
    require("answer" not in before and exam["status"] == "working", "模拟交卷前不应返回答案。")
    await student.call("GET", exam_path + "/study", expect=409)
    await student.call("PUT", exam_question, json={"expected_revision": 0,
                                                    "response": {"kind": "option", "option_ids": ["B"]}})
    restored = await student.call("GET", exam_question)
    require(restored["response"]["option_ids"] == ["B"], "模拟答题卡没有保存。")
    finished = await student.call("POST", exam_path + "/submit")
    require(finished["earned_score"] == 2 and finished["max_score"] == 16, "原创模拟卷预期得分为 2 / 16。")
    study_path = exam_path + "/study"
    await peer.call("GET", study_path, expect=404)
    topics = (await student.call("GET", study_path))["items"]
    require(sum(len(topic["numbers"]) for topic in topics) == 7
            and any(topic["article"] for topic in topics), "错题按知识点归类或讲义入口有误。")
    results_path = f"/written/exams/classes/{matrix['class_id']}/results"
    await student.call("GET", results_path, expect=403)
    results = (await teacher.call("GET", results_path))["items"]
    require(len(results) == 1 and results[0]["id"] == exam["id"] and results[0]["missed_count"] == 7
            and results[0]["corrected_count"] == 0, "教练没有读到本班已交的模拟答卷。")
    review_path = exam_path + "/review"
    errors = (await student.call("GET", review_path))["items"]
    require(len(errors) == 7 and not errors[0]["answered"], "模拟未答题没有进入复练清单。")
    first_error = errors[0]
    retry_id = first_error["question_id"]
    retry_question = await student.call("GET", f"{review_path}/{retry_id}")
    require(retry_question["question_type"] == "fill_blank" and "answer" not in retry_question,
            "复练第一题不是原卷还未作答的填空，或提前返回了答案。")
    practice = await student.call("POST", "/written/attempts", json={
        "exam_id": exam["id"], "question_id": retry_id, "version_id": retry_question["version_id"],
        "request_id": str(uuid4()), "response": {"kind": "text", "value": "22"},
    })
    reviewed = (await student.call("GET", review_path))["items"]
    require(reviewed[0]["latest_attempt"] == {"id": practice["id"], "correct": True}
            and (await student.call("GET", exam_path))["earned_score"] == 2,
            "原卷复练进度或卷分有误。")
    results = (await teacher.call("GET", results_path))["items"]
    require(len(results) == 1 and results[0]["earned_score"] == 2 and results[0]["max_score"] == 16
            and results[0]["missed_count"] == 7 and results[0]["corrected_count"] == 1,
            "教练看到的卷分或复练次数与学生侧不一致。")
    report = await teacher.call("GET", f"{results_path}/{exam['id']}/questions")
    retry_row = next((row for row in report["items"] if row["question_id"] == retry_id), {})
    require(report["submitted_count"] == 1 and len(report["items"]) == 8
            and retry_row.get("blank_count") == 1 and retry_row.get("corrected_count") == 1,
            "模拟卷的单题学情没有反映未答和复练。")

    short = await submit(SHORT_QUESTION, short_task, {"kind": "text", "value": "每个节点最多有两个孩子，所以下一层最多翻倍。"})
    require(short["earned_score"] is None and short["answer"] is None, "简答提交后应待复核。")
    review = f"{ASSIGNMENTS}/{short_task}/short-reviews/{short['id']}"
    await teacher.call("POST", review + "/suggestion", expect=503)
    await teacher.call("POST", review, json={"points": [2, 0], "feedback": "已说明翻倍，还需从根推导到第五层。"})
    final = await student.call("GET", f"/written/attempts/{short['id']}")
    require(final["earned_score"] == 2 and final["teacher_grade"]["points"] == [2, 0],
            "学生没有读到复核成绩。")
    print("自动验收通过：规则判分和离线答疑、学习路线与学情、模拟交卷和复练、教练模拟学情、简答人工评分。", flush=True)


def run(args: argparse.Namespace) -> None:
    require(shutil.which("docker") is not None, "请先启动 Docker Desktop。")
    vite = WEB / "node_modules" / "vite" / "bin" / "vite.js"
    if not args.check_only:
        require(bool(shutil.which("node")) and vite.is_file(), "请先安装 Node，并在 web 运行 pnpm install --frozen-lockfile。")
    require(args.check_only or args.api_port != args.web_port, "API 和页面端口必须不同。")
    for port in ([args.api_port] if args.check_only else [args.api_port, args.web_port]):
        require(port_available(port), f"端口 {port} 已占用，可更换端口参数。")
    try:
        command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=12)
    except (DemoError, subprocess.TimeoutExpired) as exc:
        raise DemoError("Docker 引擎未就绪，请在 Docker Desktop 恢复后重试。") from exc

    name = f"oi-written-demo-{uuid4().hex[:12]}"
    log_path = ROOT / "data" / "private" / "written-demo" / name
    log_path.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(32)
    processes = []
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    print(f"准备本次独立演示库。日志：{log_path}", flush=True)
    frontend_env = {**os.environ, "OI_API_TARGET": api_url}
    try:
        command(["docker", "run", "-d", "--rm", "--name", name,
                 "--label", f"{CONTAINER_LABEL}={name}", "-p", "127.0.0.1::3306",
                 "-e", "MYSQL_ROOT_PASSWORD", "-e", "MYSQL_PASSWORD",
                 "-e", f"MYSQL_USER={DB_USER}", "-e", f"MYSQL_DATABASE={DATABASE}",
                 IMAGE, "--log-bin-trust-function-creators=1", "--innodb-buffer-pool-size=64M",
                 "--performance-schema=OFF"],
                env={**os.environ, "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(32), "MYSQL_PASSWORD": password},
                timeout=240)
        binding = command(["docker", "port", name, "3306/tcp"])
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", binding)
        require(match is not None, "独立 MySQL 没有按预期绑定到回环地址。")
        env = demo_env(password, int(match[1]), log_path, enable_model=args.enable_model)
        asyncio.run(wait_database(env))
        try:
            command([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=BACKEND,
                    timeout=180, log=log_path / "migration.log")
        except DemoError as exc:
            raise DemoError("演示库迁移失败，请检查本次 migration.log。") from exc
        api = start_service([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(args.api_port)], env=env, cwd=BACKEND, log_path=log_path / "api.log")
        processes.append(api)
        wait_http(api_url + "/health", api, check_db=True)
        asyncio.run(prepare(env, api_url, check_only=args.check_only))
        if args.check_only:
            return
        web = start_service(["node", str(vite), "--host", "127.0.0.1", "--port", str(args.web_port), "--strictPort"],
                            env=frontend_env, cwd=WEB, log_path=log_path / "web.log")
        processes.append(web)
        wait_http(web_url, web)
        print(f"打开 {web_url}，从登录页选择演示账号。按 Ctrl+C 停止演示。", flush=True)
        print(f"外部模型：{'按本机配置启用' if args.enable_model else '关闭'}。", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise DemoError("演示服务意外退出，请查看本次日志。")
    finally:
        try:
            for process in reversed(processes):
                stop_service(process)
        finally:
            stop_container(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=18002)
    parser.add_argument("--web-port", type=int, default=5299)
    parser.add_argument("--enable-model", action="store_true", help="从本机配置启用模型；只使用原创题和合成作答。")
    parser.add_argument("--check-only", action="store_true", help="用隔离库验证三端数据和接口，随后退出。")
    args = parser.parse_args()
    if not all(1024 <= port <= 65535 for port in (args.api_port, args.web_port)):
        parser.error("端口须在 1024 至 65535 之间")
    if args.check_only and args.enable_model:
        parser.error("自动验收固定关闭外部模型")
    try:
        run(args)
    except KeyboardInterrupt:
        print("\n本次演示已结束。")
    except (DemoError, OSError, httpx.HTTPError, subprocess.TimeoutExpired) as exc:
        print(f"初赛演示未完成：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
