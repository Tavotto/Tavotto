# ADR 0005：外部交接（`magplot open`）与 Codex 插件

状态：已实施（2026-08-18）；第 4 节的「skills-only」已被
[ADR 0006](0006-codex-mcp-app-and-publication-profile.md) 部分推翻——插件现在**同时**带一个
本地 stdio MCP server 与内嵌画布，交接那条路（本 ADR 的 1–3 节）原样保留。
相关：[0001 对象层级](0001-project-canvas-tab-object.md)、[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)、
[0006 Codex MCP App 与出版规范](0006-codex-mcp-app-and-publication-profile.md)

## 背景

Magplot 一直假设「用户先有一个图库目录，然后打开 Magplot」。但图越来越多是**在别处
刚生成出来的**——Codex / Claude Code 写一个 matplotlib 脚本、跑一遍、拿到一张 PDF。
这时用户手里有的是「一张图 + 一个脚本」，而不是「一个项目」。

要让这条路顺，缺的不是渲染能力，是两件事：

1. **一条外部程序能调的入口**：把「这张图」翻译成 Magplot 的世界观并打开它；
2. **一份写脚本的约定**：图要能在 Magplot 里双击进去改元素，脚本必须满足几条硬性
   要求（最关键的是**脚本与产物同目录**）。

## 决策

### 1. 交接入口是 `magplot open <路径>`，不是新协议

实现在 `src/magplot/engine/handoff.py`（纯标准库），三步：**解析目标 → 登记 stem →
唤起界面**。

* **解析目标**：接受产物（`.pdf/.png…`）、脚本（`.py`，产出名由静态扫描解出）或目录，
  产出 `(项目目录, stem)`。项目 = 含 `mm_registry.json` 的那一层（向上找 ≤3 层），
  找不到才退回图自己的目录。**向上找有上限**：静默把某个上层目录当图库，会把一整棵
  源码树当素材扫一遍。
* **登记 stem**：注册表缺这一条，图能显示但双击进不去。合并复用
  `discover.merge`（现有条目永远优先、冲突只报告不裁决），**不另写一套裁决**。
  注册表读不懂时报错退出，绝不重写用户手写的资产。
* **唤起**：桌面 App 优先，没有才退回浏览器模式。浏览器模式先探本机 5089 上有没有
  实例——有就让它 `POST /api/projects/open` 并带 `?pj=` 打开，**绝不再起一个进程去抢
  同一个端口**（抢不到的那个只会把用户送回旧项目）。

为什么不做 `magplot://` 自定义 URL scheme：多一套注册（安装器、卸载残留、
Windows 注册表），换来的能力与「直接 exec 桌面二进制」完全一样——单实例插件本来
就负责把 argv 转发给在跑的窗口。

### 2. 桌面交接的契约是 argv：`Magplot --open <项目目录> [--stem <stem>]`

**生产者唯一**：`handoff.desktop_argv()`。**消费者唯一**：`src-tauri/src/main.rs`
的 `parse_open_args()`。两侧各有单测看护，改一边必须同步另一边。

两条路，汇进前端同一个执行体（`web/src/lib/openRequest.ts`）：

| 场景 | 壳做什么 | 前端入口 |
| --- | --- | --- |
| 首启 | 项目 → sidecar 的 `--figures`；stem → 落地 URL 的 `?open=` | `readOpenRequestFromUrl()` |
| 已经开着窗口 | 单实例转发 argv → emit `magplot:open` | `onDesktopOpen()` |

浏览器模式与桌面首启共用 `?open=`，所以**只有一份定位逻辑**。

macOS 上刻意**直接 exec 包内二进制**而不是 `open -a Magplot --args`：App 已经在跑时
`open -a` 的 `--args` 根本不会送达，交接会静默失败。

### 3. 前端交接的三条纪律

`applyOpenRequest()` 里：

1. **同项目绝不调 `projectStore.open`**——那条路会 `switchDocument` 成空白文档，
   用户正在排的版当场没了。同项目只重扫素材。
2. **必须重扫素材**：交接的图是刚写到磁盘上的，运行中实例手里那份 panels 是旧的。
3. **找不到就说找不到**：绝不退而求其次选中别的面板。

同一张图重复交接（用户在 Codex 里改一版就交一次）只选中已有面板，不叠第二份。

### 4. Codex 插件随 Magplot 仓库一起发（当时定为 skills-only，见下方修订）

`codex-plugin/` + 仓库根的 `.agents/plugins/marketplace.json`（仓库即插件市场根，
`codex plugin marketplace add erwanjun/magplot` 直接可用）。CLI 与 Codex 桌面应用
共用同一份插件目录，装一次两边都在。

当时的决定是**不做 MCP server**（「Codex 本来就能跑 Python、读写文件，缺的是约定与
最后一跳」）。**这一条已被 ADR 0006 推翻**：那个判断只对「生成脚本」成立；结构化图表
编辑要的是 manifest、override 语义、canonical patch 哈希、规范预检与真矢量导出，
没有一样是「跑个 Python 脚本」能替代的。现在插件同时带 skills 与一个本地 stdio MCP
server（`codex-plugin/.mcp.json`），技能这一条路一字未改。

**仍然不做 `.app.json`**：那需要在 OpenAI 侧注册一个托管的 App/Connector id，
与本地工具链无关。

技能 `magplot-figure` 的硬性约定里，第一条也是最要紧的一条是**脚本与产物同目录、
且必须先落成文件**（禁止 `python -c` 出图）——Magplot 靠「stem ↔ 产出它的脚本」把图
变成可参数化面板，没有脚本文件就只有一张死图。

自检不靠祈祷：`scripts/handoff.py` 读 `magplot open --json` 的
`registry.parameterizable`，为 false 时**退出码 4**并给出怎么修。图出来了但只是死图，
那不是成功。

## 代价与已知边界

* 每次交接都会在图库目录里写 `mm_registry.json`（缺条目时）。这是 Magplot 打开项目
  本来就会做的事，交接只是提前做掉。
* Linux 没有桌面发行形态，那里交接一律走浏览器模式。
* `magplot open` 是**纯 flag 形态主入口之外的子命令**，在 argparse 之前拦截分派——
  改成 subparsers 会把 `magplot --figures …` 整个既有命令行换掉。
