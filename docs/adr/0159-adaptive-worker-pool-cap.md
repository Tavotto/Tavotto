# ADR 0159：worker 池上限按内存自适应，后台补渲染不挤掉前台

日期：2026-11-02 · 状态：**Proposed**（反馈 TVM-Y37X「越改越慢」；2026-11-02 在 origin/main 上压测定位）
相关：[0003 worker 协议 v1](0003-worker-protocol-v1.md)、[0004 workerd supervisor](0004-workerd-supervisor.md)、[0128 跨图对齐](0128-smart-alignment.md)（邻居补渲染）；
`docs/rules/backend/worker-protocol-and-lifecycle.md`、`docs/rules/backend/process-boundaries.md`、`docs/rules/frontend/render-state-keying.md`。

## 动机

同一张图编辑 400 次不变慢（热渲染约 105 ms）。慢的是**会话池写死 `MAX_ALIVE = 3`** 时的 LRU 轮转：

| 轮流渲染的脚本数 | 中位耗时 | 冷启动 |
| --- | --- | --- |
| 2 / 3 | 约 105 ms | 0 次 |
| 4 / 6 | 约 1580 ms | **每次都冷启动**（15 倍） |

LRU 在「循环访问的工作集 > 容量」时命中率塌到 0。Windows 上冷启动本身可达 20–40 s，所以掉下这个悬崖体感就是「越用越慢、新开的图也慢」。
前端自己制造轮转：进图内编辑时 `crossFigureNeighbours` 给视口里所有可见的带脚本邻居补渲染（ADR 0128），SVG 预算驱逐之后 `svgEvicted` 又触发重画。

## 决定

1. **上限按内存自适应**，唯一出处 `pool.max_alive()`（淘汰、冷启动容量、`_pending_retirements` 的拒绝线、发布账本都问它）：
   `clamp(MIN_ALIVE=3, (可用物理内存 + 池内活会话数 × SESSION_MEMORY_ESTIMATE) × MEMORY_SAFETY_FACTOR(0.5) ÷ SESSION_MEMORY_ESTIMATE(500 MiB), MAX_ALIVE_CEILING=8)`；
   可用内存读不到 → 一律 3（下限）。读数缓存 60 s。
   - **自己会话占的内存算回预算**：`available` 是扣掉池内会话之后的余量，不加回来就会「会话越多 → 上限越小 → 淘汰 → 内存回来 → 上限变大 → 冷启动」自激振荡。活会话数在 `max_alive()` 重算时锁外近似读（`list(_workers.values())` 在 GIL 下原子，差一两个只让估计偏一点）。
   - **迟滞**：自适应上限的下降量小于 `MAX_ALIVE_HYSTERESIS`（2）时沿用旧值（只掉 1 不采纳），掉 2 以上才算真的内存吃紧。
   - **淘汰只发生在 `acquire()`**，且只在活会话数 > 上限时；上限回升或抖动从不主动杀热会话。环境变量 `TAVOTTO_MAX_ALIVE`（整数，夹到 [1, 64]，非整数忽略）覆盖自适应，给 Lab 压测用。
   - 读取在新模块 `engine/sysmem.py`，**纯标准库**（Flask 父进程规则）：Windows `ctypes GlobalMemoryStatusEx`、macOS `vm_stat`（free+inactive+speculative）、Linux `/proc/meminfo` 的 `MemAvailable`。
   - 每会话占用用保守估计，**不取实测 RSS**：worker 在 workerd 里时 Flask 拿不到子进程 PID，硬取会引入平台分支，宁可高估。
   - `max_alive()` 不许在持 `_lock` 时调（macOS 读内存是带超时的子进程）。
2. **取会话带意图**（闭集 `purpose`，请求体可选字段，缺省 `foreground`）：`foreground` / `neighbour`。
   - `neighbour` 命中热会话：**不刷新 `last_used`**，也不摘掉会话的「仅后台用过」标记。
   - 淘汰排序：先「仅后台用过」的会话（`_background_only`），再按 LRU。用户正在编辑的图每次渲染都走前台，`last_used` 永远最新，最后才被淘汰。
3. **后台补渲染不挤掉前台**：`neighbour` 需要新会话（冷启动 / 重建）而池里**别的**活会话已占满上限时不做，抛 `WorkerError(code="background_render_deferred")`，
   `/api/engine/render` 回 **409** `{"code": "background_render_deferred", "deferred": true}`；不发 `render.preparing` / `render.failed`，不进交接结论，不报错。
   前端把它当「暂不补渲染、保留旧画面」：状态回到发请求前，`wantPatches` 清掉、`deferredPatches` 记下，同步器不以后台身份重发同一版；
   用户进编辑 / 改值 / 脚本变更后它不再是「纯邻居」，照常以前台发。判定「纯邻居」的唯一函数是 `useEngineSync.renderPurposeOf`；调度仍只住 `renderScheduler`。

## 协议兼容

- 请求加一个可选字段 `purpose`；不带 = `foreground`，行为与改动前一致。老前端 + 新后端、新前端 + 老后端（忽略未知字段，等于永远前台）都成立。
- 未知取值 400 `invalid_purpose`。
- 前台请求的字节形状不变：前端只在 `neighbour` 时才带 `purpose`。

## 后果

- 内存充裕的机器上，4–8 张图轮流渲染的冷启动从「每次」降到「每个脚本一次」；内存紧张的机器仍是 3，与改动前一致。
- 池可能比 3 多占最多 5 个 worker 的内存（约 2.5 GiB 估计值）：这是用户机器上的实际占用，所以安全系数取 0.5 且上限封顶 8。
- 邻居补渲染在池满时被推迟：跨图对齐候选在该邻居拿到 manifest 之前出不了线（与 ADR 0128 里「邻居还没渲染」同一状态），换来的是不再把用户正在编辑的图挤出去。

## 看护

`tests/test_pool_adaptive_cap.py`：上限公式的夹紧与读取失败回落、环境变量覆盖、单一出处（AST）、4/6 脚本轮流的冷启动计数、邻居不刷新 LRU / 不淘汰前台 / 先于前台被淘汰、
上限不随池内会话增多而下降、边界抖动下热会话不被反复淘汰（含迟滞）、`/api/engine/render` 的 purpose 透传、400 与 409 契约；`web/src/store/renderStore.test.ts` 与 `hooks/useEngineSync.test.ts` 的暂缓与意图用例。

## 未验证

Windows / macOS 真机的内存读数与 Lab 压测（`TAVOTTO_MAX_ALIVE` 对照）未在本 PR 里跑。
