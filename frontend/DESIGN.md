---
version: 1
name: SAI 校园问答
colors:
  bg:            { light: "#f6f6f9", dark: "#0e1014" }
  surface:       { light: "#ffffff", dark: "#161920" }
  surface-2:     { light: "#eeeef4", dark: "#1d212a" }
  surface-3:     { light: "#e6e6ee", dark: "#242a35" }
  border:        { light: "#e4e4ec", dark: "#262b36" }
  border-strong: { light: "#d2d2de", dark: "#333a48" }
  text:          { light: "#191a20", dark: "#e8e9ef" }
  text-muted:    { light: "#62646f", dark: "#a2a6b4" }
  text-faint:    { light: "#74768a", dark: "#8b90a0" }
  accent:        { light: "#5b53d8", dark: "#8f88f0" }
  accent-hover:  { light: "#4b43c8", dark: "#a29bf5" }
  accent-soft:   { light: "#ecebfb", dark: "#21203a" }
  accent-ink:    { light: "#453dbb", dark: "#b3adfa" }
  accent-border: { light: "#d6d4f5", dark: "#383663" }
  danger:        { light: "#c2352f", dark: "#e8736d" }
  success:       { light: "#2f7d4f", dark: "#5fbf8a" }
  warn:          { light: "#8a5a12", dark: "#d8a45c" }
type:
  family-ui:  "Outfit, -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif"
  family-num: "Outfit, ui-monospace, 'SF Mono', Consolas, monospace"
  size: [12, 13, 14, 16, 18, 20, 24, 32]
  weight: [400, 500, 600, 700]
  leading: { tight: 1.25, body: 1.7, dark: 1.75 }
  measure: "65-75ch"
space: [4, 8, 12, 16, 24, 32, 48, 64]
radius: [6, 10, 14, 20]
motion:
  ease: "cubic-bezier(0.2, 0.8, 0.2, 1)"
  fast: "140ms"
  base: "200ms"
  slow: "320ms"
elevation:
  md: "0 4px 16px -4px rgba(24,26,40,.10)"
  lg: "0 16px 48px -12px rgba(24,26,40,.18)"
---

# SAI 校园问答 · 设计系统

面向桂林电子科技大学师生的校园制度问答工具。**用户是来找答案的，不是来逛的** —— 所有取舍以"更快读到、更少怀疑"为准。

## Overview

一套**编辑感的后台工具**：排版承担层级，结构靠 1px 细线，色彩极度克制，动效只在状态变化时出现。刻意避开"AI 生成界面"的通用形态 —— 渐变、光晕、通用卡片、等宽三连卡。

## Colors

**The One Voice Rule.** 强调色在任何一屏的覆盖面积不超过 10%。它只标记三类东西：当前选中项、唯一的主操作、可点的链接。除此之外一律用中性色。

**The Single Hue Rule.** 全站只有一个强调色相（蓝紫，约 285°）。不引入第二个彩色。深色主题单独取值提亮，**不是浅色的机械反转**。

中性色统一向强调色相偏一点冷，不混暖灰。

对比度底线（已逐项核算，浅/深两套都达标）：正文 ≥ 4.5:1，大字号 ≥ 3:1，控件与焦点环 ≥ 3:1。
`--text-muted` 与 `--text-faint` 都落在 AA 之上，两者只差一档明度，用于区分"次要"与"最次要"。

## Typography

**The Two-Script Rule.** 西文与数字用 Outfit（自托管 Latin 子集，32KB，不走 CDN），中文回落到系统栈 PingFang SC → 微软雅黑 → Noto Sans SC。因此字体族写法恒为 `Outfit, <中文字体栈>`，中文由回落接管，不额外加载中文字体文件。

字号只用 `12 / 13 / 14 / 16 / 18 / 20 / 24 / 32` 这一条阶梯，相邻级差不小于 1.25 倍。
正文 **16px 起**，行高 1.7；深色下 1.75 并加一档字重（浅色底反白需要更多"墨水"才能等价可读）。
所有数字一律 `font-variant-numeric: tabular-nums` —— 向量数、相关度分数会实时刷新，比例数字会左右跳动。

## Layout

间距走 4 基数阶梯 `4/8/12/16/24/32/48/64`，**标题上方的留白恒大于下方**，让标题归属它下面那段内容。

正文列限宽 65–75ch。聊天消息列固定 760px 居中，不随窗口拉伸 —— 4K 屏上一行 40 个字是读不下去的。

## Elevation & Depth

**The Hairline First Rule.** 结构用 1px 描边表达，阴影只用于**真正浮起**的东西：输入岛、弹窗、下拉。静止的内容面板没有阴影。

阴影一律带向下偏移并染冷色相，全站光源方向一致（自上而下）。

## Shapes

圆角四档并**按层级递减**：`6` 小控件（按钮、标签）/ `10` 输入框与列表行 / `14` 内容面板 / `20` 浮层与输入岛。不做全站同一个圆角。

## Components

**输入岛（composer）** —— 唯一的常驻浮起件：`--surface` 底 + hairline + `--shadow-md`，圆角 20，内嵌 textarea 无边框。

**消息** —— 助手回复**不做气泡**，直接是页面上的文字块（去掉容器，让内容自己立住）；用户发言用 `--accent-soft` 填充的右侧气泡。二者靠对齐和底色区分，不靠边框。

**来源标签** —— 6px 圆角细描边小片，不用胶囊；展开后的详情用 `--surface-2` 平铺。

**检索命中** —— 用序号 + 细线分行，**不使用左侧彩色竖条**（那是最典型的生成式 UI 痕迹）。

## Do's and Don'ts

- **Do** 用排版和留白建层级；**Don't** 用渐变、光晕、彩色阴影堆砌。
- **Do** 给每个交互元素 hover / active / focus-visible / disabled 四态；**Don't** 让任何按钮没有按下反馈。
- **Do** 为列表、表格、检索结果写空状态；**Don't** 让"没有数据"是一块空白。
- **Do** 动效只走 `transform` / `opacity` 配指数缓出；**Don't** 用 `linear` / `ease-in-out`、弹性回弹、闪烁光标、脉冲圆点。
- **Do** 让触摸目标 ≥ 44×44（含内边距）；**Don't** 把关键操作做成 24px 的小图标。
- **Do** 让浏览器自身的界面（滚动条、光标、选中色、表单控件）跟随主题；**Don't** 只给 `data-theme` 换色却漏掉 `color-scheme`。