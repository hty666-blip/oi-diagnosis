---
name: OI 错题诊断
description: 以题目、代码、执行证据和教学任务为中心的中文电脑工作台
colors:
  bg: "#f4f4f4"
  panel: "#ffffff"
  line: "#e0e0e0"
  line-soft: "#e8e8e8"
  field-border: "#8d8d8d"
  text: "#161616"
  muted: "#525252"
  dim: "#6f6f6f"
  accent: "#0f62fe"
  accent-2: "#0043ce"
  accent-active: "#002d9c"
  accent-soft: "#edf5ff"
  ok: "#198038"
  warn: "#8a5d00"
  bad: "#da1e28"
  success-text: "#0e6027"
  success-bg: "#defbe6"
  warning-text: "#684e00"
  warning-bg: "#fcf4d6"
  error-text: "#a2191f"
  error-bg: "#fff1f1"
typography:
  headline:
    fontFamily: '"IBM Plex Sans", "Microsoft YaHei", "PingFang SC", sans-serif'
    fontSize: "26px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0"
  title:
    fontFamily: '"IBM Plex Sans", "Microsoft YaHei", "PingFang SC", sans-serif'
    fontSize: "16px"
    fontWeight: 500
  body:
    fontFamily: '"IBM Plex Sans", "Microsoft YaHei", "PingFang SC", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: ".16px"
  label:
    fontFamily: '"IBM Plex Sans", "Microsoft YaHei", "PingFang SC", sans-serif'
    fontSize: "13px"
    fontWeight: 400
  code:
    fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.75
rounded:
  square: "0px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.panel}"
    rounded: "{rounded.square}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-2}"
  button-primary-active:
    backgroundColor: "{colors.accent-active}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.square}"
    padding: "7px 12px"
  field:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text}"
    rounded: "{rounded.square}"
    padding: "9px 12px"
  navigation-item:
    textColor: "{colors.muted}"
    height: "44px"
    padding: "0 20px"
    rounded: "{rounded.square}"
  badge:
    backgroundColor: "{colors.line}"
    textColor: "{colors.muted}"
    rounded: "{rounded.square}"
    padding: "2px 8px"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.square}"
    padding: "24px"
---

# Design System: OI 错题诊断

## Overview

**Creative North Star: "证据工作台"**

学生练习、教练安排训练与复核、管理员处理平台事务。先呈现标题、工具条和工作内容，解释紧邻相关状态与操作。

白色内容区置于灰色画布，用排版、留白和细线分组。蓝色标记操作、选择和焦点，语义色表示执行状态。产品事实见 [README](README.md) 与 [改进需求基线](docs/改进需求基线.md)。

