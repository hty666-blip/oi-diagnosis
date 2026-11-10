"""有界、纯内存读取候选 ZIP，不解压到磁盘，不抓取远端资源。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
from collections import Counter
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

MAX_UPLOAD = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
REQUIRED = {
    "questions.jsonl",
    "question_groups.jsonl",
    "papers.jsonl",
    "sources.jsonl",
    "knowledge_points.json",
    "manifest.json",
}


class PackageError(ValueError):
    pass


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or any(p in {"..", "."} for p in value.split("/"))
        or len(value) > 240
    ):
        raise PackageError("题包包含无效文件路径")
    return str(path)


def image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise PackageError("仅支持 PNG、JPEG、GIF 或 WebP 图片资源")


def read_zip(content: bytes) -> dict[str, bytes]:
    if not content or len(content) > MAX_UPLOAD:
        raise PackageError("ZIP 题包不得为空或超过 16 MB")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 512 or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise PackageError("题包文件数量或解压后体积超出限制")
            names = set()
            for entry in entries:
                if entry.orig_filename != entry.filename or "\x00" in entry.orig_filename:
                    raise PackageError("题包文件名包含非规范分隔符或控制字符")
                name = safe_path(entry.filename.rstrip("/"))
                if name in names or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise PackageError("题包含重复文件、链接或加密文件")
                names.add(name)
                if entry.file_size > MAX_FILE:
                    raise PackageError("题包单个文件不得超过 12 MB")
            roots = [
                e.filename.removesuffix("questions.jsonl")
                for e in entries
                if PurePosixPath(e.filename).name == "questions.jsonl"
            ]
            if len(roots) != 1:
                raise PackageError("题包必须且只能包含一份 questions.jsonl")
            prefix = roots[0]
            files = {
                e.filename[len(prefix) :]: archive.read(e)
                for e in entries
                if not e.is_dir() and e.filename.startswith(prefix)
            }
            if not REQUIRED.issubset(files):
                raise PackageError("题包缺少题目、题组、试卷、来源、知识目录或清单文件")
            return files
    except (BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise PackageError("ZIP 无法读取或内容校验失败") from exc


def parse_json(raw: bytes):
    try:
        # NaN/Infinity 不属于可持久化的 JSON 数据。
        return json.loads(
            raw.decode("utf-8-sig"),
            parse_constant=lambda _: (_ for _ in ()).throw(PackageError("JSON 含非法数值")),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PackageError("题包必须使用有效的 UTF-8 JSON") from exc


def indexed(rows, label: str, limit: int) -> dict[str, dict]:
    if not isinstance(rows, list) or len(rows) > limit:
        raise PackageError(f"{label}记录数量或格式无效")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not ID.fullmatch(row["id"])
            or row["id"] in result
        ):
            raise PackageError(f"{label}编号无效或重复")
        result[row["id"]] = row
    return result


def identifiers(value, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(v, str) or not ID.fullmatch(v) for v in value
    ):
        raise PackageError(f"{label}引用格式无效")
    return value


def question_review(q: dict, nodes: dict) -> dict:
    flags = set(q.get("issues") or [])
    if q.get("answer_status") not in {"official_cross_checked", "reference_cross_checked"}:
        flags.add(
            "answer_conflict" if q.get("answer_status") == "conflict" else "answer_unverified"
        )
    if q.get("question_type") == "multiple_choice" and q.get("scoring_rule") not in {
        "all_or_nothing",
        "explicit_partial_credit",
    }:
        flags.add("scoring_rule_unknown")
    relation = q.get("relation_to_canonical")
    if q.get("canonical_question_id") not in {None, q["id"]}:
        flags.add("deduplication_requires_review")
    if "原卷中被删除" in q["stem"]:
        flags.add("withdrawn_question_requires_review")
    suggestions = []
    current = [q.get("primary_knowledge_point_id"), *q.get("secondary_knowledge_point_ids", [])]
    for phrase, nid in [("空间复杂度", "KP-08-02-02"), ("时间复杂度", "KP-08-02-01")]:
        if phrase in q["stem"] and nid in nodes and nid not in current:
            suggestions.append({"id": nid, "name": nodes[nid].get("name", nid), "basis": phrase})
    if re.search(r"函数的作用|行的作用|语句的作用", q["stem"]):
        nid = "KP-08-03-02"
        if nid in nodes and nid not in current:
            suggestions.append(
                {"id": nid, "name": nodes[nid].get("name", nid), "basis": "题干明确询问代码作用"}
            )
    if suggestions:
        flags.add("knowledge_tag_suggestion")
    return {
        "independent_id": q["id"],
        "related_question_id": q.get("canonical_question_id")
        if q.get("canonical_question_id") != q["id"]
        else None,
        "source_relation": relation,
        "review_status": "needs_review",
        "exam_origin": q.get("source_kind"),
        "acquisition_source_ids": q.get("source_ids", []),
        "knowledge_suggestions": suggestions,
        "issues": sorted(flags),
    }


def parse_package(content: bytes) -> tuple[dict, dict, dict[str, tuple[bytes, str]]]:
    files = read_zip(content)
    manifest = parse_json(files["manifest.json"])
    if not isinstance(manifest, dict):
        raise PackageError("清单格式无效")
    declared = manifest.get("files", []) + manifest.get("assets", [])
    declared_names = set()
    for item in declared:
        if not isinstance(item, dict):
            raise PackageError("清单记录无效")
        name = safe_path(item.get("path", ""))
        if name in declared_names or name not in files:
            raise PackageError("清单含重复或不存在的文件")
        declared_names.add(name)
        value = files[name]
        if (
            item.get("bytes") != len(value)
            or item.get("sha256") != hashlib.sha256(value).hexdigest()
        ):
            raise PackageError("文件与清单大小或摘要不一致，请重新打包")
    if not (REQUIRED - {"manifest.json"}).issubset(declared_names):
        raise PackageError("核心文件必须在清单中登记")
    payload = {}
    indexes = {}
    for key, limit in [
        ("questions", 10000),
        ("question_groups", 3000),
        ("papers", 500),
        ("sources", 1000),
    ]:
        rows = [parse_json(line) for line in files[f"{key}.jsonl"].splitlines() if line.strip()]
        indexes[key] = indexed(rows, key, limit)
        payload[key] = rows
    if not payload["questions"] or not payload["papers"] or not payload["sources"]:
        raise PackageError("题目、试卷和来源不能为空")
    knowledge = parse_json(files["knowledge_points.json"])
    nodes = indexed(knowledge.get("nodes") if isinstance(knowledge, dict) else None, "知识点", 1000)
    payload["knowledge_points"] = knowledge
    papers, groups, sources = indexes["papers"], indexes["question_groups"], indexes["sources"]
    questions = indexes["questions"]
    for node in nodes.values():
        if not isinstance(node.get("name"), str) or not node["name"].strip():
            raise PackageError("知识点名称不能为空")
        refs = identifiers(node.get("prerequisites", []), "先修知识")
        if node.get("parent_id"):
            refs = [*refs, node["parent_id"]]
        if any(ref not in nodes for ref in refs):
            raise PackageError("知识点关系引用不存在")

    def visit(nid, field, visited, active):
        if nid in active:
            raise PackageError("知识关系不能成环")
        if nid in visited:
            return
        active.add(nid)
        refs = nodes[nid].get(field)
        for ref in refs if isinstance(refs, list) else ([refs] if refs else []):
            visit(ref, field, visited, active)
        active.remove(nid)
        visited.add(nid)

    for edge in ["parent_id", "prerequisites"]:
        visited, active = set(), set()
        for nid in nodes:
            visit(nid, edge, visited, active)
    assets = {}

    def check_assets(row):
        paths = row.get("asset_paths", [])
        if not isinstance(paths, list):
            raise PackageError("图片引用必须是数组")
        for path in paths:
            if not isinstance(path, str):
                raise PackageError("图片路径无效")
            path = safe_path(path)
            if not path.startswith("assets/") or path not in files or path not in declared_names:
                raise PackageError("题目引用的图片缺失或未在清单登记")
            if len(files[path]) > 4 * 1024 * 1024:
                raise PackageError("单张图片不得超过 4 MB")
            assets[path] = (files[path], image_type(files[path]))

    for paper in papers.values():
        if any(s not in sources for s in identifiers(paper.get("source_ids", []), "试卷来源")):
            raise PackageError("试卷来源不存在")
    for group in groups.values():
        if (
            group.get("paper_id") not in papers
            or not isinstance(group.get("shared_stem"), str)
            or not group["shared_stem"].strip()
        ):
            raise PackageError("题组试卷或共用材料无效")
        children = identifiers(group.get("subquestion_ids"), "题组子题")
        if (
            not children
            or len(children) != len(set(children))
            or any(
                qid not in questions or questions[qid].get("group_id") != group["id"]
                for qid in children
            )
        ):
            raise PackageError("题组与子题关系不一致")
        check_assets(group)
    reviews = {}
    for q in questions.values():
        if (
            q.get("paper_id") not in papers
            or q.get("level") not in {None, "J", "S"}
            or not isinstance(q.get("stem"), str)
            or not q["stem"].strip()
        ):
            raise PackageError("题目试卷、级别或题干无效")
        if q.get("group_id"):
            g = groups.get(q["group_id"])
            if not g or g["paper_id"] != q["paper_id"] or q["id"] not in g["subquestion_ids"]:
                raise PackageError("题目与题组归属不一致")
        refs = identifiers(q.get("secondary_knowledge_point_ids", []), "次知识点")
        if q.get("primary_knowledge_point_id"):
            refs = [*refs, q["primary_knowledge_point_id"]]
        if any(nid not in nodes for nid in refs):
            raise PackageError("题目知识点引用不存在")
        refs = identifiers(q.get("source_ids", []), "题目来源")
        if q.get("answer_source_id"):
            refs = [*refs, q["answer_source_id"]]
        if any(sid not in sources for sid in refs):
            raise PackageError("题目或答案来源不存在")
        if q.get("canonical_question_id") and q["canonical_question_id"] not in questions:
            raise PackageError("去重关联题目不存在")
        if not isinstance(q.get("issues", []), list) or any(
            not isinstance(v, str) or len(v) > 100 for v in q.get("issues", [])
        ):
            raise PackageError("题目问题标记格式无效")
        answer = q.get("answer")
        if not isinstance(answer, dict):
            raise PackageError("题目答案结构无效")
        kind = q.get("question_type")
        if kind in {"single_choice", "multiple_choice"}:
            options = q.get("options")
            if (
                not isinstance(options, list)
                or not 2 <= len(options) <= 26
                or any(
                    not isinstance(o, dict)
                    or not isinstance(o.get("id"), str)
                    or not isinstance(o.get("text"), str)
                    for o in options
                )
            ):
                raise PackageError("选择题选项无效")
            chosen = identifiers(answer.get("option_ids"), "答案选项")
            ids = [o["id"] for o in options]
            if (
                answer.get("kind") != ("option" if kind == "single_choice" else "options")
                or not chosen
                or len(chosen) != len(set(chosen))
                or len(ids) != len(set(ids))
                or not set(chosen).issubset(ids)
                or (kind == "single_choice" and len(chosen) != 1)
            ):
                raise PackageError("选择题答案引用无效")
        elif kind == "true_false":
            if answer.get("kind") != "boolean" or type(answer.get("value")) is not bool:
                raise PackageError("判断题答案必须是布尔值")
        elif kind == "fill_blank":
            accepted = answer.get("accepted")
            if (
                answer.get("kind") != "text"
                or not isinstance(accepted, list)
                or not accepted
                or any(not isinstance(v, str) or not v.strip() for v in accepted)
            ):
                raise PackageError("填空题答案格式无效")
        else:
            raise PackageError("本批仅支持单选、多选、判断和填空候选题")
        check_assets(q)
        reviews[q["id"]] = question_review(q, nodes)
    payload["reviews"] = reviews
    flag_counts = Counter(flag for row in reviews.values() for flag in row["issues"])
    summary = {
        "questions": len(questions),
        "papers": len(papers),
        "groups": len(groups),
        "assets": len(assets),
        "knowledge_nodes": len(nodes),
        "knowledge_leaves": sum(n.get("is_leaf") is True for n in nodes.values()),
        "source_canonical_count": len(
            {q.get("canonical_question_id", q["id"]) for q in questions.values()}
        ),
        "candidate_count": len(questions),
        "published_count": 0,
        "flags": dict(flag_counts),
        "levels": dict(Counter(q.get("level") or "unclassified" for q in questions.values())),
    }
    return payload, summary, assets
