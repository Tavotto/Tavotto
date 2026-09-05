# ADR 0006：Codex 里的 MCP server / MCP App 画布，与版本化出版规范

状态：已实施（2026-08-18）
相关：[0003 worker 协议 v1](0003-worker-protocol-v1.md)、
[0005 外部交接与 Codex 插件](0005-external-handoff-and-codex-plugin.md)

## 背景

ADR 0005 把插件定成 **skills-only**，理由是「Codex 本来就能跑 Python、读写文件，
缺的是约定与最后一跳」。这个判断在「生成脚本 → 交接 → 打开 Tavotto」这条链上是对的，
但它有一个前提：**用户愿意离开 Codex**。

实际用起来，被抱怨的正是那一跳：图刚画完，用户想挪一下图例、把刻度字号从 8 改成 9，
然后就导出投稿。为此要切到另一个应用、等它起 worker、改完再切回来——而这中间 Codex
对「用户到底改了什么」一无所知，接着聊的时候还得用户自己复述。

同时暴露的第二件事：**「合规」这件事没有可执行的定义**。课题组有一份图表格式规范
（单栏 8cm / 双栏 15cm、≥300dpi、Times New Roman、刻度朝内、图例无框、封闭坐标轴、
线宽四档、Scientific colour maps），但它躺在一份 PDF 里，只能靠人记。前端的
`runPreflight` 里那几个阈值（6pt、300dpi）与导出对话框里的 `85/150/180mm` 是**各写
一份的硬编码**——规范一改，两处都开始撒谎。

## 决策

### 1. 出版规范是一份版本化的 canonical JSON，Python 与 TypeScript 共读

`src/tavotto/profiles/publication.json`（随 wheel 分发）是**规则的唯一出处**：

* Python 侧 `engine/profiles.py` 走 `importlib.resources` 定位（装成 wheel 后源码树的
  相对路径不存在）；
* TypeScript 侧经 `@profiles` 路径别名把它**整份 import 进 bundle**
  （`web/vite.config.ts` / `web/vitest.config.ts` / `web/vite.mcp.config.ts` 各配一次）。

profile 里有 `profile_id` / `version` / `widths_mm` / `allowed_aspect_ratios` /
`font_family` / `cjk_fallback` / `default_font_size_pt` / `min_effective_font_size_pt` /
`absolute_min_font_size_pt` / `min_raster_dpi` / `preferred_formats` / `line_widths_pt` /
`axis_policy` / `legend_policy` / `palette_policy` / `text_weight_policy` /
**`severity`**（检查项 → 等级的映射）。

期刊自定义走**覆盖**而不是新 profile：`journal` 是一份浅合并补丁（`widths_mm` 等几个
子对象深合并），合并结果带 `derived_from` 与 `journal`，proof report 原样写出去——
「这张图是按哪套规矩过的检」必须能从留档里读回来。

文档里**只存 `{id, journal}`，不存规则**（`FigureDocument.profile`，可选字段，schema
仍是 2）。旧文档没有它就走默认 profile；规范升级后旧文档自动跟着新规则走，而不是把
一份过期的规则冻在布局文件里。

### 2. 预检有两个求值器，靠 golden 向量对齐

浏览器里跑不了 Python，Codex 的 MCP server 里跑不了 TypeScript，所以求值器必须有两份：

| 求值器 | 服务谁 |
| --- | --- |
| `src/tavotto/engine/preflight.py` | MCP server 的 `tavotto_preflight` / `tavotto_export` |
| `web/src/lib/preflight.ts` | 画布与导出对话框 |

**两份不许分叉**，办法与 patchspec ↔ Rust supervisor 完全一样：同一份输入
（`tests/golden/preflight_vectors.json`，由 `scripts/gen_preflight_vectors.py --write`
按 Python 侧生成），**pytest 与 vitest 各跑一遍**。只比判据
（`id / severity / object_ids / gids / detail`），不比中文措辞——措辞是界面的事。

输入是一份**规范化的 figure spec**（页面 + 面板 + 文字 + 对象几何），不是画布文档也
不是 manifest 本身。这样同一套规则能同时服务「一张图」（MCP，scale=1）与「多面板
拼版」（画布，每个面板带自己的 scale）。

