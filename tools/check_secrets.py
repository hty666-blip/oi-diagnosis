"""检查 Git 索引中的密钥；默认检查本次修改，--all 检查全部跟踪文件。"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
                   rb"github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def env_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            result[key] = value.strip().strip("\"'")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="检查全部索引文件，适用于 CI")
    args = parser.parse_args(argv)
    sample = env_values(ROOT / ".env.example")
    local = env_values(ROOT / ".env")
    private_values = [value.encode() for key, value in local.items()
                      if re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.I)
                      and len(value) >= 8 and value != sample.get(key)]
    command = ["git", "ls-files", "-z"] if args.all else [
        "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z",
    ]
    paths = subprocess.check_output(command, cwd=ROOT)
    problems = []
    files = [part.decode("utf-8") for part in paths.split(b"\0") if part]
    for path in files:
        name = Path(path).name
        if ((name == ".env" or name.startswith(".env.") or name.endswith(".env"))
                and name != ".env.example"):
            problems.append((path, "真实环境配置文件"))
            continue
        data = subprocess.check_output(["git", "show", f":{path}"], cwd=ROOT)
        if TOKEN.search(data):
            problems.append((path, "疑似访问密钥或私钥"))
        if any(secret in data for secret in private_values):
            problems.append((path, "包含本地环境中的私密值"))
    for path, kind in problems:
        print(f"BLOCKED: {path} ({kind})")
    if problems:
        return 1
    print(f"检查通过：{len(files)} 个索引文件，未发现环境文件或匹配的密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
