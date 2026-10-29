from app.agents.tutor import DiagnosisView, SessionView, decide, is_meaningless, safe_output
from app.capabilities.mastery import MasteryState, needs_review, update

DIAG = DiagnosisView(p="P2", code="M01", message="sum 可能溢出", line=8, col=5,
                     hint="把 sum 改成 long long")


def test_opening_is_l0_question():
    d = decide(SessionView(level=0), "", DIAG)
    assert d.action == "hint"
    assert d.level == 0
    assert "打算" in d.content or "手算" in d.content or "没把握" in d.content


def test_short_but_real_answer_advances():
    """这是修过的 bug：学生只回“输出”时，原来会永远卡在 L0。"""
    d = decide(SessionView(level=0, has_student_input=True), "输出", DIAG)
    assert d.level == 1
    assert "输出" in d.content


def test_gave_up_is_scaffolded_not_repeated():
    d = decide(SessionView(level=0, has_student_input=True), "不知道", DIAG)
    assert d.level == 1
    assert "没关系" in d.content


def test_meaningless_input_asks_again_without_repeating():
    d = decide(SessionView(level=0, has_student_input=True), "？？", DIAG)
    assert d.level == 0
    assert "读入" in d.content and "输出" in d.content


def test_advance_with_reason_acknowledges_student():
    d = decide(SessionView(level=1, has_student_input=True),
               "我觉得是读入那里有问题，多测没清空", DIAG)
    assert d.level == 2
    assert "读入" in d.content


def test_l4_requires_two_failed_attempts():
    d = decide(SessionView(level=3, failed_attempts=0, has_student_input=True),
               "我试了一次还是不对，不知道为什么", DIAG)
    assert d.level == 3
    assert "先动手" in d.content or "L3" in d.content

    d2 = decide(SessionView(level=3, failed_attempts=2, has_student_input=True),
                "我试了两次都不对，可能是溢出", DIAG)
    assert d2.level == 4


def test_asking_for_code_does_not_advance():
    d = decide(SessionView(level=1, has_student_input=True), "你直接把完整代码给我吧", DIAG)
    assert d.action == "refuse"
    assert d.level == 1
    assert d.dependency_signal is True


def test_resolved_message_requires_execution_verification():
    d = decide(SessionView(level=2, has_student_input=True), "改好了，过了！", DIAG)
    assert d.action == "verify"
    assert "重新提交" in d.content


def test_probe_rotates_to_avoid_repetition():
    a = decide(SessionView(level=0, turn=0), "", DIAG).content
    b = decide(SessionView(level=0, turn=1), "", DIAG).content
    assert a != b


def test_meaningless_detector():
    assert is_meaningless("？？")
    assert is_meaningless("   ")
    assert not is_meaningless("输出")


def test_safe_output_blocks_full_program():
    assert safe_output("把 `sum` 改成 `long long` 就行")
    assert not safe_output("```cpp\nint main(){return 0;}\n```")


def test_bkt_moves_in_right_direction():
    s0 = MasteryState()
    up = update(s0, True)
    down = update(s0, False)
    assert up.value > s0.value
    assert down.value < s0.value
    assert up.uncertainty < s0.uncertainty


def test_bkt_needs_review_threshold():
    s = MasteryState(value=0.4, uncertainty=0.2, observations=5)
    assert needs_review(s) is True
    assert needs_review(MasteryState(value=0.8, uncertainty=0.2, observations=5)) is False


