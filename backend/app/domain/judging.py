"""判题与诊断共享的输出比较语义。"""


def outputs_equal(actual: str, expected: str) -> bool:
    def lines(text: str) -> list[str]:
        result = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
        while result and not result[-1]:
            result.pop()
        return result

    return lines(actual) == lines(expected)
