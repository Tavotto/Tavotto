# ADR 0007：浏览器 playground——把 Python 搬进浏览器，编辑器一行不动

状态：已实施（2026-08-21）
相关：[0003 worker 协议 v1](0003-worker-protocol-v1.md)、
[0006 Codex MCP App 画布](0006-codex-mcp-app-and-publication-profile.md)

## 背景

网站（tavotto.com）能展示 Tavotto，但访客要**体验**语义化改图，得先下载安装。
我们想要的是：打开 `tavotto.com/try`，拖进一个普通的 Matplotlib `.py`，
它在浏览器里跑起来，然后用 Tavotto 真正的编辑器点中标题、拖走图例、改字号
——源文件一个字节不动。不是录屏、不是预烤的假 demo、不是服务器代跑。

## 决策

**用 Pyodide（WebAssembly 的 CPython）在 Dedicated Web Worker 里本地执行
用户脚本，前端经一条新的 `EngineTransport` 复用 Tavotto 既有的画布与引擎
语义。** 新东西只有「执行环境」；编辑器、manifest、override、patch 表示、
undo，全部是原来那一份。

### 架构（第三条传输，不是第二套实现）

`web/src/lib/engineTransport.ts` 的可替换传输层是 ADR 0006 留下的接缝，
现在有三个实现：

    桌面 / 浏览器模式      画布 → HTTP → Flask → EngineWorker
    Codex MCP App 画布     画布 → tools/call → MCP server → 同一个 EngineWorker
    浏览器 playground      画布 → Worker RPC → Pyodide → 同一份 manifest/overrides

共享且**必须继续共享**的：CanvasStage、命中测试、拖拽、吸附、
ElementInspector、ElementTree、全部 zustand stores、patch 表示、undo/redo、
`useEngineSync`、i18n。种子逻辑抽成 `web/src/embedded/session.ts`，
MCP 会话与 playground 会话共用（不许复制后各自漂移）。

Python 侧同理：`engine/browser.py` 是**适配层不是分叉**——它平铺 import
的 `manifest.py` / `overrides.py` / `pathgeom.py` / `patchspec.py` 与桌面
worker 是同一份文件（worker 式的「engine 目录进 sys.path」布局原样保留，
见 CLAUDE.md 对平铺 import 的约定）。禁止出现 `browser_manifest.py` 这类
复制品。`browser_imports.py` 单独成模块是因为它必须在「决定下不下载
matplotlib 那十几 MB」之前跑，而 `browser.py` 模块级就 import matplotlib。

### 运行时与包

* Pyodide 版本钉死在 **`packaging/playground-runtime.json`**（唯一权威：
  前端经 vite 的 JSON import 读它，构建脚本把它写进产物 manifest）。
  当前 314.0.5 / Python 3.14.2，从官方 jsDelivr CDN 拉（方案 A：产物小、
  浏览器可缓存；自托管留作后续选项）。**不许 latest / dev / 浮动版本。**
* 支持的包是**确定性白名单**：matplotlib / numpy / pandas / scipy / pillow
  （全部是 Pyodide 官方分发内置，版本随发行版钉死）。不按 import 自动装
  任意 PyPI 包，不支持 `micropip` 代装——性能、兼容与隐私都不可预测。
  seaborn 不在 Pyodide 内置分发里，所以**不宣传支持**。
* 脚本 import 先做 ast 静态分类（`browser_imports.classify_imports`）：
  stdlib / 支持 / 不支持。不支持的在**下载任何科学栈之前**拒绝，给结构化
  `unsupported_import` + 桌面版出口。`try/except ImportError` 里的可选
  import 不算阻断。

### 入口层级：示例与上传平级（2026-08-21 补）

空状态原来是「拖放区 + 一行『或者试个示例』+ 三颗小 chip」——示例读起来像
退路。但**多数第一次来的访客手边并没有一个现成的 `.py`**，而 Tavotto 想让人
理解的东西一次点击就能看到。所以改成两条平级的路：拖放区 → `或者` 分隔线 →
一个填色的主 CTA「直接试一个示例」（跑 `EXAMPLES` 里唯一标了 `primary` 的
那张），其余示例退成次级文字入口。

