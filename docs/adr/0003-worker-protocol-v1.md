# ADR 0003：版本化 worker 协议 v1 + patch 规范化契约

日期：2026-08-18 ｜ 状态：已采纳（worker 双栈兼容，`pool.py` 已切 v1）

## 背景

渲染 worker 与 Flask 父进程之间是一条 stdin/stdout 的 JSON 行协议
（`engine/worker.py` ↔ `engine/pool.py`）。它一直没有版本号、没有请求标识、
错误只有一句字符串：

- **没有请求标识**：管道是串行的，靠「写一行读一行」的隐式配对。worker 少回
  或多回一条，之后每一条响应都错位——A 图的 manifest 落到 B 图上，而且**不报错**。
- **没有版本号**：任何字段变更都是猜谜；第三方实现（下一阶段的 Rust
  supervisor `magplot-workerd`）无从判断对面说的是哪一套。
- **错误没有 code**：父进程靠正则从 traceback 里认 `missing_dependency`，
  其余一律当成「渲染失败」。
- **patch 列表没有身份**：同一份修改可以有无数种等价写法（顺序不同、
  同一 (gid, prop) 写两遍、夹带脏条目），无法判断「这两次渲染是不是同一件事」，
  缓存命中与幂等重放都无从谈起。

下一阶段由 Rust supervisor 接管 worker 生命周期（拉起 / 超时 kill / 重启 /
路由请求）。**它必须与 Python 侧逐字节对齐**，所以先把契约在 Python 两侧钉死，
再让 Rust 照着实现。

## 决定

### 1. 请求信封（v1）

```json
{"protocol_version": 1, "request_id": "r-8f3c…", "worker_generation": 3,
 "render_revision": 17, "cmd": "render", "stem": "Fig1",
 "canonical_patch_hash": "sha256:…", "payload": {"patches": []}}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `protocol_version` | int | 是 | 恒为 1。**没有这个字段 = legacy 信封**（见下） |
| `request_id` | 非空 str | 是 | 每请求唯一；响应必须原样回显 |
| `worker_generation` | int | 否 | 第几代 worker（同一池键每重建 +1） |
| `render_revision` | int | 否 | 调用方的渲染版本号 |
| `cmd` | 非空 str | 是 | 见命令表 |
| `stem` | 非空 str | 命令相关 | 作用在哪张图上；**顶层，不在 payload 里** |
| `canonical_patch_hash` | str | 否 | `payload.patches` 的规范哈希，供 worker 自检 |
| `payload` | object | 否 | 命令参数（缺省 `{}`） |

`stem` 与命令参数分开是有意的：前者是「作用对象」，跨命令同构；后者一命令一
形状。混在一起的话，Rust 侧要为每个命令重复处理同一个字段。

未知字段两侧都必须**容忍并忽略**——加字段不升版本，改语义/删字段才升。

### 2. 命令表

| cmd | payload | 成功响应（除回显字段外） |
|---|---|---|
| `ping` | — | — |
| `build` | — | `stems: {stem: {size_mm}}` |
| `render` | `patches` | `manifest`, `warnings` |
| `render_png` | `width` | `path` |
| `preview_png` | `patches`, `width`, `tag` | `path` |
| `export` | `patches`, `path`, `format`, `dpi` | `path`, `warnings` |
| `cancel` | `request_id`（要取消的那条） | `cancelled`, `seen`, `note` |
| `shutdown` | — | 无响应（进程退出） |

`render` 就是老的 `override`（应用全量 override 列表 + 重出预览 SVG 与
manifest）。池方法名仍叫 `override`（`app.py` 一路这么叫），**线上名统一
`render`**——命令表是给 Rust 看的，那边没有历史包袱，不该背我们的旧名字。
映射表在 `pool._V1_CMD`，是唯一出处。

`render_png` / `preview_png` / `export` 之外的图内语义（override 的全量列表
语义、几何应用顺序、状态中立）**一概没变**，见 CLAUDE.md「渲染引擎核心机制」。

### 3. 成功响应

```json
{"ok": true, "protocol_version": 1, "request_id": "r-8f3c…",
 "worker_generation": 3, "render_revision": 17,
 "canonical_patch_hash": "sha256:…", "manifest": {…}, "warnings": []}
