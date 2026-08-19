# ADR 0004：Rust supervisor `tavotto-workerd` 与 supervisor 协议 v1

日期：2026-08-18 ｜ 状态：已采纳（Python 池完整保留为回退与参考实现）

## 背景

worker 协议 v1（[ADR 0003](0003-worker-protocol-v1.md)）把**一条会话上的一次
往返**钉死了：信封、回显、错误 code、patch 身份。它没解决的是**会话之上的调度**，
而那正是 `engine/pool.py` 三个已知洞的所在：

- **stdin 写入阻塞**：`proc.stdin.write()` 在管道满时同步阻塞，而它是在持着
  `w.lock` 的状态下调的。worker 不读 stdin（正在跑一段死循环）时，发请求的线程
  连同整条会话一起挂住。
- **请求排队无上限**：池本身没有队列——并发请求全堵在 `w.lock` 上。用户在慢图上
  连拖十几下，十几次渲染会一条条全部跑完，界面表现为「越用越慢」，而其中只有
  最后一次的结果还有人要。
- **淘汰正在渲染的会话要等锁**：LRU 淘汰与 `invalidate` 走的 `shutdown()` 要抢
  同一把 `w.lock`，正在渲染时就一起卡住，`finally` 里的 `proc.kill()` 永远走不到。

这三条都不是「再加个超时」能治的，它们的共同根因是**控制面与数据面挤在同一把
锁上**。所以把生命周期管理挪进一个独立的 Rust 进程 `tavotto-workerd`：它有真正的
线程模型（每会话一条线程 + 独立的读写线程），杀进程是任何线程随时能做到的事。

## 决定

### 1. 边界：Rust 是机制层，Python 是策略层

| 归属 | 内容 | 出处 |
|---|---|---|
| **Python（策略）** | 解释器优先级与探测、内置 runtime 的 `-B` 与 env 改道、超时档位（BUILD/REQUEST/EXPORT/SHUTDOWN）、会话数上限、队列上限、错误文案与 `missing_dependency` 识别 | `pool.py`、`runtime.py` |
| **Rust（机制）** | spawn / 握手 / 健康检查、generation 隔离、有界队列与最新合并、超时强杀、取消、LRU 淘汰、patch 规范化哈希 | `workerd/src/` |

Flask 把**完整的 spawn 规格**（argv 列表、env 增量、log 路径、握手期限）交过去，
workerd 照做，一个探测都不跑。这条线不许模糊：解释器选择在
`pool._prioritized_candidates()` 里已经是唯一权威，在 Rust 里重写一份就是
制造第二个权威，两边迟早分叉，而分叉的症状是「同一台机器上换个控制面就换了
渲染环境」——几乎无法排查。

crate 在仓库根的 `workerd/`（**不进 `src-tauri/`**，桌面壳保持薄）；
`workerd/target/` 进 .gitignore；wheel/sdist 都不含它（pyproject 的
`[tool.hatch.build] exclude` 显式挡住——sdist 的 `include = ["tests"]` 是
gitignore 风格模式，会把 `workerd/tests/` 一起收进去）。

### 2. supervisor 协议（Flask ↔ workerd）

**与 worker 协议是两套东西**：worker 那条严格串行、一次一请求；这条要被 Flask
的多个线程共用，靠 `request_id` 多路复用，响应**必然乱序**（被顶掉的那条会先于
在飞的那条回来）。

请求（stdin，一行一条）：