* 主示例是 `kinetics.py`：标题 / x-y 轴标签 / 两条曲线 / 图例齐全，点开就有
  东西可选可拖，又不至于第一眼看不懂。**有且只有一个 primary**
  （`examples.test.ts` 看护）——主路径指得到两个地方就不叫主路径了。
* 三个示例都在 `savefig` 前 `tight_layout()`：matplotlib 的默认边距在这个
  figsize 下会把 x/y 轴标签整条裁掉（实测三张全中）。轴标签恰恰是访客第一件
  想点的东西，裁掉了既难看又点不着。
* **仍然是真执行**：点下去走的是 `Browser File 等价的源码 → Pyodide →
  matplotlib → manifest → 真编辑器`。不许为了快用预烤的 SVG/manifest——
  那一刻演示的就不是这个产品了。

### 预热：`/try` 空闲时把 Pyodide 核心装起来（2026-08-21 补）

打开 `/try` 的人意图已经很明确，闲置的那几秒拿来装运行时是纯赚。
实现在 `web/src/playground/prewarm.ts`，一台**至多一个**的暖机账本
（`cold → warming → ready`，取走即回 cold）。

* **营销首页一个字节的 Pyodide 都不加载**——那是网站仓库里的静态页面，
  与本模块没有任何连接；预热只发生在 playground 这个应用页面上。
* **只到核心为止**：`init` = Pyodide 核心 + `engine.zip`。科学栈仍然要等
  `browser_imports` 分类说了话才下载（实测预热窗口里的 CDN 请求正好是
  `pyodide.mjs` / `pyodide-lock.json` / `python_stdlib.zip` /
  `pyodide.asm.wasm` / `pyodide.asm.mjs` 五条，**wheel 零条**）。
* **尊重省流量与慢网**：`navigator.connection?.saveData` 为真、或
  `effectiveType` 是 `slow-2g`/`2g` 就不预热。一律特性检测——Network
  Information API 是 Chromium 专有的，直接读会在 Safari/Firefox 上抛。
* **只有一条初始化路径**：`PlaygroundClient.init()` 幂等去重，预热中点了
  示例接的是**同一个在途 Promise**，不会变成两个 Worker。
* **「一个文件 = 一个 Worker」没有松动**：暖着的那个还没跑过任何用户代码，
  所以它可以当第一个会话用；跑过脚本的解释器换文件时照旧 terminate 重建。
* **预热是优化不是依赖**：失败悄悄退回 cold 并收掉半死的 Worker，用户真开
  会话时按正常路径重来，**不弹任何错误**（在他还没动手之前弹一个全屏错误
  是最糟的形态）。
* 实测（12 Mbps / 40ms 节流、冷 profile、开关各两轮）：点击 → 编辑器可见
  从 15.4/15.9s 降到 10.4/10.4s。缓存已热时 3.4s → 2.8s。

### 源文件完整性：一个可验证的不变式（2026-08-21 补）

界面上那句「`figure.py` · 未改动」原来的根据是
`session.loadedSource === session.originalSource`——两个变量指向同一个 JS
字符串，恒真，什么也没证明。现在的根据是两个隔着 Worker 边界算出来的 sha256：

    主线程   crypto.subtle.digest('SHA-256', TextEncoder(原文))
    Worker   pyodide.FS.readFile(/workspace/<脚本>) → crypto.subtle
             （`pyodide.worker.ts` 的 fsDigest，**在 Python 之外**）

* **权威摘要必须在用户的解释器之外算**，这是这条不变式成立的前提。用户脚本
  跑在**同一个** Python 解释器里、而且跑在核对**之前**，它改完自己的文件再
  monkeypatch `builtins.open`、换掉 `hashlib.sha256`、或直接改
  `sys.modules['browser']` 的全局，就能让 Python 侧的 `source_status` 继续
  回报原摘要——界面于是宣称「未改动」，而实际执行的文件已经变了。
  **一个能被它所校验的代码改写的校验，不叫校验。** 所以字节由
  `pyodide.FS.readFile` 直接从 Emscripten FS 取、摘要由 Worker 的 Web Crypto
  算，全程不经过用户够得着的 Python 名字空间（`import js` 这类反向逃逸由
  `browser_imports` 在执行前就拦掉）。这条有 e2e 原样跑那个场景钉着，
  **把摘要挪回 Python 就会红**。
