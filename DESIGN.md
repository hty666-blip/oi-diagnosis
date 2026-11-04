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

学生围绕题目、代码和提交完成练习，教练安排训练、查看档案与复核，管理员处理账号、班级、题库及运行任务。界面保持适合持续操作的密度，先给标题、工具条与工作内容；解释紧邻需要它的状态或操作。

白色内容区置于灰色画布，以字体层级、留白和细分隔线组织信息。蓝色标记操作、选择和焦点，语义色表示执行状态。这里只记录已实现的系统；产品事实见 [README](README.md) 与 [改进需求基线](docs/改进需求基线.md)。

参考来自 [awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 的 IBM 文档，固定版本 `fcc70284932276d085f01ef9f83edde90b1258cb`；本地 [参考文档](docs/design-reference/IBM-DESIGN.md)、[来源说明](docs/design-reference/SOURCE.md) 与 [MIT 许可](docs/design-reference/LICENSE) 一并保留。采用灰阶、蓝色、排版和方形控件；产品使用自身 OI 标识与中文任务结构。字体许可见 [OFL.txt](web/public/fonts/OFL.txt)。

**Key Characteristics:**

- 电脑任务布局，列表、表格、题面和编辑器承担主体内容。
- 平面表面、直角控件、有限强调色。
- 执行事实、模型建议、人工审定与恢复动作分别可识别。

## Colors

主色使用 `accent`；悬停、链接和表格动作使用 `accent-2`，按下使用 `accent-active`，轻选择背景使用 `accent-soft`。蓝色用于可操作或已选择的对象。

中性色中，`bg` 承担画布与字段底色，`panel` 承担内容区；`line`、`line-soft` 分隔列表和区域，`field-border` 承担字段底线。`text` 用于正文，`muted`、`dim` 用于标签和辅助信息。

判定文字使用 `ok`、`warn`、`bad`；状态标签使用对应的 `success-*`、`warning-*`、`error-*` 前景与浅底组合。状态同时给出文字与测试范围，不能只靠颜色表达。源码编辑器与源码块使用反向明暗：`text` 色底、`bg` 色字。

## Typography

界面字体为自托管 IBM Plex Sans（400 / 500 / 600），中文回退 Microsoft YaHei、PingFang SC；禁用合成字重。代码使用本机等宽字体栈。上方 token 是常用层级；卡片标题采用 `title`，页面标题采用 `headline`，正文采用 `body`。

辅助信息与状态标签通常为（12px），表格为（13px）。题面 Markdown 为（14px / 1.85），答疑正文局部保留（16px）以便阅读；登录标题为（28px / 400），不是全局展示字级。长篇讲评与反馈保留现有（75ch）内容宽度约束，代码保留缩进与横向滚动。

## Layout

电脑外壳为两列网格：顶栏高（56px），左导航宽（208px），主区使用剩余宽度。顶栏与导航保持粘附；主区最大宽度（1648px），内边距为上（28px）、左右（32px）、下（56px）。页标题与右侧工具条同行，正文从下一行开始。

常用区域内边距（24px），区域间距（20px）；控件行间距（12px）。练习页在足够宽的窗口中按题面与编辑区（0.95 : 1.05）分栏，间隔（24px）；题面独立滚动，结果与答疑顺序排在编辑器下方。训练工作区左列表宽（264px），右侧展示任务详情；管理首页为待处理事项与状态两列，近期操作横跨两列。

保留代码中已有的窄窗口布局：做题区在（1100px）及以下改单列；（1056px）及以下导航缩至（184px）、管理首页改单列；（760px）及以下外壳堆叠。表格保留容器横向滚动。这些是窗口收窄的现有降级规则；本轮用户验收范围为电脑，不包含手机验收。

登录页使用（64px）品牌栏与居中表单，表单宽（420px）、内边距（36px）。管理弹窗常规宽上限（640px）、宽版（880px），同时受视口宽度和高度限制。

## Elevation & Depth

常规界面没有投影，不用悬浮位移制造按钮反馈。白灰表面、分隔线与局部填色表达层次。原生模态弹窗通过半透明深色遮罩隔离背景，遮罩为 `rgba(22,22,22,.5)`；弹窗自身仍无投影。动效限于已有短时颜色与边框过渡；系统要求减少动态效果时关闭过渡和动画。

## Shapes

通用形态为直角（`square`）：按钮、输入、标签、卡片、弹窗与 OI 标识都沿用该形态。边框通常为（1px），不额外套多层卡片。现有聊天气泡保留单个下角（4px）的局部样式，不将它推广到通用控件。

## Components

- **按钮**：主操作蓝底白字，常规最小高（36px）；次操作白底细边。表格内操作为紧凑蓝色文字，最小高（30px），悬停显示浅蓝底。登录按钮满宽且最小高（42px）。悬停、按下和禁用沿用现有色阶；禁用透明度（0.5），请求期间保留具体动作状态，如“正在发送任务…”。
- **字段**：浅灰填充、灰色底线、直角，标签置于字段上方；占位符不替代标签。聚焦时蓝色（2px）内描边，偏移（-2px）；代码编辑器也保留可见焦点。错误在相关区域显示红色提示与可执行的恢复动作。
- **导航与页签**：导航带图标和文字，当前项为灰底、加粗文字与蓝色图标，使用 `aria-current`。管理页签以蓝色下划线和字重标记选中，使用 `aria-pressed`；选中页签仍显示键盘焦点。普通按钮、链接与折叠标题的焦点为蓝色（2px）外描边、偏移（2px）；页签向内偏移（-4px）。保留跳到主内容的键盘入口。
- **表格与容器**：白色内容区、灰色表头、行分隔线，单元格内边距（12px 16px），悬停行浅灰。空状态给出现状及可用的下一步；等待状态与空结果分开。状态标签为短文字，不能承担整段说明。
- **代码与证据**：深色仅用于代码编辑器及源码块；样例与普通执行证据采用浅灰底。编辑器最小高（340px），正文按等宽字体显示；保留语言选择、快捷提交、草稿、执行结果、诊断、证据、协作记录与答疑入口。
- **管理弹窗**：使用原生 `dialog`，标题区与正文分隔，关闭按钮始终可定位；主要确认动作排在操作区右侧。保留初始焦点、Tab 约束、关闭后焦点恢复、忙碌时防误关和错误聚焦。

## Do's and Don'ts

- Do 先呈现任务、数据和操作；删除重复介绍、无关机制解释、伪精确指标与无证据效果承诺。
- Do 保留决定下一步所需的测试范围、权限影响、导入许可、不可逆操作后果和错误恢复说明，并放在相关操作附近。
- Do 区分无测试、样例通过、题包通过、基础设施失败、模型建议和人工审定；缩短文案时保持这些事实边界。
- Do 新增页面沿用已有角色入口、控件状态、键盘行为与电脑布局；以实际渲染结果核对文字溢出和表格可操作性。
- Don't 为视觉整理删除、合并或改变既有功能、事件、权限、请求参数和数据流；学生、教练、管理员现有流程及历史恢复、导出、复核、任务重试/取消均须保留。
- Don't 把测试通过改写为完整正确或已经掌握，也不把合成验收截图当作真实教学效果。
- Don't 将参考文档的官网营销布局、第三方标识、大号轻字标题、渐变或装饰性展示区搬入工作台。

实现依据为 [全局样式](web/src/styles.css)、[应用入口](web/src/App.tsx)、[登录](web/src/Login.tsx) 与现有组件。桌面合成验收截图覆盖 [登录](docs/images/redesign-login.png)、[管理首页](docs/images/redesign-admin.png)、[账号](docs/images/redesign-accounts.png)、[弹窗](docs/images/redesign-modal.png)、[教练](docs/images/redesign-teacher.png) 与 [练习](docs/images/redesign-practice.png)。更新界面后同步核对本文件与 `.impeccable/design.json`，不将参考值或计划写成已实现事实。
