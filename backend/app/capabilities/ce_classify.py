"""编译器/运行期报错 -> 学生能懂的话（规则优先，不经模型）。

设计原则（错因体系 v2）：
- 每条规则给出 (错因码, 性质 P, 解释, 建议)
- 解析 `file.cpp:12:5:` 前缀，尽量给出精确行列
- 匹配不到时返回空列表，交给上层决定是否降级给模型
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LOC_PREFIX = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+):(?P<col>\d+):\s*(?:fatal\s+)?(?:error|warning):\s*(?P<msg>.*)$")
LOC_SIMPLE = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+):\s*(?P<msg>.*)$")


@dataclass
class Finding:
    code: str            # M01 / P0-FULLWIDTH / P1-SYNTAX ...
    nature: str          # P0 表层 / P1 语法 / P2 实现 / P3 算法 / P4 题意 / P5 策略
    message: str         # 学生看的解释
    hint: str            # 怎么办
    raw: str = ""
    line: int | None = None
    col: int | None = None
    confidence: float = 0.9


@dataclass
class ClassifyResult:
    findings: list[Finding] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def top(self) -> Finding | None:
        return self.findings[0] if self.findings else None

    @property
    def codes(self) -> list[str]:
        out: list[str] = []
        for f in self.findings:
            if f.code not in out:
                out.append(f.code)
        return out


# (正则, 错因码, 性质, 解释, 建议, 置信度)
RULES: list[tuple[str, str, str, str, str, float]] = [
    # ---- P0：非 ASCII 字符（中文标点 / BOM）----
    (r"stray '\\\d+' in program",
     "P0-FULLWIDTH", "P0",
     "代码里出现了编译器不认识的非 ASCII 字符，最常见的是**中文标点**（；，（）“”）或文件开头的 BOM。",
     "检查这一行附近的全角符号，改成英文半角；可以直接跑一遍 surface_lint 自动修复。",
     1.0),
    (r"extended character .* is not valid in an identifier",
     "P0-CJK-IDENT", "P0",
     "标识符里出现了中文或全角字符。",
     "变量名/函数名请改成英文（输入法记得切回半角）。",
     0.95),

    # ---- P1：语法规则 ----
    (r"expected '[,;]' or ';' before|expected ';' (before|at end of)",
     "P1-MISSING-SEMICOLON", "P1",
     "少了一个分号（多半在**报错位置的上一行末尾**）。",
     "看报错行的上一行结尾，补上英文 `;` 而不是 `；`。",
     0.95),
    (r"expected '\}' at end of input",
     "P1-UNBALANCED-BRACE", "P1",
     "花括号 `{}` 没有配对，少了一个 `}`。",
     "从 `main` 开始逐层数括号，或用编辑器的括号匹配高亮。",
     0.95),
    (r"expected '\)' before",
     "P1-UNBALANCED-PAREN", "P1",
     "圆括号 `()` 没有配对。",
     "检查函数调用以及 `if/while/for` 的条件括号。",
     0.9),
    (r"'(\w+)' was not declared in this scope",
     "P1-UNDECLARED", "P1",
     "用了没声明过的名字（常见：拼错、忘了定义、忘了 `#include`）。",
     "检查拼写；用 `cin/cout` 要 `#include <iostream>` 并写 `using namespace std;`。",
     0.9),
    (r"'(\w+)' does not name a type",
     "P1-UNKNOWN-TYPE", "P1",
     "这里应该是类型名，但编译器不认识它。",
     "检查是否漏了 `#include`，或把变量写在了类型的位置。",
     0.9),
    (r"no matching function for call to",
     "P1-SIGNATURE", "P1",
     "调用的函数参数对不上（个数或类型不匹配）。",
     "对照函数定义检查实参类型；注意 `int` 与 `long long`、数组传参的区别。",
     0.85),
    (r"invalid conversion from '(.+)' to '(.+)'",
     "P1-TYPE-CONV", "P1",
     "类型转换不合法（例如把字符串当成整数）。",
     "检查变量类型，以及 `scanf/printf` 的格式符是否与变量匹配。",
     0.85),
    (r"redefinition of '.*'|redeclaration of '.*'",
     "P1-REDEFINITION", "P1",
     "同一个名字被定义了两次。",
     "改名或删除重复定义；注意 `main` 只能有一个。",
     0.95),
    (r"'(.*)' file not found|No such file or directory",
     "P1-MISSING-HEADER", "P1",
     "找不到这个头文件（`#include` 写错了，或文件名拼错）。",
     "C++ 标准头文件不加 `.h`，例如 `<iostream>`、`<cstdio>`。",
     0.95),
    (r"undefined reference to `main'",
     "P1-NO-MAIN", "P1",
     "找不到 `main` 函数（拼成了 `mian`？或写成了 `Main`）。",
     "C++ 程序的入口必须是小写的 `int main()`。",
     0.98),
    (r"control reaches end of non-void function",
     "P1-MISSING-RETURN", "P1",
     "函数声明了有返回值，但没有在所有分支上都 `return`。",
     "检查每个分支的结尾是否都有 `return`。",
     0.85),
    (r"empty character constant",
     "P1-EMPTY-CHAR", "P1",
     "字符常量是空的（`''`），常见于中文引号或漏写字符。",
     "检查是不是用了中文引号 `‘’`，要写成英文单引号 `'a'`。",
     0.95),

    (r"missing terminating . character",
     "P1-UNTERMINATED-STRING", "P1",
     "字符串/字符常量没有正确闭合（少了一个引号）。",
     "检查这一行的引号是否配对；注意中文引号“ ”不是英文引号。",
     0.95),
    (r"unterminated comment",
     "P1-UNTERMINATED-COMMENT", "P1",
     "块注释 /* 没有闭合。",
     "补上对应的 */。",
     0.95),
    (r"'else' without a previous 'if'",
     "P1-ELSE-WITHOUT-IF", "P1",
     "出现了孤立的 else：多半是前面的 if 分支里多写了分号或括号没配对。",
     "检查 if/else 的括号结构，if 后面不要直接跟分号。",
     0.9),
    (r"lvalue required as left operand of assignment",
     "P1-LVALUE", "P1",
     "等号左边不是一个可以被赋值的变量（可能是常量或表达式）。",
     "检查赋值语句左边；想比较相等要用 ==，不是 =。",
     0.9),
    (r"invalid operands of types",
     "P1-OPERANDS", "P1",
     "运算符两边的类型不匹配（例如把字符串和整数相加）。",
     "检查参与运算的两个变量的类型。",
     0.85),
    (r"expected primary-expression before",
     "P1-PRIMARY-EXPR", "P1",
     "这里需要一个表达式，但语法不完整（常见于多写的运算符或括号）。",
     "看报错位置前一个符号，多半是多余的 , 或运算符。",
     0.8),
    (r"too few arguments to function",
     "P1-TOO-FEW-ARGS", "P1",
     "函数调用时参数个数不够。",
     "对照函数定义补齐参数。",
     0.9),
    (r"'main' must return 'int'",
     "P1-MAIN-RETURN", "P1",
     "main 函数的返回值必须是 int。",
     "写成 `int main()`，结尾 `return 0;`。",
     0.98),
    (r"format '.*' expects argument of type",
     "M08-FORMAT", "P2",
     "printf/scanf 的格式符与变量类型不匹配（例如用 %d 读 long long）。",
     "`long long` 用 `%lld`，`double` 用 `%lf` 读、`%f` 输出。",
     0.9),
    (r"comparison is always (true|false)",
     "M04-ALWAYS-COMPARE", "P2",
     "这个比较永远为真/假，条件写错了。",
     "检查边界值或变量类型（例如无符号数与 0 比较）。",
     0.75),
    (r"statement has no effect",
     "M11-NO-EFFECT", "P2",
     "这行语句没有任何作用（常见于多写的比较或赋值）。",
     "检查是否把 `==` 写成了 `=`，或多了个无意义表达式。",
     0.7),

    # ---- P2：能编译但实现有隐患（-Wall 提示）----
    (r"may be used uninitialized",
     "M03-UNINIT", "P2",
     "变量可能没初始化就被使用了。",
     "定义时直接赋初值，例如 `int sum = 0;`。",
     0.8),
    (r"comparison of integer expressions of different signedness",
     "M01-SIGNEDNESS", "P2",
     "有符号与无符号整数混着比较，负数会变成很大的正数。",
     "统一类型；`size()` 返回的是无符号数，比较时注意。",
     0.75),
    (r"overflow in conversion from .* to .* changes value",
     "M01-OVERFLOW-CONST", "P2",
     "常量太大，塞不进这个类型。",
     "把变量或常量改成 `long long`。",
     0.9),
    (r"integer overflow in expression",
     "M01-OVERFLOW", "P2",
     "表达式在编译期就溢出了。",
     "在运算前把操作数转成 `long long`（例如 `1LL * a * b`）。",
     0.9),
    (r"array subscript .* is above array bounds",
     "M02-OUT-OF-BOUNDS", "P2",
     "数组下标超出范围。",
     "检查数组开的大小与下标上限；从 1 开始用就要多开一个。",
     0.9),
    (r"division by zero",
     "M04-DIV-ZERO", "P2",
     "除数为 0（编译期能看出来）。",
     "检查除法/取模的除数是否可能为 0。",
     0.95),
]

# 运行期：verdict -> 可能原因
RUNTIME_RULES: list[tuple[str, str, str, str, str, float]] = [
    (r"AddressSanitizer:? ?(heap-buffer-overflow|stack-buffer-overflow|global-buffer-overflow)",
     "M02-OUT-OF-BOUNDS", "P2",
     "数组越界访问（地址检查器抓到）。",
     "看报告里的行号，检查该数组的大小与下标。",
     0.98),
    (r"AddressSanitizer:? ?SEGV|SEGV on unknown address",
     "M03-WILD-POINTER", "P2",
     "访问了非法内存（野指针/空指针/越界）。",
     "检查指针是否初始化、数组下标是否越界。",
     0.95),
    (r"runtime error: signed integer overflow",
     "M01-OVERFLOW-RUNTIME", "P2",
     "整数运算溢出（未定义行为）。",
     "把相关变量改成 `long long`。",
     0.98),
    (r"runtime error: .*index .* out of bounds",
     "M02-OUT-OF-BOUNDS", "P2",
     "数组下标越界。",
     "核对数组维度与循环边界。",
     0.98),
    (r"stack-overflow|stack overflow",
     "M05-STACK-OVERFLOW", "P2",
     "递归太深或局部数组太大，爆栈。",
     "改小局部数组（放到全局），或改递归为递推。",
     0.95),
    (r"division by zero|divide by zero|Floating point exception",
     "M04-DIV-ZERO", "P2",
     "除零错误。",
     "检查除数是否可能为 0。",
     0.95),
]


def _parse_location(line: str) -> tuple[int | None, int | None, str]:
    m = LOC_PREFIX.match(line)
    if m:
        return int(m.group("line")), int(m.group("col")), m.group("msg")
    m = LOC_SIMPLE.match(line)
    if m:
        return int(m.group("line")), None, m.group("msg")
    return None, None, line


def classify_compile(stderr: str, limit: int = 8) -> ClassifyResult:
    """把 g++ 的 stderr 归类成结构化结论。"""
    result = ClassifyResult()
    if not stderr:
        return result

    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    KEYS = ("error:", "warning:", "fatal error:", "undefined reference", "collect2:")
    for ln in lines:
        if not any(k in ln for k in KEYS):
            continue
        lineno, col, msg = _parse_location(ln)
        matched = False
        for pattern, code, nature, message, hint, conf in RULES:
            if re.search(pattern, msg, re.I):
                result.findings.append(Finding(
                    code=code, nature=nature, message=message, hint=hint,
                    raw=ln, line=lineno, col=col, confidence=conf,
                ))
                matched = True
                break
        if not matched:
            result.unparsed.append(ln)
        if len(result.findings) >= limit:
            break

    # 去重（同一码同一行只留一条）
    seen = set()
    unique: list[Finding] = []
    for f in result.findings:
        key = (f.code, f.line, f.col)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    result.findings = unique
    return result


def classify_runtime(stderr: str, verdict: str = "RE", limit: int = 4) -> ClassifyResult:
    result = ClassifyResult()
    if verdict == "TLE":
        result.findings.append(Finding(
            code="M06-COMPLEXITY", nature="P3",
            message="程序超时：算法复杂度可能高于题目要求，或存在死循环。",
            hint="先估算 n 的量级与允许的操作次数（约 1e8 次/秒）。",
            confidence=0.7,
        ))
        return result
    if verdict == "MLE":
        result.findings.append(Finding(
            code="M06-MEMORY", nature="P2",
            message="程序超出内存限制：数组开太大，或申请了过多空间。",
            hint="按题目数据范围重新估算内存；`vector` 不要无脑开 1e7。",
            confidence=0.8,
        ))
        return result

    for ln in (stderr or "").splitlines():
        for pattern, code, nature, message, hint, conf in RUNTIME_RULES:
            if re.search(pattern, ln, re.I):
                result.findings.append(Finding(
                    code=code, nature=nature, message=message, hint=hint,
                    raw=ln.strip(), confidence=conf,
                ))
                break
        if len(result.findings) >= limit:
            break
    return result


def summarize(result: ClassifyResult) -> str:
    """给前端/学生的一句话总结：只讲最靠前的一条。"""
    top = result.top
    if top is None:
        return "没有识别出具体错误类型。"
    loc = f"第 {top.line} 行" + (f" 第 {top.col} 列" if top.col else "") if top.line else ""
    return f"{loc}：{top.message} {top.hint}".strip()
