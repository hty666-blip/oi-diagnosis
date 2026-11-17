"""生成可由管理员导入的四道原创初赛练习。"""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PAPER = "oi-original-tree-practice"
SOURCE = "oi-original-written-demo"
KNOWLEDGE = "OI-DEMO-BINARY-TREE"


def questions():
    shared = {
        "paper_id": PAPER,
        "source_ids": [SOURCE],
        "answer_source_id": SOURCE,
        "source_kind": "original",
        "answer_status": "provided_unverified",
        "primary_knowledge_point_id": KNOWLEDGE,
        "score": 2,
        "scoring_rule": "all_or_nothing",
    }
    return [
        {
            **shared,
            "id": "oi-tree-01",
            "level": "J",
            "question_type": "single_choice",
            "stem": "【自编】一棵二叉树的根在第 1 层，最深的节点在第 4 层。这棵树最多有多少个节点？",
            "options": [
                {"id": "A", "text": "4"},
                {"id": "B", "text": "8"},
                {"id": "C", "text": "15"},
                {"id": "D", "text": "16"},
            ],
            "answer": {"kind": "option", "option_ids": ["C"]},
            "explanation": "第 1 至 4 层最多分别有 1、2、4、8 个节点。各层都达到上限时共有 15 个节点。",
        },
        {
            **shared,
            "id": "oi-tree-02",
            "level": "J",
            "question_type": "multiple_choice",
            "stem": "【自编】一棵二叉树有 5 个节点。关于它的结构，下列哪些说法一定正确？",
            "options": [
                {"id": "A", "text": "共有 4 条父子边。"},
                {"id": "B", "text": "根一定有两个孩子。"},
                {"id": "C", "text": "至少有一个叶节点。"},
                {"id": "D", "text": "共有 3 个叶节点。"},
            ],
            "answer": {"kind": "options", "option_ids": ["A", "C"]},
            "explanation": "每个非根节点贡献一条父子边，共 4 条。最深的节点是叶节点。把 5 个节点排成一条链即可排除 B 和 D。",
        },
        {
            **shared,
            "id": "oi-tree-03",
            "level": "J",
            "question_type": "true_false",
            "stem": "【自编】若一棵二叉树有 9 个节点，那么从根到最深节点至少经过 4 条边。",
            "answer": {"kind": "boolean", "value": False},
            "explanation": "根所在层到第 4 层相隔 3 条边，四层最多容纳 15 个节点。前 3 层放满后再在第 4 层放 2 个节点即可构成反例。",
        },
        {
            **shared,
            "id": "oi-tree-04",
            "level": "S",
            "question_type": "fill_blank",
            "stem": "【自编】一棵非空二叉树中，恰有两个孩子的节点有 7 个，恰有一个孩子的节点有 4 个。没有孩子的节点有多少个？请填整数。",
            "scoring_rule": "exact_text",
            "answer": {
                "kind": "text",
                "accepted": ["8"],
                "normalization": "trim_outer_whitespace",
            },
            "explanation": "从父节点数边，边数是 2×7+4=18，所以共有 19 个节点。减去 7 个双孩子节点和 4 个单孩子节点，得到 8 个叶节点。",
        },
    ]


def json_line(value):
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")


def build(path: Path):
    files = {
        "questions.jsonl": b"".join(map(json_line, questions())),
        "question_groups.jsonl": b"",
        "papers.jsonl": json_line(
            {"id": PAPER, "title": "二叉树自编练习", "source_ids": [SOURCE]}
        ),
        "sources.jsonl": json_line(
            {
                "id": SOURCE,
                "title": "本项目原创练习",
                "license": "可用于本项目练习与演示",
            }
        ),
        "knowledge_points.json": json_line(
            {"nodes": [{"id": KNOWLEDGE, "name": "二叉树节点与边", "is_leaf": True}]}
        ),
    }
    files["manifest.json"] = json_line(
        {
            "files": [
                {
                    "path": name,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in files.items()
            ],
        }
    )
    with ZipFile(path, "x") as archive:
        for name, content in files.items():
            entry = ZipInfo(name)
            archive.writestr(entry, content, compress_type=ZIP_DEFLATED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="尚不存在的 ZIP 输出路径")
    options = parser.parse_args()
    build(options.output)
    print(f"已生成 {options.output}，请在初赛题库导入并审核。")