四档等级：

| 等级 | 语义 |
| --- | --- |
| `error` | 默认**阻止**导出；用户显式确认才放行，确认写进 proof |
| `warn` | 放行，但必须展示 |
| `not_verifiable` | **我们查不了**，需要人工确认；同样写进 proof |
| `suggestion` | 只是建议（数据语义类全在这一档） |

没在 `severity` 表里登记的检查项兜底为 `warn`，**刻意不是 `suggestion`**：新加的检查
忘了登记，用户会以为它通过了。

两条硬纪律：

* **字号按最终物理尺寸判**。manifest 里的 `fontsize` 是脚本坐标系里的 pt，面板缩到
  60% 摆上版面时读者量到的是 `fontsize × scale`。只看原始值会让「缩一缩就放行」成为
  常态。阈值是 8.5pt（严格下限）与 8.0pt（绝对下限，「必须大于 8pt」——**正好 8.0 不算
  过**）。
  > **2026-08-29 修订（ADR 0029）**：8.5pt 那条**已删除**——课题组文档原文里本来就有
  > 8 pt 的图例与刻度，那条比它想守护的规范更严。默认规范里字号下限只剩一个数 8.0pt，
  > 「必须大于 8pt」这一条**语义不变**。两条检查仍然是两条：规范把两档设成不同值时
  > （`free-form-v1` 6.0/5.0、期刊覆盖）各自出场。
* **查不了就说查不了**。外部位图内部的文字字号没有矢量文字层可查，一律
  `not_verifiable`；矢量面板没有 manifest（没渲染 / 不是脚本产物）同理。假装通过一次，
  用户学到的就是「这个提示可以无视」。

数据语义类（柱状图该不该有误差棒、拟合该不该给置信带、色系选得对不对、多条无 marker
的曲线在黑白下能不能分辨）**全部只作 suggestion**，绝不替用户裁决。

### 3. 插件加一个本地 stdio MCP server —— 推翻 ADR 0005 的「不做 MCP server」

ADR 0005 说「多一个常驻进程只是把简单事情复杂化」。那个判断的适用范围是**生成脚本**：
Codex 确实不需要我们帮它写文件。但**结构化图表编辑**不是这样——它要的是 Tavotto 的
manifest（哪些元素可改、每个元素有哪些属性）、override 语义（全量列表 + 自动还原）、
canonical patch 哈希、出版规范预检、真矢量导出。这些没有一样是「跑个 Python 脚本」
能替代的，而把它们塞进技能文档等于让模型每次现推一遍。

所以：`codex-plugin/.mcp.json` 声明一个**本地 stdio** server
（`python3 ./mcp/server.py`，字段形状取自 Codex 官方插件装出来的清单）。六个工具：

| 工具 | 干什么 |
| --- | --- |
| `tavotto_open_figure` | 解析 → 登记 → 起引擎会话 → 渲染一次；回 manifest / SVG / patch hash / profile / 预检 |
| `tavotto_apply_overrides` | 应用**全量** patches 并重渲染；回新 manifest / SVG / warnings / 被拒条目 |
| `tavotto_preflight` | 出版规范体检，机器可读 + 人类可读两份 |
| `tavotto_export` | **先预检**，有 error 且没有 `explicit_confirm` 时一张图都不出；写 proof report |
| `tavotto_verify_replay` | 起一次性 worker 全量重放，与热态逐元素比几何 |
| `tavotto_close_session` | 释放会话；用户项目数据零改动 |

### 两处已知限制（如实记着，别当成已经解决）

**① `command` 是写死的 `python3`，Windows 上未必存在。** 官方安装器装出来的
Python 有 `python.exe`，`python3.exe` 只是 Microsoft Store 的执行别名存根；
反过来 macOS 12.3 起 `/usr/bin/python` 已经没了，`python` 同样不能通用。清单
的字段形状取自 Codex 官方插件装出来的那份，**里面没有按平台分支的写法**，而
这里的纪律是「不要猜」——猜一个字段名的下场是清单 schema 不合法、插件整个
装不上，比现在坏得多。目前的对策是如实写在这儿与 README 里：Windows 用户若
遇到「插件装上了但一个工具都没有」，把 `.mcp.json` 的 `command` 改成自己那个
解释器的绝对路径即可。等确认了官方清单的平台分支写法再收掉这一条。