```json
{"supervisor_protocol_version": 1, "request_id": "c-17", "op": "render",
 "session_id": "s-3", "stem": "Fig1", "timeout_ms": 300000,
 "payload": {"patches": []}}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `supervisor_protocol_version` | int | 是 | 恒为 1，不符一律 `bad_request` |
| `request_id` | 非空 str | 是 | 每请求唯一；响应原样回显，配对全靠它 |
| `op` | 非空 str | 是 | 见操作表 |
| `session_id` | str | 操作相关 | 哪条会话 |
| `stem` | str | 操作相关 | 作用在哪张图上（顶层，与 worker 协议同构） |
| `timeout_ms` | int | 否 | **超时由调用方携带**——档位是 Flask 的策略 |
| `payload` | object | 否 | 操作参数 |

成功响应：`{"supervisor_protocol_version":1,"request_id":"c-17","ok":true,
"session_id":"s-3","generation":2, …worker 的结果字段…}`；
失败：`{…,"ok":false,"error":{"code","retryable","message","traceback", …}}`。

worker 结果字段（`manifest` / `warnings` / `path` / `stems`）**平铺在顶层**，
`app.py` 拿到的结构与 Python 池一字不差。信封字段（`protocol_version` /
`request_id` / `worker_generation` / `render_revision`）在 workerd 这里被吃掉——
它们是 supervisor 的账本，不该往上层漏。

未知字段两侧都必须容忍并忽略；加字段不升版本，改语义/删字段才升。

### 3. 操作表

| op | payload | 成功响应 | 谁来回 |
|---|---|---|---|
| `hello` | `max_sessions`, `max_queue` | `workerd_version`, `worker_protocol_version`, `pid`, `capabilities` | 主循环 |
| `open_session` | `argv`, `env`, `cwd`, `log_path`, `handshake_timeout_ms`, `label` | `session_id`, `spec_hash`, `pid`（复用时 `reused: true`） | 会话线程 |
| `build` | — | `stems` | 会话线程 |
| `render` | `patches` | `manifest`, `warnings` | 会话线程 |
| `render_png` | `width` | `path` | 会话线程 |
| `preview_png` | `patches`, `width`, `tag` | `path` | 会话线程 |
| `export` | `patches`, `path`, `format`, `dpi` | `path`, `warnings` | 会话线程 |
| `ping` | — | 带 `session_id` 就 ping worker，否则 ping workerd 自己 | 两者 |
| `cancel` | `target_request_id` | `outcome`: `queued_removed` / `in_flight_killed` / `unknown` | 主循环 |
| `close_session` | `force` | `closed` / `released` | 会话线程 |
| `sessions` | — | 会话概况（诊断用） | 主循环 |
| `shutdown` | — | `closed_sessions`，随后进程退出 | 主循环 |

`open_session` 的响应由**会话线程**发：spawn + 握手可能要几十秒（冷启动 +
import matplotlib），占住主循环的话别的会话连一条 ping 都发不出去。

### 4. 会话键 = spawn 规格哈希

会话按 `sha256(canonical_json({argv, env, cwd}))` 索引。argv 里已经带着
(解释器, 脚本, figures-dir, out-dir, sandbox, entry)，所以「换 entry」
「换项目」「换解释器」天然就是另一条会话——workerd 不必重新理解这些概念。
`label` 与 `log_path` **不参与**（观测用的附属信息，改它不该让会话重建）。

同规格重复 `open_session` **复用同一个 worker 并引用 +1**，`close_session`
引用清零才真关（`force: true` 无视引用直接杀）。理由是一个很常见的交叠窗口：
`pool.get()` 建好新的 `EngineWorker` 时，旧的往往还在另一条线程上异步关停。
不做引用计数的话，旧的那次 close 会把刚建好的会话关掉。

反过来，「写回前的干净重放」（`pool.one_shot()`）要的恰恰是**必然不复用**：
它必须是一条从零跑过脚本的新会话，复用热会话就等于什么都没验。这件事**不需要
supervisor 增加任何概念**——一次性 worker 本来就有自己的 out_dir/sandbox
（argv 因此不同），再加一个一次性的 `TAVOTTO_REPLAY_NONCE` salt env 作双保险，
spec 哈希必然不同。用完走普通 `close_session`，引用归零即真关
（`test_workerd_write_back_replays_without_leaking_a_session` 用 `sessions` op
断言写回之后 supervisor 手里只剩热会话那一条）。

### 5. generation：上一代的迟到响应一律丢弃

每次 (re)spawn 把会话的 generation +1，随每条 worker 请求下发（`worker_generation`），
worker 原样回显。**读线程给它读到的每一行都打上自己那一代的号**，会话线程只认
当前代：号对不上的行、垃圾、EOF 全部直接丢弃。

这不是「理论上更安全」：会话被超时 kill 后是**原地重建**的，上一代进程在被杀前
写出的半条响应完全可能晚到，而它带着的是旧 Figure 的 manifest。认下去就是新会话
被旧状态静默污染。`session.rs` 的 `late_responses_from_a_previous_generation_are_dropped`
钉的就是这条。

同一代里回显对不上（或 `protocol_version` 不是 1、或 stdout 上出现非 JSON）=
会话真的错位了：**当场 kill 并报 `protocol_mismatch`**，与 `pool._check_envelope()`
同纪律。

### 6. 队列：有界 + 最新合并 + 不抢占

每条会话一条队列（默认上限 32，Flask 在 `hello` 里给真值）：

- **只有 `render` 合并**，合并键是 `(会话, stem)`：队列里同键至多一条，新的
  **原位替换**旧的（不插队到已经排在前面的 export 之前），被顶掉的**立刻**收到
  `queue_superseded`。不是静默丢弃——那条请求有一个调用线程在等响应。
- **export / 写回类一条都不合并**，按序执行。它们各自有独立产物，合并等于悄悄
  少写一个文件；`preview_png` 按 tag 分文件，顶掉会让某个直方图永远不出现。
- **在飞的不抢占**：worker 没有协作中断（ADR 0003 §6），抢占只能靠杀进程，而
  杀掉一次正在跑的渲染去插队并不划算。
- **队列满立即拒绝**（`queue_full`，retryable），绝不把调用方挂在一条排不上的队上。

**`queue_wait_ms` 的口径在两条控制面上不一样，如实标注**：Python 池里「排队」
就是抢 `w.lock`，量得到；workerd 里真正的排队发生在 Rust 的合并队列中，Flask
侧只能量到「发出请求前自己等了多久」（≈0）。workerd 若在响应里带自己的排队时长
（顶层 `queue_wait_ms`）就**透传优先**，没带就用 Python 侧那个数——所以 workerd
路径上的 0.0ms 只说明「Flask 没等」，**不说明没排队**。本阶段不为此改 Rust
（`session.rs` 对 worker 的结果字段是整体透传的，worker 的 `timings` 自动到位）。
基线与实测见 `docs/perf-baseline.md`。

### 7. 超时 / 取消 / 重启

- **超时**：档位由请求携带。超时即 kill worker 并报 `worker_timeout`；下一条请求
  原地重建（generation +1，重新握手）。错误文案区分「请求已经写进管道」与
  「连写都没写进去」——后者说明对面根本没在读 stdin。
- **写不阻塞会话线程**：每个 worker 一条专用写线程，发请求只是往 channel 里塞一条。
  这是对 Python 池那条洞的**结构性**修复，不是加了个超时。
- **取消**：排队中 → 移出队列并回 `cancelled`；在飞 → **杀 worker**（EOF 让在飞的
  那条收到 `cancelled`），generation 在下次 spawn 时 +1。找不到目标一律回
  `ok` + `outcome: "unknown"`——取消是幂等的尽力而为，与 ADR 0003 §6 的诚实边界一致。
- **淘汰 = kill**：超出 `max_sessions` 按最久未用淘汰，**不等在飞的活跑完**。
  队列里剩下的每一条都会收到 `session_dead`（一条都不静默丢）。
- **崩溃**：worker 进程退出 → `session_dead`；会话本身还在，下一条请求原地重建。
  这是安全的：worker 的每条命令都会 `_ensure_built()`，而 override 是全量列表语义，
  新起的进程只是多跑一次 build。

### 8. 错误码

worker 那五个（`bad_request` / `unknown_cmd` / `unknown_stem` / `script_error` /
`internal`）**原样透传**，`retryable` 与 `traceback` 一个字节都不改——Flask 侧对
`missing_dependency` 的正则识别因此完全不用动。supervisor 层新增：

| code | retryable | 何时 | 调用方该做什么 |
|---|---|---|---|
| `queue_superseded` | false | 队列里被同 stem 的新 render 顶掉 | 什么都不做，新的那条覆盖了它 |
| `cancelled` | false | 被 `cancel` 取消 | 同上 |
| `session_dead` | true | worker 退出 / 被淘汰 / 会话已关 | 重开会话（`pool` 里就是下一次 `get()`） |
| `spawn_failed` | false | 子进程根本没起来 | 修 spawn 规格（路径、权限） |
| `handshake_timeout` | true | 起来了但没在期限内回 ping | 重试；反复出现就是解释器有问题 |
| `worker_timeout` | true | 单次请求超时，worker 已被杀 | 可以重试 |
| `protocol_mismatch` | true | 回显错位 / 版本不符 / 管道上有垃圾 | 会话已重启，可以重试 |
| `queue_full` | true | 有界队列满 | 稍后重试 |
| `unknown_session` | true | session_id 不存在（workerd 重启过） | 重开会话 |
| `unknown_op` / `bad_request` | false | 调用方写错了 | 修调用方 |

`pool.py` 把前七条里状态不可知的那几条（`session_dead` / `spawn_failed` /
`handshake_timeout` / `protocol_mismatch` / `worker_timeout` / workerd 自身故障）
标成会话死亡，`alive()` 转 false，下一次 `get()` 原地重建——与 Python 池的
「超时或状态未知的 worker 一律不复用」是同一条纪律。

`unknown_session` 是唯一被**透明重试**的一条：workerd 重启后 session_id 作废，
`WorkerdWorker._call()` 重开一次会话再发一遍，对上层只表现为一次稍慢的渲染。

### 9. patch 规范化在 Rust 侧的复刻

`workerd/src/patchspec.rs` 必须**逐字节复现** `engine/patchspec.py`，硬验收是
`tests/golden/patch_vectors.json`（两侧共用同一份，`workerd/tests/golden_vectors.rs`
逐组断言 canonical / dropped / canonical_json / hash）。三个坑：

1. **浮点写法**（`workerd/src/pyfloat.rs`）。Rust 没有 `repr(float)` 的等价物：
   `{}` 永不用指数（`1e22` 打成 23 位数字）、`{:e}` 永远用指数且不补零
   （`1e-7` 而不是 `1e-07`）、serde_json 的 ryu 三样都对不上。但**有效数字本身
   是一致的**（两边都给最短且能往返的十进制，半程进偶），所以只需按 CPython
   `format_float_short` 的写法重排：从 `{:e}` 拆出 digits 与 `decpt`；
   `decpt <= -4 || decpt > 16` 用指数；定点形态在看起来像整数时补 `.0`
   （`Py_DTSF_ADD_DOT_0`，于是 `1.0` / `-0.0` 保号）；指数按 `%+.02d` 输出
   （**永远带符号、至少两位**）。
2. **int 与 float 是两个值**。serde_json 必须开 `arbitrary_precision`：没有它，
   超出 u64 的整数字面量会被解析成 f64 并**照单全收**，而 Python 的 `json` 解析成
   int 后按 `int_out_of_range` 剔除——两边的 patch 身份于是静默分叉。开了之后
   按字面量判（含 `.`/`e` 就是浮点），与 Python 的 `json` 完全一致。
3. **字符串转义照抄 `ESCAPE_DCT`**（ensure_ascii=False）：只转义 `"`、`\` 与 C0
   控制字符；`/`、U+2028/2029、DEL 一律原样出字。多转一个字符就是一次哈希分叉，
   这里不许「顺手更安全一点」。

