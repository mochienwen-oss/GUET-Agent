---
version: 2
name: SAI 校园问答
colors:
  # 用户指定色板：深海蓝主色 + 中性蓝灰底；派生值经 AA 实测校准
  bg:            { light: "#f3f6fa", dark: "#121a29" }
  sidebar:       { light: "#e9eff7", dark: "#17202f" }
  surface:       { light: "#ffffff", dark: "#1e293b" }
  surface-2:     { light: "#f1f5fa", dark: "#253347" }
  surface-3:     { light: "#e6edf5", dark: "#2c3c52" }
  border:        { light: "#e2e8f0", dark: "#2c3a4e" }
  border-strong: { light: "#cbd5e1", dark: "#3b4c64" }
  text:          { light: "#1f2937", dark: "#f1f5f9" }
  text-muted:    { light: "#4b5b73", dark: "#94a3b8" }
  text-faint:    { light: "#5d6c82", dark: "#93a5ba" }
  accent:        { light: "#254b7d", dark: "#3a639e" }
  accent-hover:  { light: "#3a639e", dark: "#4a76b4" }
  accent-soft:   { light: "#e7eff8", dark: "#1b2b41" }
  accent-ink:    { light: "#1c3e68", dark: "#9fc0e4" }
  accent-border: { light: "#c6d7e8", dark: "#2f4a6b" }
  brand-navy:    { light: "#1a3459", dark: "#3a639e" }
  data:          { light: "#9a5f00", dark: "#f5a623" }
  data-fill:     { light: "#f5a623", dark: "#f5a623" }
  data-soft:     { light: "#fdf3e2", dark: "#33270f" }
  seal:          { light: "#c93b3b", dark: "#f08585" }
  seal-soft:     { light: "#fdecec", dark: "#3a1f22" }
  danger:        { light: "#c93b3b", dark: "#f08585" }
  success:       { light: "#36b37e", dark: "#36b37e" }
  warn:          { light: "#9a5f00", dark: "#f5a623" }
brand:
  source: "桂林电子科技大学 · 人工智能学院"
  navy:   "#0e1e63"
  indigo: "#434ba0"
  red:    "#b1030c"
  orange: "#c2570a"
type:
  family-ui:  "Outfit, -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif"
  family-num: "Outfit, ui-monospace, 'SF Mono', Consolas, monospace"
  size: [11, 12, 13, 14, 15, 16, 20, 24, 32]
  weight: [400, 500, 600, 700]
  leading: { tight: 1.25, body: 1.7, reading: 1.85, dark: 1.75 }
  measure: "65-75ch"
space: [4, 8, 12, 16, 24, 32, 40, 48, 64]
radius: [5, 6, 8, 10, 12, 14, 20]
motion:
  ease: "cubic-bezier(0.2, 0.8, 0.2, 1)"
  fast: "140ms"
  base: "200ms"
  slow: "320ms"
elevation:
  md: "0 2px 8px -2px rgba(16,22,51,.08)"
  lg: "0 16px 48px -12px rgba(16,22,51,.18)"
---

# SAI 校园问答 · 设计系统

面向桂林电子科技大学师生的校园制度问答工具。**用户是来找答案的，不是来逛的** —— 所有取舍以"更快读到、更少怀疑"为准。

## Overview

一套**有学院识别的编辑式工具界面**：排版承担层级，结构靠 1px 细线，色彩分三层配额使用，动效只在状态变化时出现。刻意避开"AI 生成界面"的通用形态 —— 渐变、光晕、通用卡片、等宽三连卡。

v2 相比 v1 的核心变化不是"加颜色"，而是**给颜色分配语义配额**：背景保持浅色（长文本要读得住），色彩由「色带 + 分区淡彩 + 数据」三层承担。

## Colors

**The Three-Layer Colour Rule.** 颜色只允许出现在三个层次，且不得越层：

| 层 | 载体 | 浓度 | 作用 |
|---|---|---|---|
| 基座 | `--bg` / `--sidebar` / `--surface` | 中性 | 承载文字，永不参与表达 |
| 分区淡彩 | `--tint-*` | 约 6% | 划分区域（资料页分区、结果块底色） |
| 强调 | `--accent` / `--data` / `--seal` | 见下表 | 只给数字、选中、状态 |

**The One Voice Rule.** 结构色（`--accent`，靛）在任何一屏的覆盖面积不超过 10%。它只标记三类东西：当前选中项、可点的链接、以及需要被认出的控件边界。

**The Colour Quota Rule.** 四色各有专属语义，不得串用：

| 色 | 令牌 | 只允许出现在 | 面积 |
|---|---|---|---|
| 海军蓝 | `--brand-navy` | 每屏**一个**实底主操作（如「新对话」） | ≤8% |
| 靛紫 | `--accent` | 可点、选中、链接、焦点环 | ≤10% |
| 校旗橙 | `--data` / `--data-fill` | **只给数据**：总数、刻度条、教室号 | ≤5% |
| 学院红 | `--seal` | **只给状态与危险**：角色标记、当前项、退出登录 hover | ≤2% |

四色同屏时（主界面）只允许出现三种可见色块 —— 第四种必须几乎不可见（一个 2px 色带片段、一个 4px 圆点）。这是"不单调"与"不彩虹"的分界线。

