"""从授权训练的执行事实生成学情摘要，自动解释与教师确认分别计数。"""
import hashlib
import json
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Diagnosis, Evidence, JudgeJob, Problem, Submission, Training, User
from app.services.judgement import effective_verdict
from app.services.training_service import training_detail

MECHANISMS = {
    "M01": "类型与溢出", "M02": "数组与内存", "M03": "初始化与未定义行为",
    "M04": "边界与特判", "M05": "循环与终止", "M06": "复杂度",
    "M07": "取模与精度", "M08": "题意理解", "M09": "算法模型",
    "M10": "实现细节", "M11": "工程习惯",
}
CODE_FAILURES = {"CE", "WA", "RE", "TLE", "MLE", "OLE", "PE"}


async def analyze_training(session: AsyncSession, user: User, training: Training) -> dict:
    if user.role not in ("teacher", "admin"):
        raise HTTPException(403, "班级学情仅供教练查看")
    if training.status != "published":
        raise HTTPException(409, "请先发布训练再查看学情")
    detail = await training_detail(session, user, training)
    latest_ids = [cell["id"] for row in detail["rows"] for cell in row["cells"] if cell]
    visible_cells = {cell["id"]: cell for row in detail["rows"] for cell in row["cells"] if cell}
    latest = (await session.execute(
        select(Submission, Diagnosis).outerjoin(Diagnosis, Diagnosis.submission_id == Submission.id)
        .where(Submission.id.in_(latest_ids)).order_by(Submission.id)
    )).all() if latest_ids else []
    evidence = (await session.scalars(select(Evidence).where(
        Evidence.submission_id.in_(latest_ids)).order_by(Evidence.id))).all() if latest_ids else []
    evidence_by_sub = {}
    for item in evidence:
        evidence_by_sub.setdefault(item.submission_id, []).append(item)
    by_id = {sub.id: (sub, diag) for sub, diag in latest}
    problem_sources = {p.id: p for p in (await session.scalars(select(Problem).where(
        Problem.id.in_({sub.problem_id for sub, _ in latest})))).all()} if latest else {}
    members = [row["student_id"] for row in detail["rows"]]
    # 用于描述本训练中的“先前未通过、最新正式通过”，不推断独立完成或掌握度。
    history = (await session.execute(
        select(Submission, JudgeJob).outerjoin(JudgeJob, JudgeJob.submission_id == Submission.id)
        .where(Submission.training_id == training.id, Submission.student_id.in_(members))
    )).all() if members else []
    failed_before = {(sub.student_id, sub.problem_id) for sub, job in history
                     if sub.stage == "READY" and effective_verdict(sub) in CODE_FAILURES
                     and (job is None or job.status == "succeeded")}
    problems, issues, fingerprints = [], {}, []
    for index, problem in enumerate(detail["problems"]):
        counts = Counter()
        repaired = unclassified = 0
        for row in detail["rows"]:
            cell = row["cells"][index]
            if not cell:
                counts["not_submitted"] += 1
                continue
            state = cell["verdict"] or "UNVERIFIED" if cell["state"] == "complete" else cell["state"]
            counts[state] += 1
            sub, diag = by_id[cell["id"]]
            if cell["passed"] and (sub.student_id, sub.problem_id) in failed_before:
                repaired += 1
            refs = {e.id for e in evidence_by_sub.get(sub.id, [])}
            classified = False
            if cell["state"] == "complete" and cell["verdict"] in CODE_FAILURES and diag:
                for claim in diag.mechanism or []:
                    if not isinstance(claim, dict):
                        continue
                    code, cited = claim.get("code"), claim.get("evidence_ids")
                    if code not in MECHANISMS or not isinstance(cited, list) or not cited:
                        continue
                    if any(type(ref) is not int or ref not in refs for ref in cited):
                        continue
                    confirmed = (diag.status == "confirmed" and diag.reviewer_id is not None
                                 and (diag.model_meta or {}).get("review_decision") in ("confirm", "revise")
                                 and set(cited).issubset(set((diag.model_meta or {}).get("review_evidence_ids") or [])))
                    source = "teacher" if confirmed else "automatic"
                    key = (problem["pid"], code, source)
                    group = issues.setdefault(key, {"pid": problem["pid"], "code": code,
                        "label": MECHANISMS[code], "source": source, "students": set(), "submission_ids": set()})
                    group["students"].add(row["student_id"])
                    group["submission_ids"].add(sub.id)
                    classified = True
            if cell["state"] == "complete" and cell["verdict"] in CODE_FAILURES and not classified:
                unclassified += 1
            fingerprints.append({"cell": cell, "code_hash": sub.code_hash,
                "diagnosis": {"status": diag.status, "revision": diag.review_revision,
                    "mechanism": diag.mechanism, "narrative": diag.narrative,
                    "reviewer_id": diag.reviewer_id, "meta": diag.model_meta} if diag else None,
                "evidence": [{"id": e.id, "type": e.type, "payload": e.payload} for e in evidence_by_sub.get(sub.id, [])]})
        problems.append({**problem, "students": len(detail["rows"]), "counts": dict(counts),
                         "repaired": repaired, "unclassified": unclassified})
    groups = [{**{k: v for k, v in group.items() if k not in ("students", "submission_ids")},
               "students": len(group["students"]), "submission_ids": sorted(group["submission_ids"])}
              for group in issues.values()]
    groups.sort(key=lambda group: (-group["students"], group["pid"], group["code"], group["source"]))
    examples = []
    for sub, diag in sorted(latest, key=lambda pair: (effective_verdict(pair[0]) == "AC", pair[0].id)):
        if visible_cells[sub.id]["state"] != "complete":
            continue
        # 不触发异步懒加载；按完成矩阵找到题号。
        problem = next((detail["problems"][i] for row in detail["rows"] for i, cell in enumerate(row["cells"])
                        if cell and cell["id"] == sub.id), None)
        if problem is None:
            continue
        records = evidence_by_sub.get(sub.id, [])[:6]
        examples.append({"submission_id": sub.id, "pid": problem["pid"], "language": sub.lang,
            "problem_context": problem_sources[sub.problem_id].statement_md[:1400],
            "verdict": effective_verdict(sub), "code": sub.code[:1600], "code_truncated": len(sub.code) > 1600,
            "narrative": (diag.narrative if diag else "")[:500],
            "evidence": [{"id": e.id, "type": e.type, "excerpt": json.dumps(e.payload, ensure_ascii=False)[:900]} for e in records]})
        if len(examples) >= 6:
            break
    basis = {"training": {"id": training.id, "title": training.title, "objective": training.objective},
             "members": members, "problems": problems, "issues": groups, "records": fingerprints,
             "failed_before": sorted(failed_before)}
    digest = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {"summary": detail["summary"], "problems": problems, "issues": groups, "examples": examples,
            "basis_digest": digest, "observed_at": detail["observed_at"]}
