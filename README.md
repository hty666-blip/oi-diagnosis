# 青少年信息学竞赛错题诊断与答疑系统

本项目面向青少年信息学竞赛学习场景，把学生的一次代码提交转换为可检查的编译、运行和测试证据，再通过多 Agent 协作完成错误分诊、原因解释、分层提示以及修改后的重新验证。

项目强调“先有证据，再给结论”：确定性的编译错误、运行结果和测试记录由程序产生；大模型只作为可选的解释与引导能力，不能替代判题结果。

需求和验收以 [改进需求基线](docs/改进需求基线.md) 为准。其他设计、实验和论文材料可能包含历史方案，其中的目标指标不代表真实教学效果。

## 主要功能

- 学生、教师、管理员登录及数据权限隔离。
- 支持 C11、C++17、Java 17、Python 3 的代码提交、编译和运行。
- 保存编译信息、运行轨迹、测试范围和诊断证据。
- 多 Agent 协作完成表层检查、错误分诊、解释生成和输出审查。
- 按提示层级引导学生思考，避免直接泄露完整答案。
- 修改代码后重新提交，并用正式题包验证是否真正解决问题。
- 按用户、题目和语言保存草稿，恢复最近提交、提示记录及验证状态。
- 学生错题卡导出，以及教师班级诊断查看。
- 持久后台判题、刷新恢复、取消与有限重试；教师可查看授权范围内的队列。
- 同题修改父链、正式测试核验，以及教师确认/修订/退回的追加审计。
- 历史测试计数异常显示 `UNVERIFIED`，不凭口头声明、布尔计数或样例通过结束答疑。

判题结果中，公开样例通过记为 `SAMPLE_PASS`，没有测试数据记为 `NO_TESTS`；只有当前正式题包的非空测试全部通过才记为 `AC`。测试通过仅代表通过当前题包，不能证明学生已经掌握知识点。

## 技术架构

```text
React + TypeScript 前端
          │ HTTP / JSON
          ▼
FastAPI 接口与业务服务 ──────► MySQL 8
          │                     提交、证据、诊断、会话、用户
          ├──► 多 Agent 诊断与答疑编排
          │       └──► OpenAI 兼容模型接口（可选）
          └──► Docker 判题执行器
                  └──► GCC / G++ / Java 17 / Python 3
```

Redis 已作为开发基础设施预留；MinIO 为可选的 `full` profile。任务、租约和复核审计保存在 MySQL，核心判题流程不依赖 MinIO。

## 项目目录

| 目录 | 说明 |
| --- | --- |
| `backend/app/api/` | FastAPI 接口与请求、响应模型 |
| `backend/app/services/` | 提交、答疑、错题卡等业务服务 |
| `backend/app/agents/`、`backend/app/orchestrator/` | 多 Agent 诊断与编排 |
| `backend/app/capabilities/` | 判题、分诊、表层检查和反例最小化能力 |
| `backend/migrations/` | Alembic 数据库迁移 |
| `backend/tests/` | 后端单元测试和集成测试 |
| `web/src/` | React、TypeScript 前端 |
| `problems/PTV-*/` | 自建题包、正式测试和题面 |
| `scripts/` | 数据库初始化、题包导入、演示数据和启动脚本 |
| `deploy/` | Docker Compose 和判题镜像配置 |
| `docs/`、`eval/` | 设计、实验和评测资料 |

## 两人协作分工

下面以“成员 A / 成员 B”表示，请在实际使用时替换为姓名。

| 成员 | 主要负责 | 目录所有权 | 交付前检查 |
| --- | --- | --- | --- |
| 成员 A：后端与诊断 | FastAPI、数据库模型和迁移、判题执行器、多 Agent 编排、诊断与答疑服务 | `backend/`、`deploy/judge.Dockerfile` | `ruff check .`、`pytest -q`、数据库迁移、四语言判题冒烟测试 |
| 成员 B：前端与产品 | 登录和工作台、代码编辑与恢复、提示交互、教师页面、题目数据、使用文档 | `web/`、`problems/`、`docs/`、`README.md` | `pnpm build`、手动验证登录/提交/恢复/提示流程、题包检查 |

共同维护 `scripts/`、`.env.example` 和 `deploy/compose.dev.yml`。涉及 API 字段或数据库结构时，成员 A 先给出接口或迁移，成员 B 再接入页面；不要两个人同时修改同一个文件。

建议使用短分支和 Pull Request：

```text
backend/<功能名>     成员 A
frontend/<功能名>    成员 B
docs/<主题>          文档修改
```

每次合并前先同步主分支，PR 中写明“问题、改动、验证命令与结果”。前端变更附截图，数据库变更必须同时提交迁移文件。

## 从零部署（Windows 本地开发）

以下步骤适用于 Windows 10/11 与 PowerShell。当前仓库提供的是本地开发和受控演示部署，不建议直接暴露到公网。

