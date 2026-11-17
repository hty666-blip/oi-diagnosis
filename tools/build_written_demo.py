"""把仓库原创初赛演示源稿打成可预览、暂存和逐题审核的 ZIP。"""

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/written/demo_bank.json"
SOURCE_ID = "oi-written-demo-original"


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_archive() -> bytes:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    papers = [
        {**paper, "source_ids": [SOURCE_ID]} for paper in data["papers"]
    ]
    paper_ids = {paper["level"]: paper["id"] for paper in papers}
    questions = [
        {
            **question,
            "stem": "【自编】" + question["stem"],
            "paper_id": paper_ids[question["level"]],
            "score": 2,
            "scoring_rule": "exact_text" if question["question_type"] == "fill_blank" else "all_or_nothing",
            "source_ids": [SOURCE_ID],
            "answer_source_id": SOURCE_ID,
            "source_kind": "original_demo",
            "source_locator": f"examples/written/demo_bank.json · {question['id']}",
            "answer_status": "provided_unverified",
            "review_status": "needs_review",
        }
        for question in data["questions"]
    ]
    sources = [{
        "id": SOURCE_ID,
        "title": "本项目原创初赛演示题与参考解答",
        "publisher": "OI 错题诊断项目",
        "source_type": "original_demo",
        "license": "随本仓库发布的原创教学演示内容",
    }]
    records = {"questions": questions, "question_groups": data["question_groups"], "papers": papers, "sources": sources}
    files = {
        f"{kind}.jsonl": b"".join(json_bytes(row) + b"\n" for row in rows)
        for kind, rows in records.items()
    }
    files["knowledge_points.json"] = json_bytes(data["knowledge_points"])
    files["manifest.json"] = json_bytes({
        "files": [
            {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in files.items()
        ],
        "assets": [],
    })
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(ZipInfo(path), content, compress_type=ZIP_DEFLATED)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/private/oi-written-demo.zip")
    arguments = parser.parse_args()

    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.written_bank_package import parse_package

    content = build_archive()
    _, summary, _ = parse_package(content)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(content)
    print(f"{summary['questions']} 道自编题，{summary['knowledge_leaves']} 个知识点，已生成 {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