**The Band Rule.** 四色色带（`--band-1..4`）是全站唯一的品牌签名，高度 2–3px，只出现在页头顶部与资料页页首。不得放大成色块、不得重复出现在内容区。

中性色统一向强调色相偏冷，不混暖灰。深色主题单独取值提亮，**不是浅色的机械反转**。

对比度底线（已逐项核算，浅/深两套都达标）：正文 ≥ 4.5:1，大字号 ≥ 3:1，控件与焦点环 ≥ 3:1。

## Typography

**The Two-Script Rule.** 西文与数字用 Outfit（自托管 Latin 子集，32KB，不走 CDN），中文回落到系统栈 PingFang SC → 微软雅黑 → Noto Sans SC。因此字体族写法恒为 `Outfit, <中文字体栈>`。

字号只用 `11 / 12 / 13 / 14 / 15 / 16 / 20 / 24 / 32` 这条阶梯。
正文 14–16px，行高 1.7；**长回答正文 14px / 1.85**（阅读态与扫描态要分开）。深色下加一档字重（浅色底反白需要更多"墨水"）。

**The Number-As-Hero Rule.** 关键数字（空闲教室总数、统计计数）用 24–32px 等宽，颜色取 `--data`。数字是结论，不该和正文一个字号。

所有数字一律 `font-variant-numeric: tabular-nums`。

## Layout

间距走 4 基数阶梯 `4/8/12/16/24/32/40/48/64`，**标题上方的留白恒大于下方**。

正文列限宽 65–75ch。聊天消息列固定 760px 居中，不随窗口拉伸。资料页双栏 `186px + 自适应`，左栏 sticky，总宽锁 1040px。

## Elevation & Depth

**The Hairline First Rule.** 结构用 1px 描边表达，阴影只用于**真正浮起**的东西：输入岛、弹层、下拉、**当前选中的列表项**。静止的内容面板没有阴影。

**The Double Rule Rule.** 段落级分隔用「上粗下细」双线（`.rule-pair`）而不是单根 hairline —— 单线太平，读不出"这一段结束了"。

## Shapes

圆角按层级递减：`5` chip / `6` 小控件 / `8–10` 列表行与输入框 / `12–14` 内容面板 / `20` 浮层与输入岛。不做全站同一个圆角。

## Components

**输入岛（composer）** —— 唯一的常驻浮起件：`--surface` 底 + hairline + `--shadow-md`，圆角 20。

**消息** —— 助手回复**不做气泡**，直接是页面上的文字块；用户发言用 `--accent-soft` 填充的右侧气泡。

**身份条与栏目条菜单** —— 左下角是"我是谁"的锚点：32px **方形**头像（证件照语感，不用圆形社交头像）+ 昵称 + 学号。点击就地向上展开**栏目条**菜单（每行 40px：图标 + 标签 + 行尾状态），不做居中弹窗 —— 弹窗会让整屏变暗并要求视线移向中央。危险项（退出登录）默认中性色，hover 才转 `--seal`。

**栏目条索引** —— 长页面的导航用栏目条（编号 + 标题，一行一条），**不做描述型目录**。描述性文字会让索引变成第二份正文，读起来比页面本身还累。滚动时高亮跟随（scroll-spy）。

**空教室结果块** —— 白牌 + 顶部 2px 橙色实线；总数 32px 等宽橙；楼栋用 2px 刻度条表达占比（长度即比例）；教室号是淡橙 chip。

**来源标签** —— 6px 圆角细描边小片，不用胶囊。

**等待态（thinking）** —— 三个水滴依次下落：`border-radius` 保留一个直角当尖、`rotate(135deg)` 把尖送到正上方，靠 squash & stretch（下落拉长 → 触底压扁 → 回正 → 收小消散）表达"水"。**不用脉冲圆点**（最典型的通用加载态），也不用旋转圈。

## Motion

**The Frame-Not-Raf Rule.** 流式文本刷新用**时间戳节流 + `setTimeout`**，不用 `requestAnimationFrame` 作为唯一调度器。rAF 在窗口失焦/被节流时不回调，会让"已排程"标记永远为真，界面永久停在一段文字上。滚动写入同样节流（≥90ms），避免每帧读 `scrollHeight` 触发强制重排。

**The Cheap-Frame Rule.** 流式期间禁走重管线（KaTeX、引用注入、全文重排）；定稿后一次性切回完整渲染。用户在看"字长出来"时并不需要公式排版。

## Do's and Don'ts

- **Do** 用排版、留白和色带建层级；**Don't** 用渐变、光晕、彩色阴影堆砌。
- **Do** 让四种品牌色各守其位（配额见 Colors）；**Don't** 让任一屏出现第四种可见彩色块。
- **Do** 给每个交互元素 hover / active / focus-visible / disabled 四态；**Don't** 让任何按钮没有按下反馈。
- **Do** 为列表、表格、检索结果写空状态；**Don't** 让"没有数据"是一块空白。
- **Do** 动效只走 `transform` / `opacity` 配指数缓出；**Don't** 用 `linear` / `ease-in-out`、弹性回弹、闪烁光标、脉冲圆点。
- **Do** 让触摸目标 ≥ 44×44；**Don't** 把关键操作做成 24px 的小图标。
- **Do** 让浏览器自身的界面（滚动条、光标、选中色、表单控件）跟随主题；**Don't** 只换 `data-theme` 却漏掉 `color-scheme`。
