"""把历史判题结果按实际测试范围解释，避免旧版样例 AC 被当成整题通过。"""

from app.domain.models import Submission


def valid_test_summary(submission: Submission) -> dict | None:
    """只将真实整数计数作为执行摘要，不把 bool 或异常 JSON 当测试数量。"""
    counts = submission.tests_passed
    if not isinstance(counts, dict):
        return None
    total, passed = counts.get("total"), counts.get("passed")
    if type(total) is not int or type(passed) is not int or not 0 <= passed <= total:
        return None
    return counts


def effective_verdict(submission: Submission) -> str | None:
    if submission.verdict != "AC":
        return submission.verdict
    summary = valid_test_summary(submission)
    if summary is None and submission.tests_passed:
        return "UNVERIFIED"
    counts = summary or {}
    total = counts.get("total", 0)
    if not isinstance(total, int) or total <= 0:
        return "NO_TESTS"
    if counts.get("passed") != total:
        return "UNVERIFIED"
    if counts.get("scope") == "package":
        return "AC"
    return "SAMPLE_PASS"


def result_narrative(submission: Submission, narrative: str) -> str:
    """旧结果按实际范围降级时，同步纠正展示文案，原诊断和审计不改写。"""
    verdict = effective_verdict(submission)
    legacy_success = narrative.strip() in {"样例全部通过，暂无问题。", "样例全部通过，暂无问题"}
    if submission.verdict == "AC" or legacy_success:
        if verdict == "UNVERIFIED":
            return "历史测试计数不完整或不一致，无法确认完整通过；请重新提交验证。"
        if verdict == "SAMPLE_PASS":
            return "公开样例已通过，尚未验证正式题包，不能确认程序正确。"
        if verdict == "NO_TESTS":
            return "没有可执行测试数据，历史结果尚未验证。"
    return narrative