参考 [awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 的 [IBM 文档](docs/design-reference/IBM-DESIGN.md)，固定版本 `fcc70284932276d085f01ef9f83edde90b1258cb`；[来源](docs/design-reference/SOURCE.md)、[MIT 许可](docs/design-reference/LICENSE) 与字体 [OFL 许可](web/public/fonts/OFL.txt) 随仓库保留。采用灰阶、蓝色、排版和直角形态，使用产品自身 OI 标识与中文任务结构。

**Key Characteristics:**

- 电脑任务布局，列表、表格、题面和编辑器承担主体内容。
- 平面表面、直角控件、有限强调色。
- 执行事实、模型建议、人工审定与恢复动作分别可识别。

## Colors

`accent` 用于主操作与焦点，`accent-2` 用于悬停及链接，`accent-active` 用于按下，`accent-soft` 用于轻选择背景。

`bg` 为画布与字段底色，`panel` 为内容区；`line`、`line-soft` 分隔区域，`field-border` 为字段底线。正文使用 `text`，辅助信息使用 `muted`、`dim`。

判定使用 `ok`、`warn`、`bad`；标签使用对应语义前景与浅底。状态同时给出文字与测试范围。深色源码区使用 `text` 色底、`bg` 色字；普通证据和样例仍为浅色。

## Typography

界面自托管 IBM Plex Sans（400 / 500 / 600），中文按 token 字体栈回退，禁用合成字重；代码使用本机等宽字体。页面、卡片、正文分别使用 `headline`、`title`、`body`。

辅助信息（12px）、表格（13px）、题面（14px / 1.85）、答疑正文局部（16px）、登录标题（28px / 400）均为既有局部层级。长篇讲评与反馈限宽（75ch），代码保留缩进与横向滚动。

## Layout

电脑外壳：粘附顶栏（56px）、左导航（208px）、剩余主区；主区最大宽（1648px），内边距（28px 32px 56px）。页标题与右侧工具条同行，正文从下一行开始。

内容区内边距（24px）、区域间距（20px）、控件间距（12px）。练习页题面与编辑区按（0.95 : 1.05）分栏、间隔（24px）；题面独立滚动，结果与答疑排在编辑器下方。训练列表宽（264px）；管理首页使用两列与跨列近期操作。

已有窄窗口规则：做题区在（1100px）及以下改单列；（1056px）及以下导航缩至（184px）、管理首页改单列；（760px）及以下外壳堆叠。表格容器可横向滚动。本轮验收范围为电脑，不包含手机验收。

登录品牌栏高（64px），居中表单宽（420px）、内边距（36px）。弹窗宽上限常规（640px）、宽版（880px），同时受视口约束。

## Elevation & Depth

白灰表面、细线与填色表达层次，没有投影或按钮悬浮位移。弹窗遮罩为 `rgba(22,22,22,.5)`。保留短时状态过渡；减少动态效果偏好会关闭过渡与动画。

## Shapes

按钮、字段、标签、卡片、弹窗与标识使用直角（`square`），边框通常为（1px）。聊天气泡保留单个下角（4px）的局部例外。

## Components

- **按钮**：主操作蓝底白字、次操作白底细边，最小高（36px）；表格文字操作最小高（30px）；登录按钮满宽、最小高（42px）。禁用透明度（0.5），等待文字说明具体动作。
- **字段**：浅灰填充、底线与上方标签；蓝色（2px）焦点内描边、偏移（-2px）。错误显示相关恢复动作，保留输入内容。
- **导航与页签**：当前导航灰底、加粗、蓝图标，使用 `aria-current`；选中页签蓝色下划线，使用 `aria-pressed`。选中页签仍有焦点：蓝色（2px）、偏移（-4px）；普通按钮、链接、折叠标题偏移（2px）。保留跳到主内容入口。
- **表格与容器**：白底、灰表头、行细线，单元格内边距（12px 16px），悬停浅灰。空状态、等待与错误分别呈现，标签只放短状态。
- **代码与证据**：源码编辑器最小高（340px），保留语言选择、快捷提交、草稿、结果、诊断、原始证据、协作记录与答疑入口。
- **弹窗**：原生 `dialog`，确认在右；保留初始焦点、Tab 约束、关闭后焦点恢复、忙碌时防误关和错误聚焦。

## Do's and Don'ts

- Do 先呈现任务、数据和操作；删除重复介绍、无关机制解释、伪精确指标与无证据效果承诺。
- Do 保留决定下一步所需的测试范围、权限影响、导入许可、不可逆操作后果和错误恢复说明，并放在相关操作附近。
- Do 区分无测试、样例通过、题包通过、基础设施失败、模型建议和人工审定；缩短文案时保持这些事实边界。
- Do 沿用角色入口、控件状态、键盘行为与电脑布局，核对实际渲染的溢出和可操作性。
- Don't 为视觉整理删除、合并或改变既有功能、事件、权限、请求参数和数据流；学生、教练、管理员现有流程及历史恢复、导出、复核、任务重试/取消均须保留。
- Don't 把测试通过改写为完整正确或已经掌握，也不把合成验收截图当作真实教学效果。
- Don't 将参考文档的官网营销布局、第三方标识、大号轻字标题、渐变或装饰性展示区搬入工作台。

实现依据为 [styles.css](web/src/styles.css)、[App.tsx](web/src/App.tsx)、[Login.tsx](web/src/Login.tsx) 与现有组件。桌面合成验收截图位于 `docs/images/redesign-*.png`，覆盖登录、管理、账号、弹窗、教练与练习。界面改动后同步本文件与 `.impeccable/design.json`，不把计划写成已实现事实。
