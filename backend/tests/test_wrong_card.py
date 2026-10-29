from app.services.wrong_card import CardInput, render_card


def test_card_contains_core_sections():
    md = render_card(CardInput(
        pid="P1001", title="A+B Problem", verdict="CE",
        narrative="第 2 行第 20 列：中文分号",
        nature=[{"p": "P0", "code": "P0-FULLWIDTH", "line": 2, "col": 20,
                 "message": "中文/全角符号", "hint": "改成英文半角"}],
        code_lines=["#include <cstdio>", "int main(){", "  int a;", "  return 0;", "}"],
    ))
    assert "# 错题卡" in md
    assert "P0-FULLWIDTH" in md
    assert "三天后自测" in md
    assert "完整题解不在卡内" in md


def test_card_snippet_marks_focus_line():
    md = render_card(CardInput(
        pid="P1001", title="t", verdict="WA",
        nature=[{"p": "P2", "code": "M01", "line": 3, "message": "溢出"}],
        code_lines=["a", "b", "c", "d", "e"],
    ))
    assert ">   3 | c" in md


def test_card_without_focus_omits_snippet():
    md = render_card(CardInput(pid="P1", title="t", verdict="WA",
                               nature=[{"p": "P3", "code": "M06", "message": "复杂度"}]))
    assert "当时写的是" not in md


def test_card_includes_mastery_and_next():
    md = render_card(CardInput(
        pid="P1", title="t", verdict="WA",
        mastery={"node": "前缀和", "value": 0.42, "uncertainty": 0.21},
        next_action="做 2 道同型题",
    ))
    assert "前缀和 = 0.42" in md
    assert "做 2 道同型题" in md
