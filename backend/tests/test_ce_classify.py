from app.capabilities.ce_classify import classify_compile, classify_runtime, summarize

CE_FULLWIDTH = """\
main.cpp:2:20: error: stray '\\357' in program
    2 | using namespace std；
      |                    ^
"""

CE_SEMICOLON = """\
main.cpp:5:15: error: expected ';' before '}' token
    5 |     return 0
      |               ^
"""

CE_UNDECLARED = """\
main.cpp:6:5: error: 'cout' was not declared in this scope
"""

CE_NO_MAIN = """\
/usr/bin/ld: /tmp/cc1.o: in function `__static_initialization_and_destruction_0(int, int)':
main.cpp:(.text+0x1a): undefined reference to `main'
collect2: error: ld returned 1 exit status
"""

CE_UNINIT = """\
main.cpp:8:9: warning: 'sum' may be used uninitialized [-Wmaybe-uninitialized]
"""


def test_fullwidth_is_p0():
    r = classify_compile(CE_FULLWIDTH)
    assert r.findings, "应识别出 stray 字符"
    f = r.findings[0]
    assert f.code == "P0-FULLWIDTH"
    assert f.nature == "P0"
    assert (f.line, f.col) == (2, 20)
    assert "中文标点" in f.message


def test_missing_semicolon():
    r = classify_compile(CE_SEMICOLON)
    assert r.findings[0].code == "P1-MISSING-SEMICOLON"
    assert r.findings[0].line == 5


def test_undeclared():
    r = classify_compile(CE_UNDECLARED)
    assert r.findings[0].code == "P1-UNDECLARED"


def test_no_main():
    r = classify_compile(CE_NO_MAIN)
    assert "P1-NO-MAIN" in r.codes


def test_warning_uninitialized():
    r = classify_compile(CE_UNINIT)
    assert r.findings[0].code == "M03-UNINIT"
    assert r.findings[0].nature == "P2"


def test_unknown_lines_are_collected():
    r = classify_compile("main.cpp:1:1: error: some brand new compiler message\n")
    assert r.findings == []
    assert r.unparsed


def test_dedup_same_code_same_line():
    dup = CE_UNDECLARED * 3
    r = classify_compile(dup)
    assert len(r.findings) == 1


def test_runtime_asan_overflow():
    r = classify_runtime(
        "==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000\n", "RE")
    assert r.findings[0].code == "M02-OUT-OF-BOUNDS"
    assert r.findings[0].confidence > 0.9


def test_runtime_tle():
    r = classify_runtime("", "TLE")
    assert r.findings[0].code == "M06-COMPLEXITY"
    assert r.findings[0].nature == "P3"


def test_summarize_only_top():
    r = classify_compile(CE_SEMICOLON + CE_UNDECLARED)
    s = summarize(r)
    assert "分号" in s
