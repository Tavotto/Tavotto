# ADR 0005：外部交接（`magplot open`）与 Codex 插件

状态：已实施（2026-08-18）
相关：[0001 对象层级](0001-project-canvas-tab-object.md)、[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)

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

### 4. Codex 插件是 skills-only，随 Magplot 仓库一起发

`codex-plugin/` + 仓库根的 `.agents/plugins/marketplace.json`（仓库即插件市场根，
`codex plugin marketplace add erwanjun/magplot` 直接可用）。CLI 与 Codex 桌面应用
共用同一份插件目录，装一次两边都在。

**不做 MCP server**：Codex 本来就能跑 Python、读写文件，缺的是约定与最后一跳，
多一个常驻进程只是把简单事情复杂化。**不做 `.app.json`**：那需要在 OpenAI 侧注册
一个托管的 App/Connector id，与本地工具链无关。

技能 `magplot-figure` 的硬性约定里，第一条也是最要紧的一条是**脚本与产物同目录、
且必须先落成文件**（禁止 `python -c` 出图）——Magplot 靠「stem ↔ 产出它的脚本」把图
变成可参数化面板，没有脚本文件就只有一张死图。

自检不靠祈祷：`scripts/handoff.py` 读 `magplot open --json` 的
`registry.parameterizable`，为 false 时**退出码 4**并给出怎么修。图出来了但只是死图，
那不是成功。

### 5. 桌面安装另带一个 console 版 `magplot-cli`，并写一份安装清单（2026-08-18 补）

**问题**：只装了桌面程序的 Windows 用户那里，Codex 插件一直报「没找到 Magplot」。
插件当时只查三处——`MAGPLOT_CLI` / PATH / 当前解释器里的 magplot 模块——
只装桌面版的用户三条全落空。

**为什么不能直接调 `Magplot.exe`**：它是 GUI 子系统的可执行文件，sidecar 也是
（`packaging/magplot.spec` 的 `console=False`）。没有真终端时 `sys.stdout` 是
`None`，`packaging/entry.py` 会把输出改道到 `app.log`——调用方 `capture_output`
拿到的是空 stdout，不是那行 JSON。**GUI 可执行文件不是 CLI**，哪怕它接受同样的参数。

**决策**：

1. `packaging/magplot.spec` 从**同一个 Analysis** 出第二个 exe `magplot-cli`
   （`console=True`），与 sidecar 共用同一份 `_internal/`——代价只是多一个
   ~1.5 MB 的 bootloader。两个 exe 跑的是同一份 `packaging/entry.py`。
2. 子命令分派提到 `engine/cli.py`（纯标准库），`entry.py` 在 import Flask
   **之前**就把 `open` / `doctor` 处理掉：一次交接用不上任何 HTTP 端点，
   不该付整个 Flask 的冷启动。
3. 新增 `magplot doctor [--json] [--write-manifest|--remove-manifest]`：
   无 GUI、不起服务、不联网的健康检查，同时负责维护**安装清单**。
4. 安装清单 `install.json` 落在**用户配置目录**（不是安装目录——那儿可能只读，
   卸载还会被删）。安装器装完跑一次 `doctor --write-manifest`（让 CLI 自己写，
   NSIS 不拼 JSON），应用每次启动刷新一遍（覆盖「用户把 .app 拖走了」
   与「macOS 没有装后钩子」），卸载器在删文件**之前**移除它。
5. 发现链的唯一权威是 `engine/locate.py`：`MAGPLOT_CLI` → PATH → 清单 →
   已知安装位置 → HKCU（只当补充）→ 当前解释器。**任何单一机制都不是唯一
   依据**：清单可能没写成、注册表可能被策略锁住、PATH 可能是空的。

**明确不做的**：不改用户 PATH（写注册表 + 广播 `WM_SETTINGCHANGE` +
1024 字符截断 + 卸载时准确摘除，每一步都可能把用户的 PATH 弄坏，而收益是零——
发现链已经不需要它了）；不要求管理员权限；不把注册表当唯一机制。

**镜像的代价**：插件跑在用户机器上、import 不到 magplot，所以
`codex-plugin/…/handoff.py` 里有一份路径规则的副本。这份重复无法避免，
能避免的是两边悄悄漂开——`tests/test_install_locate.py::test_plugin_mirrors_the_locator`
在一整张环境矩阵（Windows/macOS/Linux × 有无环境变量 × 空格与中文）上逐条比对
两侧输出。协议全文见 [`../handoff-protocol.md`](../handoff-protocol.md)。

## 代价与已知边界

* 每次交接都会在图库目录里写 `mm_registry.json`（缺条目时）。这是 Magplot 打开项目
  本来就会做的事，交接只是提前做掉。
* Linux 没有桌面发行形态，那里交接一律走浏览器模式。
* `magplot open` 是**纯 flag 形态主入口之外的子命令**，在 argparse 之前拦截分派——
  改成 subparsers 会把 `magplot --figures …` 整个既有命令行换掉。`magplot doctor`
  同理，两条都在 `engine/cli.COMMANDS` 里。
* 安装包多了一个 ~1.5 MB 的 bootloader（`magplot-cli`）。这是让「只装桌面版」
  可用的最小代价——另一条路是把 sidecar 改成 console 子系统，那会让双击启动
  弹出黑窗。
* v0.7.0 及更早的安装包里没有 `magplot-cli`。那些用户会拿到
  `desktop_found_cli_missing`（提示升级），而**不是** `magplot_missing`
  ——他们明明装了，让他们再装一遍已经装着的东西只会浪费一轮。