**② 允许的项目根不能只看进程 cwd，也不能相信模型参数。** 装好的插件 cwd 是
插件自己的目录，而 `project_path` 来自模型；两者都不是用户工作区授权。2026-08-24
引入单一 `RootAuthority`：显式配置 → host Roots 兼容层 → 用户在 MCP elicitation
确认框批准的连接内精确目录 → workspace 环境变量 → 安全 cwd。完整信任顺序、
连接生命周期、server→client 请求关联与真实 Codex capability 证据见
[ADR 0009](0009-codex-workspace-root-authority.md)。

**这一层只翻译，不实现**：会话、manifest、override、patch 规范化、导出全部落回
`tavotto.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。发给 worker 的
patches 与 Flask `/api/engine/render` 走的是同一条路径，所以 ADR 0003 的不变式原样成立：

    hot_apply(canonical_patches)
      == fresh_worker_replay(canonical_patches)
      == writeback_then_reopen(canonical_patches)

`tests/test_mcp_roundtrip.py` 用真 matplotlib + 真 stdio 逐条走了一遍（含 figure 尺寸
改变后 figure 锚定属性、axes 几何改变后依赖属性、关掉重开重放三种）。

三条边界：

* **路径范围校验**（唯一入口是 `RootAuthority`）：Codex 传来的路径可能是模型
  推断的，只有 host Roots、用户原生确认或显式服务器配置等独立权威能授予边界；
  越界一律拒，**绝不「就近找一个能用的」**；
* **stdout 归协议独占**：`hijack_stdout()` 把 `sys.stdout` 改道到 stderr 并**先存下真正
  的 stdout 句柄**。存的顺序反了，协议帧全写到 stderr 上，症状是「initialize 永远等不到
  响应」且没有任何报错（开发期真撞到过，`test_protocol_owns_the_real_stdout` 看着）；
* **没装 Tavotto 时降级而不是退出**：启动器找不到能 import tavotto 的解释器就起一个
  只会说人话的 server（握手正常，每个工具回「这么装」）。静默退出在 Codex 里表现为
  「插件没有工具」。

### 4. 内嵌画布是 MCP App，UI 只挂在需要它的两个工具上

`ui://tavotto/canvas/v1.html`，MIME `text/html;profile=mcp-app`，协议是 MCP Apps 的
**JSON-RPC over postMessage**（`ui/initialize` → `ui/notifications/initialized`；
host 推 `ui/notifications/tool-result`；app 发 `tools/call` / `ui/message` /
`ui/request-display-mode`）。桥是手写的一百来行（`web/src/mcp/appsBridge.ts`），
不引 SDK——那是个 npm 包，而画布要打成单文件塞进资源里。
`window.openai.*` 只在标准路径拿不到东西时兜底，且逐个 feature-detect。

**画布本体就是 Tavotto 前端那一份代码**：`CanvasStage` / `OverlaySvg` /
`interactions.ts` / `ObjectView` / `TextView` / `ArrowView` / `ElementInspector` /
既有 stores，拖拽、命中测试、shift 锁向、吸附、undo/redo、patch 状态**一行都没有第二份
实现**。接进去只改了一处：`lib/engineTransport.ts` 让「消息怎么送到引擎」可替换——
Tavotto 界面里是 HTTP，iframe 里是 `tools/call`。两侧最终落到同一个
`pool.EngineWorker.override`。

那一处的设计有个坑值得记：传输层**不 import `lib/api`**，只存一个可选覆盖。
把默认实现搬进这个模块会与 `lib/api` 绕成环（模块初始化顺序一变就是 TDZ 崩溃），
而且既有单测大量 `vi.mock('@/lib/api')` 打桩 `engineRender`，搬进闭包会让那些桩全部
失效（实测炸了 7 个文件）。