**下发给 worker 的永远是请求里那份原始 patch 列表**，规范化只用来算
`canonical_patch_hash`。worker 的 `hash_mismatch` 标记**只告警不拒绝**（ADR 0003 §4）。

## 影响面与非目标

- **Flask HTTP API 与前端零改动**。`app.py` 只认 `get/override/export/render_png/
  preview_png/svg_path/out_dir/rev` 这套名字，`WorkerdWorker` 与 `EngineWorker`
  同形，切控制面对上层透明。
- **Python 池完整保留**，`pool.py` 里那条路径一行都没删：它是找不到二进制时的
  回退，也是 workerd 行为的参考实现（reference oracle）。开关是
  `TAVOTTO_WORKERD`（`0`/`off`/`false`/`no` = 禁用，其余非空值 = 指定路径；
  开发态自动找 `workerd/target/{release,debug}/`）。**pytest 的 conftest 默认把它钉成
  `0`**——否则开发机上 `cargo build` 之后整套既有用例会在不知不觉间换一条控制面跑。
- **不做的**：真正的协作中断（worker 单线程串行读 stdin，见 ADR 0003 §6）、
  流式进度、一条会话上并行多请求（要 worker 变成多线程/多进程）、把渲染或解释器
  探测搬进 Rust。

## 看护

- `workerd/tests/golden_vectors.rs`：**硬验收**，golden vectors 逐组逐字节。
- `workerd/src/pyfloat.rs` 的单测：`repr(float)` 逐字节 + 2 万次随机往返。
- `workerd/src/session.rs` 的单测：上一代响应丢弃、同代错位报 `protocol_mismatch`、
  EOF 的 `session_dead`/`cancelled` 分支、信封形状。
- `workerd/tests/supervisor_behaviour.rs`：真二进制 + `tests/fake_worker.py`
  （说 v1 协议的小脚本）——握手、合并与顶替、export 不合并、有界拒绝、超时重建、
  取消两态、LRU 淘汰、退出无孤儿。
- `tests/test_workerd_client.py`：多路复用（先发的慢、后发的快，各回各的）、
  崩溃时 pending 立刻失败、重启与上限、二进制发现。
- `tests/test_workerd_pool.py`：选路与回退、**spawn 规格与 Python 池的 Popen
  argv 同源**、错误码到会话死亡的映射、超时档位随请求下发。
- `tests/test_worker_roundtrip.py` 末节：真 workerd + 真 matplotlib 的全链路
  （渲染 / 全量列表还原 / 导出状态中立与矢量文字 / 超时重建），缺二进制整组跳过。
