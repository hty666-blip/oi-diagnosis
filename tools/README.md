# tools/ · 题库采集与处理

> **用途限定**：毕业设计 / 本地教学演示。**不对外分发、不商用、不上传到公开仓库。**
> 题面著作权归洛谷及原出题人所有。使用者应自行确保符合[洛谷用户协议](https://help.luogu.com.cn/ula/luogu)。

## 1. 脚本

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `luogu_import.py` | 抓取题面 + 样例 → `data/problems/<pid>.json` | 标准库 + `lxml` |
| `luogu_tags.py` | 抓取标签 id→名称映射 → `data/tags.json`，并回填已抓题目 | 标准库 |
| `check_package.py` | **校验收录题包**（结构/元数据/错因探针覆盖），不编译、不联网 | 标准库 |
| `audit_written_bank.py` | **只读审计笔试候选数据**：哈希、关联、答案结构、去重风险和知识覆盖；不自动批准发布 | 标准库 |
| `surface_lint.py` | **表层错误检查**：中文标点/全角空格/BOM/中文标识符 + GCC 报错翻译 + 一键修复 | 标准库 |
| `probe_*.py` | 调研期的探测脚本（可删） | — |

## 1.5 自建题包校验

```bash
python tools/check_package.py --dir problems          # 校验全部
python tools/check_package.py --dir problems/PTV-0001 # 校验单个
```

它会检查：文件完整性（按 A/B/C/D 级）、meta 必填字段、`target_errors` 是否合法、测试点是否成对、AI 生成的题是否有 `verified` 与 `reviewer`，最后输出**11 类错因的覆盖情况**。

`problems/PTV-0001/` 是一个可直接跑通的示例（求和 / M01 溢出探针）。

## 2. 用法

笔试候选包可先离线审计，报告路径需位于输入目录之外：

```powershell
python -X utf8 tools/audit_written_bank.py --dir "D:\Py_Pj\DS_PROJECY\csp_bank\output" --report eval/written_bank/candidate_v1_audit.json
```

退出码 0 表示所检查的结构无错误，1 表示发现结构错误，2 表示无法读取或解析；警告写入报告。结构通过不代表答案正确、来源已经外部核验或题目可正式发布。`eligible_for_practice` 仅作为来源包的候选统计字段，不能替代人工审核和发布权限。去重风险、标签语义及来源边界的人工检查见 `docs/初赛题库候选数据验收.md`。

```bash
# 1) 先建立标签映射（一次即可，之后抓题会自动套用）
python tools/luogu_tags.py

# 2) 抓题（单线程 + 默认 2 秒间隔，务必不要调小）
python tools/luogu_import.py --ids P1001,P1046,P1047 --delay 2.0

# 3) 从文件批量（每行一个题号）
python tools/luogu_import.py --file ids.txt --delay 2.0

# 4) 重新抓取
python tools/luogu_import.py --ids P1001 --force
```

## 3. 输出格式

```jsonc
{
  "pid": "P1046",
  "title": "[NOIP 2005 普及组] 陶陶摘苹果",
  "difficulty_id": 1,
  "difficulty": "入门",
  "tags": ["模拟", "NOIP 普及组"],
  "tags_raw": [1, 82],
  "sections": { "description": "...", "input_format": "...", "output_format": "...", "hint": "..." },
  "samples": [{ "input": "...", "output": "..." }],
  "markdown": "# P1046 ...",
  "source": "luogu",
  "source_url": "https://www.luogu.com.cn/problem/P1046",
  "license_note": "题面著作权归洛谷及原出题人所有；仅用于非商业毕业设计演示，禁止再分发。",
  "package_level": "C",
  "has_testdata": false,
  "crawled_at": "2026-10-29T..."
}
```

## 4. 内置的克制措施（请勿移除）

1. **只访问 `/problem/{pid}`**；robots.txt 明确禁用的是 `/record`、`/recordnew`、`/paste`。
2. **单线程 + `--delay` 默认 2 秒**，小于 1 秒直接报错退出。
3. **失败指数退避**，最多 3 次；404/403 直接放弃，不重试轰炸。
4. **不抓测试数据、不抓提交记录、不绕过登录/验证码**。
5. 每条数据带 `source_url` 与 `license_note`，界面必须展示来源。

## 5. 重要限制：爬来的题"不完整"

`package_level: "C"` 意味着**只有题面，没有测试数据、没有 validator、没有标程**。因此这些题：

| 能做的诊断 | 做不了的诊断 |
|---|---|
| 编译错误（CE）归类 | 对拍找反例 |
| 静态规则扫描（溢出/越界/多清空） | 输入最小化 |
| 知识点讲解、思路提示 | 基于官方测试点的部分分归因 |
| 跑样例（有 31 组样例可用） | 复杂度增长实验（缺大数据） |

要演示**完整诊断链路**，仍需要按架构文档 §9.6 自建 20~40 道 `A/B` 级题包（题面 + validator + std + 少量测试点）。爬到的题正好可以作为"只有题面"的对照组，在产品里明示降级。

## 6. 当前状态

- 已抓取 **26 题**（NOIP 普及组为主 + 少量模板题），31 组样例
- 难度分布：入门 9、普及− 11、普及/提高− 5、普及+/提高 1
- 标签映射 48 条（未收录的保留为 `tag:<id>`）
