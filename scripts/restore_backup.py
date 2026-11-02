"""恢复到显式指定的专用空库；核对通过后由部署人员切换业务连接。"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.backup_service import BackupError, BackupIO


async def main():
    parser = argparse.ArgumentParser(description="检查加密备份，或恢复到同一 MySQL 的独立空库（库名须含 _restore_）。")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--sha256", required=True, help="管理台或备份清单中的完整 SHA-256")
    parser.add_argument("--execute", action="store_true", help="实际导入；默认仅检查文件与目标配置")
    args = parser.parse_args()
    io = BackupIO(directory=args.archive.parent, key_path=args.key)
    try:
        target = os.environ.get("RESTORE_DATABASE_URL", "")
        io.verify_url(target)
        payload = await asyncio.to_thread(io.read, args.archive, args.sha256)
        if args.execute:
            result = await io.restore(payload, target, keep=True)
            print(f"恢复及内容比对通过，共 {sum(result['counts'].values())} 条。旧登录已撤销，AI 已关闭。")
            print("目标库已保留。核对用户、题目及训练后，再由部署人员切换 DATABASE_URL。")
        else:
            print("备份完整性、解密及版本检查通过。实际导入需加 --execute，届时会再次确认目标为空库。")
    except BackupError as exc:
        print(f"恢复未完成：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        # 不输出连接串、数据库驱动异常或备份原文。
        print(f"恢复未完成：{type(exc).__name__}，请核对空库、权限及文件。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
