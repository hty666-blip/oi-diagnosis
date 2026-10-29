from app.capabilities.triage import apply_autofix, triage

GOOD = """#include <cstdio>
int main(){ int a,b; scanf("%d%d",&a,&b); printf("%d\\n",a+b); return 0; }
"""

FULLWIDTH = """#include <iostream>
using namespace std；
int main（）｛
    return 0；
｝
"""

CE_SEMICOLON = "main.cpp:3:15: error: expected ';' before '}' token\n"


def test_p0_fullwidth_routes_fast_fix():
    r = triage(FULLWIDTH)
    assert r.route == "fast_fix"
    assert r.worst_p == "P0"
    assert r.autofixable is True
    assert r.findings[0].line == 2


def test_p1_syntax_routes_syntax_help():
    r = triage(GOOD, compile_stderr=CE_SEMICOLON, verdict="CE")
    assert r.route == "syntax_help"
    assert r.worst_p == "P1"
    assert r.findings[0].code == "P1-MISSING-SEMICOLON"


def test_p0_wins_over_p1():
    r = triage(FULLWIDTH, compile_stderr=CE_SEMICOLON, verdict="CE")
    assert r.worst_p == "P0"
    assert r.route == "fast_fix"
    # P0 必须排在前面
    assert r.findings[0].p == "P0"


def test_compiles_but_wrong_goes_full_pipeline():
    r = triage(GOOD, verdict="WA")
    assert r.route == "full_pipeline"
    assert r.findings == []


def test_ac_routes_none():
    r = triage(GOOD, verdict="AC")
    assert r.route == "none"


def test_unmatched_ce_goes_human():
    r = triage(GOOD, compile_stderr="main.cpp:1:1: error: weird new message\n", verdict="CE")
    assert r.route == "human"
    assert r.notes


def test_findings_are_capped_and_deduped():
    many = FULLWIDTH * 3
    r = triage(many)
    assert len(r.findings) <= 8
    keys = [(f.code, f.line, f.col) for f in r.findings]
    assert len(keys) == len(set(keys))


def test_autofix_keeps_chinese_inside_strings():
    code = '''#include <cstdio>
int main(){
    printf("结果是：%d\\n", 1)；
    return 0;
}
'''
    fixed, n = apply_autofix(code)
    assert n >= 1
    assert "；" not in fixed.split("printf")[1].split("\n")[-1]
    assert "结果是：" in fixed          # 字符串里的中文冒号原样保留
    assert 'printf("结果是：%d\\n", 1);' in fixed


def test_summary_mentions_line_and_hint():
    r = triage(FULLWIDTH)
    assert "第 2 行" in r.summary
    assert "英文半角" in r.summary

