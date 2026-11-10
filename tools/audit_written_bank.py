"""只读审计笔试候选数据；不联网、不导入数据库、不批准题目发布。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root):
        raise ValueError("文件路径必须位于数据包内")
    return path


def load_rows(root: Path, name: str) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (root / name).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{name} 必须逐行包含 JSON 对象")
    return rows


def audit(root: Path) -> dict:
    root = root.resolve(strict=True)
    questions = load_rows(root, "questions.jsonl")
    groups = load_rows(root, "question_groups.jsonl")
    papers = load_rows(root, "papers.jsonl")
    sources = load_rows(root, "sources.jsonl")
    nodes = json.loads((root / "knowledge_points.json").read_text(encoding="utf-8-sig"))["nodes"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    issues: list[dict] = []

    def issue(code: str, ids: list[str], *, severity: str = "error") -> None:
        issues.append({"code": code, "severity": severity, "ids": ids})

    def index(rows: list[dict], kind: str) -> dict:
        counts = Counter(row["id"] for row in rows)
        for key, count in counts.items():
            if count > 1:
                issue("duplicate_id", [kind, key])
        return {row["id"]: row for row in rows}

    qm, gm, pm, sm, nm = (
        index(rows, kind)
        for rows, kind in [
            (questions, "question"),
            (groups, "group"),
            (papers, "paper"),
            (sources, "source"),
            (nodes, "knowledge"),
        ]
    )
    checked_files = 0
    for record in [*manifest["files"], *manifest["assets"]]:
        try:
            path = local_file(root, record["path"])
            data = path.read_bytes()
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                issue("manifest_mismatch", [record["path"]])
            checked_files += 1
        except (ValueError, OSError):
            issue("manifest_file_unavailable", [record["path"]])

    def assets(row: dict) -> None:
        for name in row.get("asset_paths", []):
            try:
                if not local_file(root, name).is_file():
                    issue("asset_missing", [row["id"], name])
            except ValueError:
                issue("asset_path_escape", [row["id"], name])

    shared_material_differences: dict[str, str] = {}
    for q in questions:
        qid = q["id"]
        if q.get("paper_id") not in pm:
            issue("paper_missing", [qid])
        if q.get("group_id"):
            g = gm.get(q["group_id"])
            if (
                not g
                or g.get("paper_id") != q.get("paper_id")
                or qid not in g.get("subquestion_ids", [])
            ):
                issue("group_membership_mismatch", [qid])
            elif q.get("shared_stem") and q["shared_stem"] != g.get("shared_stem"):
                shared_material_differences.setdefault(g["id"], qid)
        for nid in [
            q.get("primary_knowledge_point_id"),
            *q.get("secondary_knowledge_point_ids", []),
        ]:
            if nid and nid not in nm:
                issue("knowledge_missing", [qid, nid])
        for sid in [q.get("answer_source_id"), *q.get("source_ids", [])]:
            if sid and sid not in sm:
                issue("source_missing", [qid, sid])
        answer = q.get("answer") or {}
        kind = q.get("question_type")
        if kind in {"single_choice", "multiple_choice"}:
            options = [o["id"] for o in q.get("options") or []]
            chosen = answer.get("option_ids", [])
            expected_kind = "option" if kind == "single_choice" else "options"
            if (
                answer.get("kind") != expected_kind
                or not chosen
                or len(chosen) != len(set(chosen))
                or not set(chosen).issubset(options)
                or len(options) != len(set(options))
                or (kind == "single_choice" and len(chosen) != 1)
            ):
                issue("invalid_choice_answer", [qid])
        elif kind == "true_false":
            if answer.get("kind") != "boolean" or type(answer.get("value")) is not bool:
                issue("invalid_boolean_answer", [qid])
        elif kind == "fill_blank":
            accepted = answer.get("accepted")
            if (
                answer.get("kind") != "text"
                or not isinstance(accepted, list)
                or not accepted
                or any(not isinstance(v, str) or not v.strip() for v in accepted)
            ):
                issue("invalid_text_answer", [qid])
        else:
            issue("unsupported_question_type", [qid])
        c = qm.get(q.get("canonical_question_id"))
        relation = q.get("relation_to_canonical")
        if not c or c.get("canonical_question_id") != c["id"]:
            issue("canonical_reference_invalid", [qid])
        elif relation == "exact_duplicate":
            if q.get("question_type") != c.get("question_type"):
                issue("exact_duplicate_type_conflict", [qid, c["id"]], severity="warning")
            if q.get("options") != c.get("options"):
                issue("exact_duplicate_options_require_review", [qid, c["id"]], severity="warning")
            if q.get("group_id") != c.get("group_id"):
                issue("exact_duplicate_context_requires_review", [qid, c["id"]], severity="warning")
        elif relation == "numeric_variant" and c["id"] != qid:
            issue("variant_collapsed_in_canonical_count", [qid, c["id"]], severity="warning")
        if q.get("answer_status") == "conflict" and q.get("eligible_for_practice"):
            issue("conflicting_answer_marked_eligible", [qid])
        if "原卷中被删除" in q.get("stem", ""):
            issue("withdrawn_question_requires_review", [qid], severity="warning")
        assets(q)

    for g in groups:
        if g.get("paper_id") not in pm or not g.get("shared_stem", "").strip():
            issue("group_material_or_paper_missing", [g["id"]])
        children = g.get("subquestion_ids", [])
        if not children or len(children) != len(set(children)):
            issue("invalid_group_children", [g["id"]])
        for qid in children:
            if qid not in qm or qm[qid].get("group_id") != g["id"]:
                issue("child_membership_mismatch", [g["id"], qid])
        assets(g)
    for gid, qid in shared_material_differences.items():
        # 题组可能保留整段子题，而子题副本仅保留公共前缀；不能据此断言代码损坏。
        issue("shared_material_copies_differ", [gid, qid], severity="warning")

    def visit(nid: str, edge: str, state: dict[str, int]) -> None:
        if state.get(nid) == 1:
            issue("knowledge_cycle", [edge, nid])
            return
        if state.get(nid) == 2:
            return
        state[nid] = 1
        refs = nm[nid].get(edge)
        for ref in refs if isinstance(refs, list) else ([refs] if refs else []):
            if ref not in nm:
                issue("knowledge_reference_missing", [nid, ref])
            else:
                visit(ref, edge, state)
        state[nid] = 2

    for field in ["parent_id", "prerequisites"]:
        state: dict[str, int] = {}
        for nid in nm:
            visit(nid, field, state)

    eligible = [q for q in questions if q.get("eligible_for_practice") is True]
    leaves = [n for n in nodes if n.get("is_leaf")]
    coverage = {}
    for node in leaves:
        linked = [
            q
            for q in eligible
            if node["id"]
            in [q.get("primary_knowledge_point_id"), *q.get("secondary_knowledge_point_ids", [])]
        ]
        coverage[node["id"]] = {
            "name": node["name"],
            "count": len({q["canonical_question_id"] for q in linked}),
            "rows": len(linked),
        }
    with (root / "coverage.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    if Counter(r["knowledge_point_id"] for r in csv_rows) != Counter(coverage.keys()):
        issue("coverage_node_set_mismatch", [])
    for row in csv_rows:
        nid = row["knowledge_point_id"]
        if nid in coverage and int(row["independent_question_count"]) != coverage[nid]["count"]:
            issue("coverage_count_mismatch", [nid])

    counts = {
        "papers": len(papers),
        "groups": len(groups),
        "questions": len(questions),
        "sources": len(sources),
        "knowledge_nodes": len(nodes),
        "knowledge_leaves": len(leaves),
        "manifest_files_checked": checked_files,
        "eligible_candidate_rows": len(eligible),
        "canonical_count_as_supplied": len({q["canonical_question_id"] for q in eligible}),
        "candidate_rows_excluding_declared_exact_duplicates": sum(
            q.get("relation_to_canonical") != "exact_duplicate" for q in eligible
        ),
        "answer_status": dict(Counter(q.get("answer_status") for q in questions)),
        "review_status": dict(Counter(q.get("review_status") for q in questions)),
        "eligible_unknown_scoring_rules": sum(
            q.get("scoring_rule") == "multi_select_rule_unknown" for q in eligible
        ),
        "questions_without_explanation": sum(not q.get("explanation") for q in questions),
    }
    stats = json.loads((root / "stats.json").read_text(encoding="utf-8-sig"))
    for key, actual in [
        ("papers", len(papers)),
        ("groups", len(groups)),
        ("question_rows", len(questions)),
        ("independent_questions", counts["canonical_count_as_supplied"]),
        ("eligible_rows", len(eligible)),
        ("by_answer_status", counts["answer_status"]),
    ]:
        if stats.get(key) != actual:
            issue("stats_mismatch", [key])
    return {
        "scope": "offline_candidate_audit_not_publication_approval",
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "counts": counts,
        "zero_coverage_leaves": [
            {"id": nid, "name": info["name"]}
            for nid, info in coverage.items()
            if info["count"] == 0
        ],
        "issue_counts": dict(Counter(i["code"] for i in issues)),
        "errors": sum(i["severity"] == "error" for i in issues),
        "warnings": sum(i["severity"] == "warning" for i in issues),
        "issues": issues,
        "limitations": [
            "不核验外部来源真伪或答案语义正确性",
            "不批准任何题目进入正式判分",
            "覆盖复算沿用原 canonical 编号，不证明去重语义正确",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.dir.resolve(strict=True)
        if args.report and args.report.resolve().is_relative_to(root):
            raise ValueError("报告必须写在数据包外，避免覆盖原始文件")
        report = audit(root)
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        print(
            json.dumps({"errors": report["errors"], "warnings": report["warnings"]}),
            file=sys.stderr,
        )
        return 1 if report["errors"] else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"数据包无法审计：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