### 1. 安装基础环境

需要安装：

- Git
- Python 3.11 或 3.12
- Node.js 22+
- pnpm 11
- Docker Desktop，并启用 WSL 2 后端

安装完成后检查：

```powershell
git --version
python --version
node --version
pnpm --version
docker version
docker compose version
```

如果尚未安装 pnpm，可以执行：

```powershell
corepack enable
corepack prepare pnpm@11 --activate
```

### 2. 获取代码

```powershell
git clone https://github.com/hty666-blip/oi-diagnosis.git
cd oi-diagnosis
```

如果代码已经下载，直接在项目根目录执行后续命令。

### 3. 创建本地配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少修改以下内容：

```dotenv
MYSQL_PASSWORD=请设置本地数据库密码
MYSQL_ROOT_PASSWORD=请设置本地数据库Root密码
DATABASE_URL=mysql+asyncmy://gesp_user:数据库密码@127.0.0.1:3307/oi_diagnosis?charset=utf8mb4&connect_timeout=5
JWT_SECRET=请替换为足够长的随机字符串
JUDGE_BACKEND=docker
```

`MYSQL_PASSWORD` 必须和 `DATABASE_URL` 中的密码一致；密码包含 `@`、`#`、`:` 等字符时，需要先进行 URL 编码。可以用下面的命令生成 JWT 密钥：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

大模型是可选能力。暂时不接模型时，将以下配置留空；编译、运行、确定性诊断和修改验证仍然可用：

```dotenv
LLM_API_BASE=
LLM_API_KEY=
LLM_MODEL_FAST=
LLM_MODEL_REASONING=
```

接入模型时，填写 OpenAI 兼容接口地址、密钥和模型名。不要提交包含真实密码或密钥的 `.env`。

### 4. 启动 MySQL 和 Redis

确认 Docker Desktop 已启动，然后执行：

```powershell
docker compose --env-file .env -f deploy/compose.dev.yml up -d mysql redis
docker compose --env-file .env -f deploy/compose.dev.yml ps
```

默认端口：

| 服务 | 地址 |
| --- | --- |
| MySQL | `127.0.0.1:3307` |
| Redis | `127.0.0.1:6379` |

数据库数据保存在 Docker volume 中，停止容器不会自动删除数据。

### 5. 构建四语言判题镜像

```powershell
docker compose --env-file .env -f deploy/compose.dev.yml build judge
docker image inspect oi-judge:dev --format "{{.Id}}"
```

默认 `JUDGE_BACKEND=docker` 时，后端会按每次提交临时启动隔离容器，只要求存在 `oi-judge:dev` 镜像，不需要常驻启动 `oi-judge` 服务容器。

如果 Debian 软件源访问缓慢，可在 `.env` 中设置可用镜像源后重新构建：

```dotenv
DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
```

如果使用本机 HTTP/HTTPS 代理，也可以给 `docker build` 传入 `HTTP_PROXY`、`HTTPS_PROXY` 构建参数，具体配置见 `deploy/judge.Dockerfile`。

### 6. 创建后端虚拟环境并安装依赖

在项目根目录执行：

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
```

### 7. 初始化数据库并执行迁移

```powershell
backend\.venv\Scripts\python.exe scripts\init_database.py

