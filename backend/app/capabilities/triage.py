"""Triage 分诊：在花大钱之前，先判断"这错有多深、该走哪条路"。

设计（见 docs/错因体系与Triage.md）：
1. P0 表层错误 → 字符扫描，100% 确定性，不调模型
2. P1 语法错误 → 编译日志规则映射
3. 都命中不了 → 代码能编译，才值得进全流水线

输出是**清单 + 路由**，不是单一标签；一次编译可能有 10 个错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.capabilities.ce_classify import classify_compile
from app.capabilities.surface_lint import scan as scan_surface

P_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5}


@dataclass
class TriageFinding:
    p: str
    code: str
    message: str
    hint: str
    line: int | None = None
    col: int | None = None
    autofix: str | None = None
    confidence: float = 0.9

    def as_dict(self) -> dict:
        return {
            "p": self.p, "code": self.code, "line": self.line, "col": self.col,
            "message": self.message, "hint": self.hint,
            "autofix": self.autofix, "confidence": self.confidence,
        }


@dataclass
class TriageResult:
    route: str                       # fast_fix | syntax_help | full_pipeline | none | human
    findings: list[TriageFinding] = field(default_factory=list)
    summary: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def top(self) -> TriageFinding | None:
        return self.findings[0] if self.findings else None

    @property
    def worst_p(self) -> str | None:
        return self.top.p if self.top else None

    @property
    def autofixable(self) -> bool:
        return any(f.autofix is not None for f in self.findings)

    def as_dict(self) -> dict:
        return {
            "route": self.route,
            "worst_p": self.worst_p,
            "autofixable": self.autofixable,
            "summary": self.summary,
            "findings": [f.as_dict() for f in self.findings],
            "notes": self.notes,
        }


def _dedupe(findings: list[TriageFinding]) -> list[TriageFinding]:
    seen: set[tuple] = set()
    out: list[TriageFinding] = []
    for f in findings:
        key = (f.code, f.line, f.col)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def triage(code: str, *, compile_stderr: str | None = None,
           verdict: str | None = None) -> TriageResult:
    """输入源码（可选编译日志与 verdict），输出分诊结论与路由。"""
    findings: list[TriageFinding] = []
    notes: list[str] = []

    # 1) P0：字符级，确定性
    for f in scan_surface(code):
        findings.append(TriageFinding(
            p="P0",
            code=f.code,
            message=f.message,
            hint=f.suggestion,
            line=f.line,
            col=f.col,
            autofix=f.autofix,
            confidence=1.0,
        ))

    # 2) P1/P2：编译日志映射
    if compile_stderr:
        for c in classify_compile(compile_stderr).findings:
            findings.append(TriageFinding(
                p=c.nature, code=c.code, message=c.message, hint=c.hint,
                line=c.line, col=c.col, confidence=c.confidence,
            ))
        if not findings and str(compile_stderr).strip():
            notes.append("编译报错未能匹配已知规则，建议转人工或交给模型兜底")

    findings = sorted(_dedupe(findings), key=lambda f: (P_ORDER.get(f.p, 9), f.line or 0))
    findings = findings[:8]

    # 3) 路由
    has_p0 = any(f.p == "P0" for f in findings)
    has_p1 = any(f.p == "P1" for f in findings)

    if has_p0:
        route = "fast_fix"          # 先修表层，修完再看还有没有别的错
    elif has_p1:
        route = "syntax_help"
    elif verdict in ("WA", "TLE", "MLE", "RE", "OLE"):
        route = "full_pipeline"     # 能编译但结果不对，才值得取证
    elif verdict == "AC":
        route = "none"
    elif compile_stderr:
        route = "human"
    else:
        route = "full_pipeline"

    return TriageResult(route=route, findings=findings,
                        summary=_summarize(findings, route), notes=notes)


def _summarize(findings: list[TriageFinding], route: str) -> str:
    if not findings:
        return {
            "none": "样例全部通过，暂无问题。",
            "full_pipeline": "代码能编译，但结果不对，正在进一步取证。",
            "human": "没识别出具体错误类型，已记录给老师。",
        }.get(route, "")

    top = findings[0]
    loc = f"第 {top.line} 行" + (f" 第 {top.col} 列" if top.col else "") if top.line else ""
    more = f"（共 {len(findings)} 处，先修这个）" if len(findings) > 1 else ""
    return f"{loc}：{top.message}{more} 建议：{top.hint}".strip()


def apply_autofix(code: str) -> tuple[str, int]:
    """把代码区的全角符号换成半角，返回 (新代码, 替换数)。字符串/注释里的不动。"""
    chars = list(code)
    count = 0
    for f in scan_surface(code):
        if f.autofix is None:
            continue
        idx = _index_of(chars, f.line, f.col)
        if idx is not None and chars[idx] == f.char:
            chars[idx] = f.autofix
            count += 1
    return "".join(chars), count


def _index_of(chars: list[str], line: int, col: int) -> int | None:
    cur_line, cur_col = 1, 1
    for i, ch in enumerate(chars):
        if cur_line == line and cur_col == col:
            return i
        if ch == "\n":
            cur_line += 1
            cur_col = 1
        else:
            cur_col += 1
    return None