```

`worker_generation` / `render_revision` / `canonical_patch_hash` 是**原样回显**：
worker 不理解它们，也不该理解——它们是 supervisor 的账本（哪一代、第几版、
哪一份 patch），worker 插手就多一个可能与账本不一致的地方。校验归 supervisor。

**generation 是干什么的**：会话被超时 kill 后原地重建，上一代的迟到响应必须
能被认出来丢弃，否则新会话会被旧 manifest 污染。`pool._next_generation()` 按
(项目, 脚本) 池键单调递增，是唯一出处。

### 4. patch 哈希自检（`hash_mismatch`）

worker 自己也对收到的 `payload.patches` 算一遍规范哈希。与请求里带的不一致时：
响应加 `"hash_mismatch": true` 与 `"worker_patch_hash"`，stderr 记一条警告，
**照常执行**。

不拒绝是刻意的：分歧只可能来自另一种语言的序列化实现（就是 Rust supervisor），
当场拒绝会把一个可观测的对齐问题变成一次用户可见的渲染失败。标记 + 警告让上层
发现并去修序列化，用户什么都不会损失。

### 5. 错误响应

```json
{"ok": false, "protocol_version": 1, "request_id": "r-8f3c…",
 "worker_generation": 3, "render_revision": 17,
 "error": {"code": "unknown_stem", "retryable": false,
           "message": "stem 不存在: nope", "traceback": "", "known": ["Fig1"]}}
