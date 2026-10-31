#!/usr/bin/env python
"""DockerRunner 冒烟测试：依次完成 C11、C++17、Java 17、Python 3 编译与运行。"""

from __future__ import annotations

import asyncio

from app.capabilities.docker_runner import DockerRunner

CASES = {
    "cpp17": """#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a + b << "\\n"; }
""",
    "c11": """#include <stdio.h>
int main(void) { long long a, b; scanf("%lld%lld", &a, &b); printf("%lld\\n", a + b); }
""",
    "java17": """import java.util.*;
public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        System.out.println(in.nextLong() + in.nextLong());
    }
}
""",
    "python3": "a, b = map(int, input().split())\nprint(a + b)\n",
}


async def main() -> int:
    failed = False
    for language, code in CASES.items():
        runner = DockerRunner(language=language)
        compiled, result = await runner.compile_and_run(code, "2 3\n")
        if not compiled.ok or result is None:
            print(f"{language}: compile failed: {compiled.stderr}")
            failed = True
            continue
        print(
            f"{language}: status={result.status} exit={result.exit_code} "
            f"stdout={result.stdout!r}"
        )
        failed = failed or result.status != "Accepted" or result.stdout.strip() != "5"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