Push-Location backend
.\.venv\Scripts\alembic.exe upgrade head
Pop-Location
```

`init_database.py` 负责创建 `.env` 中的 MySQL 数据库，Alembic 负责创建和升级表结构。

### 8. 导入题包和演示账号

先检查再导入自建题包：

```powershell
backend\.venv\Scripts\python.exe scripts\import_packages.py --check
backend\.venv\Scripts\python.exe scripts\import_packages.py
```

创建演示账号：

```powershell
backend\.venv\Scripts\python.exe scripts\prepare_demo.py --users-only
```

本地演示账号包括 `student01`、`teacher01`、`admin`，初始密码均为 `123456`。这些账号只用于本地演示，部署到共享环境前必须修改或删除。

如需同时生成演示提交与提示记录，可去掉 `--users-only`。此时后端必须已经运行。

### 9. 启动后端

打开一个 PowerShell 终端：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 下使用 Docker 判题时不要添加 `--reload`。Uvicorn 热重载会切换事件循环，导致 Docker 判题子进程无法启动；修改后端代码后请手动重启进程。

后端启动后访问：

- 健康检查：http://127.0.0.1:8000/health
- API 文档：http://127.0.0.1:8000/docs

### 10. 安装并启动前端

另开一个 PowerShell 终端，在项目根目录执行：

```powershell
cd web
pnpm install --frozen-lockfile
pnpm dev
```

浏览器打开 http://127.0.0.1:5173/。开发服务器会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

### 11. 部署验收

在项目根目录检查后端健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

验证四种语言的真实 Docker 编译与运行：

```powershell
backend\.venv\Scripts\python.exe scripts\docker_runner_smoke.py
```

预期 C11、C++17、Java 17、Python 3 均显示 `status=Accepted`，输出为 `5`。最后在页面依次验证登录、选择题目、切换语言、提交代码、获取提示、修改后重新提交以及刷新后的状态恢复。

## 快速启动已有环境

首次部署完成、依赖和判题镜像已经存在后，可以在项目根目录执行：

```powershell
.\scripts\dev.ps1 -SkipJudge
```

该命令启动 MySQL、Redis，初始化数据库、执行迁移并启动后端。`-SkipJudge` 表示不启动常驻 go-judge 服务；默认的 DockerRunner 仍会使用已经构建好的 `oi-judge:dev` 镜像判题。前端仍需在另一个终端运行 `cd web; pnpm dev`。

如果使用已有的 MySQL、Redis，并且只想启动后端，可执行：

```powershell
.\scripts\dev.ps1 -SkipCompose -SkipJudge
```

## 开发与测试

后端检查：

```powershell
cd backend
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pytest.exe -q
```

前端检查：

```powershell
cd web
pnpm build
```

题包检查：

```powershell
backend\.venv\Scripts\python.exe tools\check_package.py --dir problems
```

设置 `TEST_DATABASE_URL` 后可以执行真实数据库接口回归。测试数据库名称必须包含 `test`，并先对该连接执行 `alembic upgrade head`；不要把测试连接指向业务数据库。设置 `RUN_DOCKER_TESTS=1` 后，可以执行真实 Docker 判题集成测试。

最新验证（2026-11-02）：独立 MySQL 8.4 常规回归 460 项通过，10 项 Docker 条件测试本轮未启用；上一批 451 项包含四语言真实 Docker 判题并全部通过。Ruff、前端 36 项与生产构建已通过；本轮未改前端。答疑能识别已提供的输出差异，并结合当前代码和最近对话继续讲解；详见 `docs/答疑上下文改进验收.md`、`docs/修改核验恢复验收.md`。常规测试关闭模型；另用自建素材验证真实 DeepSeek 讲解调用。

## 常见问题

### Docker 提示找不到 `oi-judge:dev`

说明判题镜像尚未构建。回到项目根目录执行：

```powershell
docker compose --env-file .env -f deploy/compose.dev.yml build judge
```

### 提交代码时出现 `NotImplementedError`

通常是 Windows 下使用了 `uvicorn --reload`。去掉 `--reload`，重新启动后端。

### 数据库连接失败

检查 MySQL 是否健康、3307 端口是否被占用，以及 `MYSQL_PASSWORD` 与 `DATABASE_URL` 是否一致：

```powershell
docker compose --env-file .env -f deploy/compose.dev.yml ps
```

### 前端能打开但接口请求失败

确认后端已监听 8000 端口，并访问 `http://127.0.0.1:8000/health`。如果健康检查中的 `db` 为 `down`，继续排查数据库配置。

### Java 提交编译失败

Java 入口类必须命名为 `Main`，即使用 `public class Main`。前端的 Java 初始模板已经满足此要求。

## 数据、安全与已知限制

- `.env`、私钥、运行目录和构建产物已加入忽略规则；禁止提交真实密钥和个人数据。
- 上传代码前可运行 `backend\.venv\Scripts\python.exe tools\check_secrets.py` 检查 Git 暂存内容；工具只报告路径与类型。
- 自建题包采用 CC BY-SA 4.0；采集题面与页面版权归原平台及出题人，来源与许可边界见 [数据说明](data/README.md)。
- Docker 执行器只用于本地开发和受控演示，尚未完成开放互联网环境的沙箱隔离验收。
- 当前没有完整的生产 Compose、TLS、反向代理、备份恢复和监控方案，因此不应直接作为公网生产服务部署。
- 当前题包规模较小，教师复核写回、完整提交谱系和真实教学效果评估仍待完善。

## 合并后的后台任务与验证

网页使用持久后台任务提交（`Prefer: respond-async`），202 只表示接受任务，不代表通过。API 默认内嵌工作器，也可设置 `JUDGE_WORKER_ENABLED=false` 后单独运行 `python -m app.worker`。本人或管理员可以取消排队/运行任务；成功结果和教师审定不可被旧执行者覆盖。

运行集成测试前，为 `TEST_DATABASE_URL` 配置名称含 `test` 的独立 MySQL 库并执行迁移；并发回归会创建独立测试数据库，测试账号需要相应 CREATE/DROP 权限，业务账号无需这些额外权限。禁止把业务库当测试库；测试设置 `LLM_ENABLED=false`。

`prepare_demo.py` 只准备题包和演示账号，不调用模型或自动提交学生代码。教师复核只在授权班级范围内追加审计。主线此前的 PostgreSQL 验收记录保留为历史材料；本分支的当前运行与 CI 目标统一为 MySQL 8.4，合并验证结果以 `docs/分支冲突解决验收.md` 为准。
