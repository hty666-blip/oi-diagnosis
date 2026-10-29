# Repository Guidelines

## 项目结构与模块组织

本项目用于信息学竞赛错题诊断与教学答疑。`backend/app/` 是 FastAPI 后端：`api/` 定义接口，`services/` 串联业务，`agents/` 与 `orchestrator/` 实现诊断协作，`capabilities/` 提供判题、分诊和反例最小化能力。数据库迁移放在 `backend/migrations/`，测试放在 `backend/tests/`。

`web/src/` 存放 React、TypeScript 和样式；`deploy/` 存放 Docker 配置；`problems/PTV-*/` 存放自建题包；`tools/`、`scripts/` 分别提供独立工具与开发、导入、评测脚本。设计文档位于 `docs/`，评测数据和报告位于 `eval/`，演示素材位于 `examples/`。

## 构建、测试与开发命令

使用 Python 3.11+、Docker Desktop 和 pnpm，所有命令均在项目内执行。

| 工作目录 | 命令 | 用途 |
| --- | --- | --- |
| 根目录 | `.\scripts\dev.ps1` | 启动基础设施，初始化后端环境、执行迁移并启动 API（8000） |
| `backend/`，激活 `.venv` 后 | `python -m pip install -e ".[dev]"` | 安装后端及测试、检查依赖 |
| `backend/`，激活 `.venv` 后 | `alembic upgrade head` | 更新数据库结构 |
| `backend/`，激活 `.venv` 后 | `ruff check .` | 执行与 CI 一致的静态检查 |
| `backend/`，激活 `.venv` 后 | `pytest -q` | 运行后端测试 |
| `web/` | `pnpm install --frozen-lockfile` | 按锁文件安装前端依赖 |
| `web/` | `pnpm dev` | 启动前端（5173），代理 `/api` 到后端 |
| `web/` | `pnpm build` | 执行 TypeScript 检查并构建生产资源 |
| 根目录 | `backend\.venv\Scripts\python.exe tools\check_package.py --dir problems` | 校验自建题包 |

## 代码风格与命名

Python 使用 4 空格缩进、类型标注、`snake_case` 函数和模块、`PascalCase` 类名。遵循 `backend/pyproject.toml` 的 Ruff 配置：目标 Python 3.11，行长设置为 100，`E501` 已忽略。前端沿用 2 空格缩进、双引号、分号，组件使用 `PascalCase.tsx`，函数和状态使用 `camelCase`。TypeScript 已启用严格检查；未配置 ESLint 或 Prettier。

## 测试要求

使用 pytest 与 pytest-asyncio，文件命名为 `test_*.py`，函数命名为 `test_*`；异步测试支持自动模式。为诊断规则、证据校验、模型降级和辅导防代做行为补充回归用例，外部 HTTP 调用优先使用 `httpx.MockTransport`。CI 尚未强制覆盖率阈值，前端尚未配置自动化测试；界面变更需通过构建并手动验证相关流程。

## 提交与 Pull Request

提交历史尚少，使用简短祈使句并标明范围，例如 `backend: validate diagnosis evidence`。PR 应说明问题、变更行为、验证命令与结果，关联已有 issue；界面变更附截图，数据库变更附迁移及兼容性说明。

## 配置与数据边界

需要本地配置时，从 `.env.example` 创建 `.env`，保留已有配置。禁止提交真实密钥、个人数据和运行缓存。题面、标签及评测数据随仓库公开，保留各自来源与许可说明；自建题包放入 `problems/`。解释和文档使用中文，代码标识符保留原文。