* `engine/browser.py` 的 `source_status` 保留并继续被 CPython 测试盖着：
  它验的是**引擎语义**（写进去的就是收到的、脚本跑完还是那份），跑在 Pyodide
  之外、没有那个威胁模型。两者要的东西不同，别把其中一个当重复删掉。
* Worker 侧的哈希在**脚本跑完之后**采：要证明的是「实际被 `runpy` 执行的
  那个文件此刻仍与你给的一模一样」，写进去就立刻算等于只验了一次 write。
* 写文件必须**二进制**（`open(path, "wb")`）：文本模式在 Windows 上把 `\n`
  翻成 `\r\n`，磁盘上的字节不再是用户给的那份，比对永远 mismatch。Pyodide
  的 Emscripten FS 恰好不翻译，所以只有 CI 的 windows 腿逮得到——
  **一个只在别的平台上成立的不变式不算不变式。**
* 复验走一条独立的轻命令 `source_status`（不搭渲染的顺风车，每次都真的
  重新读文件重算——缓存一个「上次算过的」哈希就又回到了自证）。触发时机：
  加载完 / **第一次改完并画出来之后** / 打开源码面板。不必每次指针事件都验。
* 复验**只在 worker 闲着的时候发**：无阶段请求的硬超时是 30s，排在一次慢
  渲染后面就可能到点，而到点等于整个会话被 terminate——为一条状态指示把
  用户的编辑现场炸掉是本末倒置。
* UI 四态 `checking / unchanged / changed / unavailable`：**没验完不许说
  「未改动」**；算不出哈希（非安全上下文没有 `crypto.subtle`）是「查不了」
  不是「没改」；`changed` 是不变式失效，按高严重度常驻横幅报出来并附上两个
  短哈希，不是一条可关闭的提示。
* 看护：`tests/test_browser_session.py`（写进虚拟 FS 的就是输入、改完图还是
  输入、**被动过一个字节就必须报出来**——篡改钩子只在测试驱动里，产品代码
  不给任何改工作区源文件的入口）+ `sourceIntegrity.test.ts`（对着 Python
  hashlib 的已知向量）+ e2e（展开的完整性明细必须等于在 node 里对同一份
  源码算出来的短哈希）。

### 边界（Phase II 刻意收窄）

* **一个 `.py` 文件**（≤256 KiB）。不支持项目目录、数据文件、伴生模块——
  UI 明说，`FileNotFoundError` 翻译成「浏览器版只收单文件，桌面版开整个
  项目目录」，不让用户读裸 traceback。
* 无服务器执行、无账号、无持久化：会话只活在内存里，源码**不进**
  localStorage / IndexedDB，刷新即重置。
* playground 是一个独立的产品界面，所以顶栏的品牌就是**回站入口**，
  按当前界面语言落到 `../`（en）或 `../zh/`（zh）——中文访客不该被送回
  英文首页。用相对路径而不是写死 `/`：产物挂在 `/try/` 下（vite 的
  `base: './'`），相对路径在任何前缀下与独立托管时都指得对。
* 不做代码写回、不做完整出版导出（那是桌面链路）；编辑只存在于 override 层，
  界面以「`figure.py` · 未改动」作证——而那句话是上面那条**跨边界哈希比对**
  的结论，不是一个断言。
* Worker 生命周期：**一个文件 = 一个 Worker = 一个 Pyodide 会话**。换文件
  terminate 旧的、起新的——不跨文件复用解释器状态，也是从坏 Python 状态
  恢复的唯一可靠办法。
* **硬超时在 Worker 边界**：任意同步 Python 没有协作取消，到点
  `worker.terminate()`，会话作废（按阶段计时：下载阶段宽、脚本执行 20s、
  编辑期请求 30s；见 `pyodideClient.ts` 的 PHASE_TIMEOUT_MS）。超时后
  不假装会话还能用——给「重新运行 / 换文件 / 去桌面版」。

