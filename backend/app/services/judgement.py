"""把历史判题结果按实际测试范围解释，避免旧版样例 AC 被当成整题通过。"""

from app.domain.models import Submission


def effective_verdict(submission: Submission) -> str | None:
    if submission.verdict != "AC":
        return submission.verdict
    counts = submission.tests_passed or {}
    total = counts.get("total", 0)
    if not isinstance(total, int) or total <= 0:
        return "NO_TESTS"
    if counts.get("scope") == "package" and counts.get("passed") == total:
        return "AC"
    return "SAMPLE_PASS"


def result_narrative(submission: Submission, narrative: str) -> str:
    """旧结果按实际范围降级时，同步纠正展示文案，原诊断和审计不改写。"""
    verdict = effective_verdict(submission)
    legacy_success = narrative.strip() in {"样例全部通过，暂无问题。", "样例全部通过，暂无问题"}
    if submission.verdict == "AC" or legacy_success:
        if verdict == "SAMPLE_PASS":
            return "公开样例已通过，尚未验证正式题包，不能确认程序正确。"
        if verdict == "NO_TESTS":
            return "没有可执行测试数据，历史结果尚未验证。"
    return narrative
