# ADR 0160：打开项目后在后台预热一条渲染会话，并合并并发的解释器体检

日期：2026-11-02 · 状态：**Proposed**
相关：[0003 worker 协议 v1](0003-worker-protocol-v1.md)、[0013 RuntimeFigureAsset](0013-runtime-figure-assets.md)、[0021 tavotto run 产品契约](0021-tavotto-run-product-contract.md)；`docs/rules/backend/worker-protocol-and-lifecycle.md`、`runtime-figure-assets.md`。
依据：反馈包 TVD-ZWV6 / TVM-Y37X（0.19.2 Windows，微软商店版 Python 3.11，项目在 U 盘）。

## 动机

重启应用、打开上次项目后第一次渲染 `Animal_4_ERSP_raw`（runtime 图）耗时 20.9s / 40.6s，其中 `worker_get_ms` 为 11.3s / 28.6s；热渲染稳定 1.4s。
sidecar 就绪后会话迟了约 20s 才打开，期间另一张图的准备会话（safe 路由）同时在跑，还有 4 路并发的解释器解析。冷启动本身消不掉，只能挪到用户感知之前，并让重复工作不再互相拖慢。

## 决定

1. **预热 = 只取会话**。`POST /api/engine/prewarm {id}` 在后台线程里走 `_engine_worker(id)`（与渲染开头同一扇门，「谁来渲染」仍只在 `enginesession.resolve()`），
   起解释器、开会话，**不 build**（不跑用户脚本）、不出图、不写项目目录、不碰注册表、不发 SSE、不进写回。runtime 图同样只取会话：
   端点只读绝不执行的规则不变，执行仍由第一次真渲染惰性触发（lazy rehydrate 不变）。native 档的 runtime 图不预热（native 会话不进池）。
2. **选图与时机**。项目解释器还没定下来（`pool.peek_project_resolution` 为空）时不预热：取会话会先做「换不换解释器」的决定（写用户配置），不该发生在用户什么都没点的时候。前端叶子组件 `<PrewarmSync />`（不经 React 订阅文档，用 store.subscribe，触发后退订）在项目 `open`、文档就位并再等 1.5s 且浏览器空闲后，为画布上**第一个**带脚本且当时没在渲染的面板发一次（每个项目一次）。
3. **节流**（`engine/prewarm.py`）：同一 (项目, 面板) 本进程一次，失败也不重试；同时最多一路；池里已活 `MAX_ALIVE-1` 个会话时不预热（给前台留位，不触发 LRU 淘汰）；
   前台渲染取会话期间不开始，登记后被前台抢先则放弃且不记「已预热」。失败只记日志，界面零反应。
4. **复用**。用户在预热完成前点这张图：`pool.acquire()` 在池锁上排队，进锁后命中同一条会话（`created` 只有一次调用为 True），不会再起第二个。
5. **前台优先：争用点与改法**。冷启动时渲染、准备会话、预热各自解析解释器，每一路都先跑一遍 `python -c "import matplotlib"`
   （体检结论要等第一个探测落地才进缓存，`pool.resolve_worker_python` / `select_worker_python`）——N 路并发 = N 个同样的子进程抢同一块慢盘与 CPU。
   `pool._has_matplotlib_once` 把同一 (解释器, 启动上下文) 上同时的探测合并为一个，后来者共享**成功的结论**；领头者抛异常时后来者各自重来
   （`CaptureDeferred` 取决于调用线程的锁状态，不能继承）。
   未改的争用点（如实记录）：`acquire()` 在池锁内构造会话，不同脚本的冷启动在池锁上串行；准备会话里的 build 与前台 build 争 CPU，没有可让路的代码点，不硬做。

## 后果

- 冷启动成本不变，只是在用户点图之前发生；点得早则与预热合并为同一次。
- 预热会过与渲染相同的取会话前置，但门只**抛**异常（载荷挂在异常上，由渲染响应带给前端弹窗），不发 SSE、不登记待弹窗：依赖门（`deprepair._spawn_gate`）、工作目录门（`pool._workdir_gate`）要问时预热静默失败，真渲染时才弹。环境决定在项目已有解释器时只复用该决定，未定则不预热。

## 看护

`tests/test_engine_prewarm.py`：只一次 / 失败静默 / 前台让路 / 池满不预热 / native 不预热 / 零副作用（无事件、无写盘、无 build）/ 探测合并 / 真池只建一条会话；
`web/src/lib/prewarm.test.ts`：选图规则与失败吞掉。