### 执行语义（照抄桌面 worker，外加一条 pyplot 兜底）

拦截 `Figure.savefig` 按 stem 捕获（build 期不写用户输出文件）、
`sys.argv = [脚本自己]`、`runpy.run_path(run_name="__main__")`、
cwd 与 `__file__` 落在 `/workspace`。脚本跑完再扫一遍 pyplot 的活 figure
（按对象身份去重）——`plt.plot(...); plt.show()` 这类从不 savefig 的脚本
也能用。0 张图如实说、1 张直接开编辑、多张出真缩略图选择器。
`preview_png` 与桌面 worker 同一条「状态中立」纪律。

### 安全模型（如实，不多不少）

访客的任意 Python 跑在 **Pyodide → Dedicated Worker → 浏览器同源模型**里。
这不是 OS 级沙箱，不这么宣传。Worker 拿不到页面 DOM；页面 JS 里没有任何
secret 可偷（纯静态站）。Python 摸得到 Worker 的 postMessage，所以主线程
**只接受 id 配对 + 形状合法的消息**（`protocol.ts` 的 `isWorkerResponse`），
来路不明的一律丢弃、绝不进 innerHTML。隐私承诺是可验证的那条：
**Tavotto 自己不上传源码**（e2e 有哨兵测试盯着「任何请求里都不出现源码
内容」）；Pyodide 运行时与包来自 CDN，这一点在界面上明写。

### 产物与防漂移

`scripts/build_browser_playground.py` 产出 `web/dist-playground/`
（index.html + 哈希资源 + **确定性 engine.zip** + `playground-manifest.json`）。
manifest 记录：源码指纹（算法复用 `build_mcp_widget.digest`，盖住 web/src
全部前端源码 + 进 zip 的每个引擎模块 + 运行时锁 + 规范文件）、产品 commit、
Pyodide/包版本、逐文件 sha256。

网站仓库（Tavotto_website）`pnpm sync-playground` 把产物拷进 `public/try/`
并提交；`pnpm check-playground` 两道校验：① 提交的文件 vs manifest 哈希
（抓手改）；② 有本地产品仓库时调 `--fingerprint` 重算比对（抓「产品改了
没重新同步」）。**过期但还能跑的 playground 比构建失败更坏**——与
build_mcp_widget / sync-product-assets 同一条纪律。

### 为什么不是……

* **服务器跑 Python**：任意代码执行的基建与安全负担、与本地优先的定位冲突、
  这个阶段根本不需要。
* **用 JS 重写一个「像 Tavotto」的编辑器**：manifest / override 语义会出现
  第二份实现，然后必然漂移。ADR 0006 已经证明画布只依赖传输接缝。
* **网站仓库自己实现**：产品代码归产品仓库；网站只是分发与展示层。
  两边靠指纹 manifest 缝合，不靠手拷贝。

## 验证

* `tests/test_browser_session.py`：fixture 矩阵（折线/图例/散点/标注/刻度/
  fill_between/色条/pandas/scipy）逐条「跑通 → manifest → 真 override →
  空列表还原」+ 错误分诊 code + 跨进程 patch_hash 一致（§不许移植第二份
  规范化）。跑在 CPython 上——语义与解释器无关，Pyodide 特有部分归 e2e。
* `web/src/playground/*.test.ts` + `web/src/embedded/session.test.ts`：
  RPC 形状闸门、超时=会话作废、传输映射、示例纯净度、种子层契约；
  `mcp/session.test.ts` 原样全绿 = 共享层重构没动 MCP 行为。
* `web/e2e/playground.spec.ts`：**真浏览器 + 真 CDN Pyodide** 的黄金路径
  （示例主 CTA → 预热只拉核心零 wheel → 语义拖标题 → pos_frac override →
  Pyodide 重渲染 → 撤销还原 → 源码未改动 + 真哈希对得上）、哨兵防泄漏、
  saveData 下零预热、品牌回站按语言、unsupported_import 在包下载前拒绝、
  死循环被硬超时杀掉。冷缓存 ~45s、热 ~10s，专门放宽这一个 spec 的超时。
* CI（ci.yml frontend）：真跑一遍构建脚本；产物过期的门禁在网站仓库。
