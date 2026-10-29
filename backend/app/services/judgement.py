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
