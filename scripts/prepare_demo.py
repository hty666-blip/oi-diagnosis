"""只准备本地演示账号和自建题包，不调用模型或自动提交学生代码。"""

from __future__ import annotations

import asyncio

from import_packages import ROOT, import_packages, read_packages
from seed_demo import ensure_users, require_demo_environment


async def prepare() -> None:
    require_demo_environment()
    records = read_packages(ROOT / "problems")
    await ensure_users()
    await import_packages(records)
    print(f"演示环境已准备：{len(records)} 道自建题，学生与教师账号可登录。")
    print("teacher01 对应初一 1 班；账号密码以本地演示登录页为准。")


if __name__ == "__main__":
    asyncio.run(prepare())
