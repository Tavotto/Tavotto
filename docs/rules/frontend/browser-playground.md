# 浏览器 playground（网站 /try，2026-08-21）

> 原文出自 `web/AGENTS.md`「浏览器 playground（网站 /try，2026-08-21）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0007-browser-playground.md` 与
`docs/adr/0011-playground-examples-first.md`，改动前先读。引擎侧
（`engine/browser.py` 的平铺 import 纪律与 ENGINE_FILES 白名单）见
`src/tavotto/AGENTS.md` 末节。

- 前端走 `engineTransport` 的第三条传输（`web/src/playground/`），画布 /
  inspector / stores / undo 与桌面同一份；MCP 与 playground 共用的种子层在
  `web/src/embedded/session.ts`。
- **Pyodide 版本与包白名单钉死在 `packaging/playground-runtime.json`**（唯一
  权威；前端 JSON import + 构建脚本共读）。不自动装任意 PyPI 包；不支持的
  import 在下载科学栈**之前**报 `unsupported_import`（`engine/browser_imports.py`
  纯标准库，分类必须先于 matplotlib 下载）。
- **超时与取消在 Worker 边界**：任意同步 Python 没有协作取消，到点
  `worker.terminate()` 且**会话作废**；一个文件 = 一个 Worker，换文件不复用
  解释器。主线程只接受 id 配对 + 形状合法的 Worker 消息（Python 摸得到
  postMessage）。
- **隐私是可验证的**：源码只进 Worker，不进 localStorage / 不出网
  （e2e 哨兵测试盯着）。
- **「figure.py · 未改动」是两个真哈希比出来的**（2026-08-21）：主线程用
  Web Crypto 算原文的 sha256，Worker 侧用 `pyodide.FS.readFile` 把
  `/workspace/<脚本>` 的字节读出来再用 Web Crypto 算一次，两个数相等才显示
  「未改动」。**别退回 `loadedSource === originalSource` 那种写法**——两个
  变量指向同一个 JS 字符串，恒真，什么也没证明。
  **权威摘要必须在用户的 Python 解释器之外算**（`pyodide.worker.ts` 的
  `fsDigest`）：用户脚本跑在同一个解释器里、而且跑在核对之前，改完自己的文件
  再 monkeypatch `builtins.open` / 换掉 `hashlib.sha256` / 改
  `sys.modules['browser']` 的全局，就能让 Python 侧继续回报原摘要——
  **一个能被它所校验的代码改写的校验不叫校验**（e2e 原样跑那个场景，
  把摘要挪回 Python 就红）。`browser.py` 的 `source_status` 保留，验的是
  引擎语义、跑在 Pyodide 之外，不是重复。写文件必须**二进制**——文本模式在
  Windows 上翻译换行，比对永远 mismatch（只有 CI 的 windows 腿逮得到）。
  **`import js` 必须够不着**（`loadPyodide` 的
  `jsglobals: Object.create(null)`，**无原型是硬要求**——普通 `{}` 上
  `constructor.constructor('return globalThis')()` 就是一台 Function 构造器）
  ——这是上面那条成立的前提：Python 拿到 `js` 就能 `js.eval` 改 Worker 任何
  全局，连 `self.postMessage` 伪造整条响应都做得到（请求 id 自增、猜得到），
  那时**这个 Worker 里没有任何东西可信**。静态分类不是防线：
  `browser_imports` 有意放行 try/except 里的可选 import，`__import__('js')`
  它更看不见。可信原语（digest / Uint8Array / FS 读取）一律在模块求值期与
  init 期绑定好，是纵深防御。两道防线各有判据，少一道都有用例红。
  **定位是「查意外，不是防蓄意」**：`pyodide_js` 是 Pyodide 的基础设施、删不掉，
  而 `pyodide_js.constructor.constructor("return globalThis")()` 实测能拿到
  Worker 全局——只要用户 Python 与验证代码同在一个 Worker，蓄意规避总是做得到。
  按模块名封堵是打不完的地鼠，「挪到独立 Worker 验」也不成立（虚拟 FS 就在
  被攻陷的那个 Worker 里）。**界面上不许出现比这更强的说法**，源码面板的
  完整性明细里已经写明。
  Worker 侧的哈希在**脚本跑完之后**采；复验走独立的轻命令、**只在 worker
  闲着时发**（无阶段请求超时 30s，排在慢渲染后面到点 = 整个会话被
  terminate）。UI 四态：没验完不许说「未改动」，算不出哈希是「查不了」
  不是「没改」，不相等按不变式失效常驻报警。
- **案例优先，上传是次级入口**（2026-08-25，ADR 0011）：idle 首屏的主角是
  案例库（三张构建期真实执行生成的 Figure 封面卡 + 中央试验台），上传降级
  为底部「已有一个独立脚本？」，单文件边界在上传前写明。案例源码唯一真源
  是 `web/src/playground/examples/*.py`（examples.ts 走 vite `?raw`，
  **别在 TS 里抄第二份 Python**）；封面由
  `scripts/generate_playground_examples.py` 在钉死的 matplotlib 版本下真实
  执行生成，manifest 记源码 sha256——改了 .py 不重新生成封面，`--check` /
  examples.test.ts / 构建指纹三道闸都是红。**封面只用于卡片展示**：五条
  启动路径（拖入试验台 / 开始体验 / Enter / Code Sheet / 触屏点击）全部走
  `openSource()` 真执行，**不许用预烤 SVG/manifest 提速**。`EXAMPLES` 里
  **有且只有一个** `featured/starter`（examples.test.ts 看护）。拖拽只认
  鼠标指针（Pointer Events 自实现，不引框架），触屏和键盘走点击/Enter，
  reduced-motion 下不位移不缩放、只靠边框与文字表达。会话来源
  （example/upload）进状态机；首次引导只对内置案例出现且**只观察不代劳**
  ——完成语「一个字也没动」必须来自 verifySourceIntegrity 的真结论。
  加载可取消：`startSession` 的 `onClient` 交出在途 client，取消 = 真
  dispose，绝不并行两个 Worker。三个案例都在 savefig 前 `tight_layout()`：
  默认边距在这个 figsize 下会把 x/y 轴标签整条裁掉，而轴标签正是访客
  第一件想点的东西。
- **`/try` 空闲时预热 Pyodide 核心**（`web/src/playground/prewarm.ts`）：
  **只到核心 + engine.zip 为止**，科学栈仍等 import 分类说了话才下载
  （e2e 断言预热窗口里 wheel 零条）；`saveData` 或 `slow-2g/2g` 不预热，
  Network Information API **一律特性检测**（Safari/Firefox 上它整个不存在）；
  `PlaygroundClient.init()` 幂等去重，「预热中点了示例」接的是同一个在途
  Promise，**不会变成两个 Worker**；暖着的 Worker 还没跑过用户代码，所以可以
  当第一个会话用——**「一个文件 = 一个 Worker」没有松动**。预热是优化不是
  依赖：失败悄悄退回 cold，绝不在用户动手之前弹错误。营销首页
  （`/`、`/zh/`）**一个字节的 Pyodide 都不加载**，那是网站仓库的静态页面。
- 产物：`python scripts/build_browser_playground.py` → `web/dist-playground/`
  （确定性 engine.zip + 指纹 manifest，指纹算法复用 build_mcp_widget.digest）。
  网站仓库 `pnpm sync-playground` 收走并提交、`pnpm check-playground` 防漂移
  ——改了 web/src 或引擎四模块，**playground 与 MCP 画布两个产物都要重建**。
- 验证：`tests/test_browser_session.py`（CPython 上跑同一份 browser.py）+
  `web/src/playground/*.test.ts` + `web/e2e/playground.spec.ts`
  （真浏览器 + 真 CDN Pyodide，慢，专属放宽超时）。
  **篡改钩子只在测试驱动里，产品代码不给任何改工作区源文件的入口**。
