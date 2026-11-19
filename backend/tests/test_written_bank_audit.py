import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def auditor():
    path = Path(__file__).resolve().parents[2] / "tools/audit_written_bank.py"
    spec = importlib.util.spec_from_file_location("written_bank_auditor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def question(qid="q1"):
    return {
        "id": qid,
        "paper_id": "p1",
        "group_id": None,
        "stem": "1 + 1 = ?",
        "question_type": "single_choice",
        "options": [{"id": "A", "text": "2"}, {"id": "B", "text": "3"}],
        "answer": {"kind": "option", "option_ids": ["A"]},
        "primary_knowledge_point_id": "k1",
        "secondary_knowledge_point_ids": [],
        "source_ids": ["s1"],
        "answer_source_id": "s1",
        "canonical_question_id": qid,
        "relation_to_canonical": "canonical",
        "review_status": "needs_review",
        "answer_status": "provided_unverified",
        "eligible_for_practice": True,
        "scoring_rule": "all_or_nothing",
    }


def bank(tmp_path, questions=None, nodes=None, groups=None):
    root = tmp_path / "bank"
    root.mkdir()
    questions = questions or [question()]
    nodes = nodes or [{"id": "k1", "name": "加法", "is_leaf": True, "prerequisites": []}]
    values = {
        "questions.jsonl": questions,
        "papers.jsonl": [{"id": "p1"}],
        "question_groups.jsonl": groups or [],
        "sources.jsonl": [{"id": "s1"}],
    }
    for name, rows in values.items():
        (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (root / "knowledge_points.json").write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    count = len({q["canonical_question_id"] for q in questions})
    (root / "coverage.csv").write_text(
        f"knowledge_point_id,independent_question_count\nk1,{count}\n", encoding="utf-8"
    )
    stats = {
        "papers": 1,
        "groups": len(groups or []),
        "question_rows": len(questions),
        "independent_questions": count,
        "eligible_rows": len(questions),
        "by_answer_status": {"provided_unverified": len(questions)},
    }
    (root / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    files = [
        {
            "path": p.name,
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in root.iterdir()
    ]
    (root / "manifest.json").write_text(
        json.dumps({"files": files, "assets": []}), encoding="utf-8"
    )
    return root


def test_candidate_audit_is_read_only_and_does_not_approve(auditor, tmp_path):
    root = bank(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = auditor.audit(root)
    assert result["errors"] == result["warnings"] == 0
    assert result["counts"]["review_status"] == {"needs_review": 1}
    assert result["scope"] == "offline_candidate_audit_not_publication_approval"
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def test_changed_question_type_and_options_cannot_be_trusted_as_exact_duplicate(auditor, tmp_path):
    original = question()
    variant = deepcopy(original)
    variant.update(
        id="q2", relation_to_canonical="exact_duplicate", question_type="multiple_choice"
    )
    variant["options"][1]["text"] = "4"
    variant["answer"] = {"kind": "options", "option_ids": ["A"]}
    result = auditor.audit(bank(tmp_path, [original, variant]))
    assert result["issue_counts"]["exact_duplicate_type_conflict"] == 1
    assert result["issue_counts"]["exact_duplicate_options_require_review"] == 1


def test_numeric_variants_are_reported_separately_from_exact_duplicates(auditor, tmp_path):
    original = question()
    variant = deepcopy(original)
    variant.update(id="q2", stem="2 + 2 = ?", relation_to_canonical="numeric_variant")
    result = auditor.audit(bank(tmp_path, [original, variant]))
    assert result["counts"]["canonical_count_as_supplied"] == 1
    assert result["counts"]["candidate_rows_excluding_declared_exact_duplicates"] == 2
    assert result["issue_counts"]["variant_collapsed_in_canonical_count"] == 1


def test_manifest_detects_changes_after_collection(auditor, tmp_path):
    root = bank(tmp_path)
    with (root / "questions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    result = auditor.audit(root)
    assert result["issue_counts"]["manifest_mismatch"] == 1


def test_asset_path_escape_is_rejected(auditor, tmp_path):
    q = question()
    q["asset_paths"] = ["../private.txt"]
    (tmp_path / "private.txt").write_text("unrelated", encoding="utf-8")
    result = auditor.audit(bank(tmp_path, [q]))
    assert result["issue_counts"]["asset_path_escape"] == 1


def test_invalid_choice_and_cross_group_membership_are_detected(auditor, tmp_path):
    q = question()
    q.update(group_id="g1")
    q["answer"]["option_ids"] = ["Z"]
    groups = [{"id": "g1", "paper_id": "p1", "shared_stem": "材料", "subquestion_ids": ["other"]}]
    result = auditor.audit(bank(tmp_path, [q], groups=groups))
    assert result["issue_counts"]["invalid_choice_answer"] == 1
    assert result["issue_counts"]["group_membership_mismatch"] == 1
    assert result["issue_counts"]["child_membership_mismatch"] == 1


def test_knowledge_cycles_and_coverage_tampering_are_detected(auditor, tmp_path):
    nodes = [{"id": "k1", "name": "加法", "is_leaf": True, "prerequisites": ["k1"]}]
    root = bank(tmp_path, nodes=nodes)
    (root / "coverage.csv").write_text(
        "knowledge_point_id,independent_question_count\nk1,99\n", encoding="utf-8"
    )
    result = auditor.audit(root)
    assert result["issue_counts"]["knowledge_cycle"] == 1
    assert result["issue_counts"]["coverage_count_mismatch"] == 1


def test_report_cannot_overwrite_input(auditor, tmp_path):
    root = bank(tmp_path)
    target = root / "questions.jsonl"
    before = target.read_bytes()
    with pytest.raises(SystemExit) as raised:
        auditor.main(["--dir", str(root), "--report", str(target)])
    assert raised.value.code == 2
    assert target.read_bytes() == before


def test_group_can_include_subquestions_without_claiming_code_corruption(auditor, tmp_path):
    q = question()
    q.update(group_id="g1", shared_stem="公共材料")
    groups = [
        {
            "id": "g1",
            "paper_id": "p1",
            "shared_stem": "公共材料\n子题正文",
            "subquestion_ids": ["q1"],
        }
    ]
    result = auditor.audit(bank(tmp_path, [q], groups=groups))
    assert result["errors"] == 0
    assert result["issue_counts"]["shared_material_copies_differ"] == 1


def test_withdrawal_claim_is_flagged_without_silently_removing_the_question(auditor, tmp_path):
    q = question()
    q["stem"] = "（本题在原卷中被删除）题目正文"
    root = bank(tmp_path, [q])
    before = (root / "questions.jsonl").read_bytes()
    result = auditor.audit(root)
    assert result["issue_counts"]["withdrawn_question_requires_review"] == 1
    assert result["counts"]["eligible_candidate_rows"] == 1
    assert (root / "questions.jsonl").read_bytes() == before