* **UI 只挂在 `tavotto_open_figure` 与 `tavotto_apply_overrides` 上**：预检 / 导出 /
  关会话的产出本来就是文字与文件，给它们挂 UI 只会让画布不停重建。
* **CSP 的 `connectDomains` 是空的**：画布不发任何跨源请求。sidecar 的端口是动态的
  （`127.0.0.1:0`），根本没法提前写进白名单——这也是为什么必须走 `tools/call` 而不是
  让 iframe 连 HTTP。
* **绝不用「开个浏览器」冒充内嵌画布**。
* **iframe 里不存业务数据**：`localStorage` 与 `widgetState` 只放临时 UI 状态，真相
  全部来自工具响应（session_id / manifest / SVG / patch hash）。iframe 随时会被 host
  重建，存在那里的东西就是随时会丢的东西。
* 产物 `codex-plugin/mcp/widget/canvas.html` 是**受管的构建物**（进 git，
  `scripts/build_mcp_widget.py` 生成，`--check` 在 CI 里看着）——插件从仓库本体分发，
  产物不进 git 用户就只有一个空目录。

  > **2026-09-05 修订（ADR 0043）**：这一条**已被取代**。当时的判断没错，错的是前提——
  > 那时安装入口只能指向源码 checkout。现在 marketplace 入口指向机器维护的发行分支
  > `plugin-stable`，画布由 CI 从固定源码状态构建、验证、发布，源码分支**不再跟踪**它。

## 代价与已知边界

* ~~**画布产物 ~580 KiB 进仓库**，且**任何 `web/src` 改动都会让指纹失效**~~——2026-09-05 起
  产物不再入库（ADR 0043），「用户装到的画布与源码同步」改由 CI 的候选验证与发行分支的
  随包清单看着。
* **Codex Desktop 里的 iframe 渲染尚未实测**：协议层（initialize / tools/list /
  tools/call / resources/read）与画布逻辑都有自动化看护，但「Codex 真的把这块 HTML
  塞进 iframe 并跑起来」这一步没有在真桌面应用里验证过。见 README 的对应说明。
* **mathtext 的字体查不准**：manifest 的 `fontfamily` 来自 artist 的 rcParam，而
  `$\\mathrm{cm^{-1}}$` 这类数学式在 matplotlib 里会落到 DejaVu 的数学字形上——
  导出的 PDF 里能看到两个字体。预检查不出这一条。
* **画布合成的导出仍只出 PDF/PNG**（走 PyMuPDF）。SVG 只在单图这一侧给
  （`tavotto_export` / 引擎导出），导出对话框里如实说明。
* MCP 会话上限 8 个，超了按最久未用淘汰——每个会话背后是一个常驻 Python 进程。

## 追记（2026-08-20）：运行时解析器、自管环境与诚实的降级

实测撞到的形态：Codex 用 Homebrew 的 `python3` 起 `mcp/server.py`，机器上只有
桌面版——启动器找不到能 import 引擎的解释器，进入降级模式；而旧降级 server 把
六个工具原样列进 tools/list（调用才发现「当前不可用」），用户看到的是「插件
说自己能用、实际一个能用的都没有」。据此改了三件事（详规见 CLAUDE.md 的
「Codex MCP server 与内嵌画布」一节与 `tests/test_mcp_resolver.py` /
`tests/test_mcp_stdio.py`）：

1. **解析器**：候选链扩到 显式 `TAVOTTO_MCP_PYTHON` → worker env/设置 →
   插件自管 venv → CLI 反推 → PATH，每一条都真的验证 `import tavotto.engine`；
2. **自管环境**：`--provision` 在 Tavotto 配置目录下建插件专属 venv（钉插件
   版本，可复现，绝不碰用户全局环境）——「只装桌面版」的用户一条命令补齐；
   `--health` 输出一行 JSON 体检；
3. **降级 server 不再伪装**：tools/list 只列真的可用的 `tavotto_health`，
   六个工具名的调用回结构化错误（code + 缺什么 + 恢复步骤），不声明资源。
   内嵌画布与桌面窗口/浏览器是两条隔离的路，谁也不冒充谁。
