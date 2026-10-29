# 信息学竞赛错题诊断与答疑系统

把一次代码提交转成可检查的编译与运行证据，再引导学生修改、重新提交并验证结果。

本轮以核心可靠性为先，需求与验收以 [改进需求基线](docs/改进需求基线.md) 为准。其他设计、实验和论文文档保留为历史材料，其中的目标指标不代表真实教学效果。

## 当前实现

- FastAPI 后端、React/TypeScript 前端及 PostgreSQL 持久化。
- C++17 编译运行、表层检查、分诊和证据保存；模型解释作为可选辅助。
- 公开样例通过标记为 `SAMPLE_PASS`，无测试标记为 `NO_TESTS`；只有当前正式题包测试全部通过才为 `AC`。
- 已接入两个自建题包，共 6 个正式测试点。测试通过仅表示通过当前版本测试，不能证明知识点掌握。
- 学生数据按账号隔离；教师只可查看非空同班学生，管理员可查看全部。
- 学生口头表示“AC”不结束答疑。修改后的同题提交须通过正式题包验证，才可结案；普通聊天不更新掌握度。
- 工作台支持代码草稿、历史继续、执行证据、答疑、修改重提和带认证的错题卡导出。

## 本地启动

需要 Python 3.11+、Node.js 22+、pnpm 11 和运行中的 Docker Desktop。以下为 Windows PowerShell 命令。

在仓库根目录准备本地配置，再启动后端；`.env` 中填写本地密钥：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\scripts\dev.ps1
```

脚本会启动开发基础设施、初始化 Python 环境、执行数据库迁移并启动 API。已有环境升级后也须执行迁移并重启旧进程。

另开终端，在仓库根目录导入自建题包：

```powershell
backend\.venv\Scripts\python.exe scripts\import_packages.py --check
backend\.venv\Scripts\python.exe scripts\import_packages.py
```

如需演示账号，可运行 `backend\.venv\Scripts\python.exe scripts\seed_demo.py`。账号使用说明见登录页；演示密码仅用于本机。教师需配置与学生一致的非空 `class_name` 才可查看班级数据。

启动前端：

```powershell
cd web
pnpm install --frozen-lockfile
pnpm dev
```

前端地址为 http://127.0.0.1:5173，API 文档为 http://127.0.0.1:8000/docs。首次无模型配置时，确定性检查和执行证据仍可使用；不能定位的问题保留未知。

## 验证

在 `backend/` 激活 `.venv` 后运行：

```powershell
ruff check .
pytest -q
```

设置 `TEST_DATABASE_URL` 后可执行真实 PostgreSQL 接口回归；数据库名称必须包含 `test`，先以该连接执行 `alembic upgrade head`。测试使用事务回滚，不应指向业务数据库。额外设置 `RUN_DOCKER_TESTS=1`，可执行真实 C++ 错解、修改和答疑核验场景，需要本地 `oi-judge:dev` 镜像。

本轮本机结果：全部 **156 项测试通过**，包括独立 PostgreSQL 测试库和真实 Docker 判题；Ruff 通过；`web/` 下 `pnpm build` 通过。前端已验证登录和草稿恢复，完整新后端浏览器流程尚待联调。未调用外部模型验证诊断质量。

CI 会运行后端静态检查、迁移、PostgreSQL 接口测试、工具检查和前端构建；真实 Docker 判题是显式启用的本地回归。

## 目录

- `backend/app/`：接口、业务服务、诊断、答疑与判题。
- `backend/tests/`、`backend/migrations/`：测试与数据库迁移。
- `web/src/`：学生工作台和教师查看界面。
- `problems/PTV-*/`：自建题包、标准程序和输入输出。
- `deploy/`、`scripts/`、`tools/`：部署、导入、评测和独立工具。
- `docs/`、`eval/`：设计、历史实验与评测材料。

## 配置与已知限制

仅提交无真实密钥的 `.env.example`。`.env`、私钥、运行目录和构建产物均在忽略规则内；题面、标签及评测数据随仓库公开。上传前可运行 `backend\.venv\Scripts\python.exe tools\check_secrets.py` 检查 Git 暂存内容；检查只报告路径与类型。

自建题包采用 CC BY-SA 4.0；采集题面与页面版权归原平台及出题人，来源与许可边界见 [数据说明](data/README.md)。当前题包规模较小，教师复核写回、完整提交谱系和真实教学效果评估仍待实现。Docker 执行器用于本地开发与受控演示，尚未完成开放互联网环境的执行隔离验收。