```

（错误信封同样回显 generation/revision——是成功信封的超集，方便 supervisor
把失败也对回请求。）

| code | retryable | 何时 | 调用方该做什么 |
|---|---|---|---|
| `bad_request` | false | 信封缺字段/类型错、payload 参数非法 | 修调用方，重试无意义 |
| `unknown_cmd` | false | cmd 不在命令表（响应带 `known`） | 同上 |
| `unknown_stem` | false | 该 stem 不在本会话（响应带 `known`） | 换 stem 或先 build |
| `script_error` | false | 用户脚本跑不起来（build 阶段） | 报给用户改脚本 |
| `internal` | true | worker 自己也不知道为什么 | 重启后可以重试一次 |

`retryable` 只有 `internal` 是 true——这是唯一「换个环境重来一次可能就好了」
的一类。`script_error` 重试多少次都是同一个 traceback。

**`missing_dependency` 优先于协议 code**：脚本 `import rdkit` 而当前渲染环境
没有，在 worker 那里只是一个普通的 `script_error`，但对用户是完全不同的一件事
（有可执行出口：换成自己的环境）。`pool._error_of()` 先按 traceback 正则认它，
认出来就覆盖 code——这条行为是既有的，v1 没有改。

`build` 阶段的任何异常一律归 `script_error`（含 mkdir / 预览落盘的 I/O 失败）。
把它们分开需要猜 traceback 的来源，猜错比归错更难排查；真正的原因永远原样
带在 `error.traceback` 里。

### 6. cancel 的诚实边界

**`cancel` 是尽力而为的幂等 no-op，永远回 ok。**

worker 是单线程串行读 stdin：正在跑 build / export 的时候它**根本读不到**
cancel；等读到了，那条请求早已结束。所以响应里 `cancelled` 恒为 `false`，
`seen` 说明那个 request_id 是否在最近 64 条记录里，`note` 是给人看的解释。

**真正的硬取消 = supervisor kill 掉进程并重启**（`pool` 的超时路径就是这个
语义：超时或状态未知的 worker 一律 kill，下一次 `get()` 原地重建）。
不许在协议层假装能中断 matplotlib——一个「取消成功」的假象比没有取消更糟：
调用方会以为图已经停在某个状态，而它其实还在跑。

### 7. legacy 兼容

**无 `protocol_version` 字段 = legacy 信封，行为一字不改**：老的扁平请求
（`{"cmd":"build"}` / `{"cmd":"override","stem":…,"patches":…}`）得到老的扁平
响应（`{"ok":true,"manifest":…}` / `{"ok":false,"error":"…","known":[…]}`）。

手工排障（`echo '{"cmd":"build"}' | python worker.py --script …`）与任何还没
切过来的调用方靠它。两条路走的是**同一份命令原语**（`Worker._do_render` 等），
不是两份实现——否则迟早只修一边。

行本身解析不出 JSON 时按 v1 错误形状回（`request_id: null`）：连信封都没有，
无从判断对方说的是哪套协议，至少 code 是可读的。

### 8. patch 规范化与哈希（`engine/patchspec.py`）

**唯一权威实现**，纯标准库，父子进程共用同一个文件（worker 把 engine 目录
塞进 sys.path 后 `import patchspec`）。三条规则：

1. **形状校验**：条目是对象，`gid`/`prop` 为非空字符串，`value` 是 JSON 值
   （null / bool / i64 范围内的整数 / 有限浮点 / 字符串 / 数组 / 键为字符串的
   对象；嵌套深度 ≤ 32）。不合规的条目由
   `canonicalize_with_diagnostics()` 连原因一起交出来——**静默丢一条 patch，
   用户看到的是「我改了但没生效」，没有任何线索**。条目上多出来的键忽略
   （不参与身份）。
2. **去重 last-wins**：同一 (gid, prop) 只留最后一条（与 `overrides.apply`
   建表同语义）。
3. **排序**：按 (gid, prop) 字典序。Python 的码点序与 Rust 的 UTF-8 字节序
   在合法字符串上等价，两边不必特殊处理。

`canonical_json()`：`sort_keys=True`（**递归**，嵌套对象的键同样排序）+
`separators=(",",":")` + `ensure_ascii=False` + `allow_nan=False`。
`patch_hash()` = `"sha256:" + sha256(canonical_json.encode("utf-8")).hexdigest()`。

**规范序只决定身份，不决定应用顺序**：worker 应用的永远是请求里那份**原始**
列表，几何优先级（size_mm → 子图 position → 其余按列表序）是
`overrides.apply` 内部的事。把规范序拿去应用会静静改掉渲染结果。

#### Rust 侧必须对齐的点（浮点是重灾区）

golden vectors 在 **`tests/golden/patch_vectors.json`**（11 组，含空表 / 单条 /
last-wins / 乱序等价 / 非法剔除 / 中文·µ·⁻¹ / 浮点 / 嵌套 / 标量 / 排序）。
文件里每组给了 `input` / `canonical` / `dropped` / `canonical_json` / `hash`，
`tests/test_patchspec.py` 逐组断言。**Rust 实现必须逐字节复现；改任何一侧都
必须同步另一侧并更新向量文件。**

已知的跨语言坑（向量里都钉住了）：

- 指数写法：Python 写 `1e+22` / `1e-07`，Rust 的 ryu 默认写 `1e22` / `1e-7`。
- `-0.0` 必须保留符号；`0.1+0.2` 写作 `0.30000000000000004`（最短往返 repr）。
- 整数 `1` 与浮点 `1.0` 是**两个不同的值**，序列化必须分开。
- 整数只接受 i64 区间——越界条目按 `int_out_of_range` 剔除，这样 Rust 侧用
  普通 `serde_json` 就够，不必开 arbitrary precision。
- 非有限浮点（NaN / Infinity）不是 JSON 值，一律剔除，不写成裸 `NaN`。

### 9. 阶段计时 `timings`（加字段，不升版本）

`build` / `render` / `export` 的**成功响应**带一个 `timings` 对象（毫秒，float）。
**legacy 信封一个字节都不加**（`{"ok":true,"manifest":…,"warnings":…}` 的形状是
契约）。加字段不升版本——两侧本来就必须容忍未知字段（§1）。

| 命令 | 键 | 含义 |
|---|---|---|
| `build` | `script_exec_ms` | 跑用户脚本那一段（import + entry） |
| `build` | `script_build_ms` | 整个 build（脚本 + instrument + 每个 stem 的首次预览） |
| `render` | `patch_apply_ms` | `overrides.apply` |
| `render` | `canvas_draw_ms` | `savefig(svg)` |
| `render` | `manifest_ms` | `build_manifest`（内含一次 `fig.canvas.draw()`） |
| `export` | `patch_apply_ms` / `export_ms` | 应用 patches / 全质量 `savefig` |

**为什么没有 `svg_ms`**：SVG 序列化与 draw 在 matplotlib 里是同一趟
（`print_svg` 边画边写），分不开。硬拆只能靠再画一遍，那就把测量本身变成了
被测对象。合并在 `canvas_draw_ms` 里，如实注明。

worker 只说**自己那一段**。父进程（`pool`）另外补两个键：`queue_wait_ms`
（请求发出去之前排了多久）与 `total_ms`（父进程看到的整次往返），worker 已给的
键一律不覆盖。冷启动那次 render 会把顺带触发的 build 计时折叠进来
（`script_*` + `build_total_ms`）——用户等的是一件事，而 build 是另一条命令，
不折叠的话响应里只剩十几毫秒的 apply/draw，而他刚等了半分钟。基线与口径见
`docs/perf-baseline.md`。

### 10. `render` 的可选 `preview_dpi`

`payload.preview_dpi`（正整数，可缺省）覆盖本次预览 SVG 的 dpi；不给就用 worker
启动参数 `--preview-dpi`。**只影响 SVG 里嵌入位图的分辨率**：含 imshow 的面板上
200→100 让 `canvas_draw_ms` 降三分之一、SVG 体积降四分之三；纯矢量图上完全无效
（实测字节数相同）。非正数是 `bad_request`——0 交给 matplotlib 会炸在渲染里，
报出来的是 `internal` + 一段指向错误方向的 traceback。

不给这个字段时**信封形状一字不变**，既有调用方与 golden 断言不受影响。

## 影响面与非目标

- **Flask HTTP API 与前端零改动**：`pool.EngineWorker` 的 Python 方法签名
  （`ensure_built` / `override` / `export` / `render_png` / `preview_png`）
  与返回结构不变，切协议对 `app.py` 完全透明。
- `request_id` 回显对不上 → `WorkerError(code="protocol_mismatch")` 且**当场
  kill 该 worker**（与超时同纪律：状态未知的会话绝不复用，下一次 `get()`
  自动重建）。这是本 ADR 里唯一新增的杀进程路径。
- 不做的：真正的中断（见 §6）、流式响应/进度、多路复用（一条管道同时跑多个
  请求）。这些都要 worker 变成多线程或多进程，是 supervisor 阶段的题目。

## 看护

- `tests/test_patchspec.py`：规范化语义 + 确定性 + golden vectors 逐组。
- `tests/test_worker_protocol.py`：pool 侧信封构造、回显校验、错误映射、
  generation 递增（假子进程，不需要科学栈）。
- `tests/test_worker_roundtrip.py` 末节：真 worker 的 v1 全链路、错误信封形状、
  hash_mismatch、cancel、**legacy 响应形状不变**、`timings` 的键集合与
  `preview_dpi` 的校验。
- `tests/test_worker_protocol.py` 末节：控制面补的 `queue_wait_ms`/`total_ms`、
  冷启动折叠 build 计时、不给 `preview_dpi` 时 payload 一字不变。
