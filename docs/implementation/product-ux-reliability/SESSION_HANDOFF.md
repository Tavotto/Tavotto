# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 05（2026-08-29）

### 目标

把「只盯已登记脚本 mtime」的 watcher 升级成**项目级 watcher**：外部编辑器
新增、删除、重命名、原子替换脚本，外部改注册表，新增/更新/删除图片，
Tavotto 都要自己看见、合并成一批、调统一刷新、发正确的事件。
本阶段**只做后端 watcher 与事件闭环**——不做前端 UI（06），不增强解析器。

### 实际完成

裁决全文在 **`docs/adr/0026-project-file-watcher.md`**。

**新模块 `src/tavotto/engine/project_watch.py`。** 判据从「按注册表清单逐个
`stat()` 看 mtime 变没变」换成**整棵树的轻量快照**：

```text
scripts   : discover.iter_all_scripts()      → rel_key(POSIX) → (size, mtime_ns)
registry  : tavotto_registry.json + mm_registry.json
assets    : project_refresh.iter_assets()    → 素材 id（与 /api/panels 逐字相同）
```

集合变了 = 新增/删除/改名；签名变了 = 就地改写/原子替换。**老实现守住的其实
只有「一个已登记脚本被就地改写」一种形状**——新建不在清单里、删除被
`OSError` 吞掉、重命名两头都看不见、原子替换换掉了 inode（判据量的是那个
已经不存在的对象）、注册表与素材根本不是 `.py`。换主语一次修好五种。

**遍历规则不新写第三份**：脚本用 `discover` 的那一份（`PRUNE_DIRS` /
`MAX_DEPTH` / 隐藏项），素材用 `project_refresh.iter_assets()`。代价是每轮
两次遍历，换来的是"watcher 盯的范围"与"discover 会收的范围"、"用户看得见的
素材"永远不会漂移。

**批次**：防抖 0.5 s（新变化把批次结束往后推）+ **批次年龄上限 5 s**——防抖
等的是「安静」，而目录可能永远不安静（脚本正在跑、正在拷一个大目录）。
**快照在结算之前就换掉**，于是刷新执行期间到达的写入进下一批而不是丢失。
两个参数都可注入，循环体拆成 `prime()` + `poll()`，测试用假时钟**逐轮**驱动
——没有一句 `time.sleep(2.5)`。

**自写循环**用 `project_refresh.is_self_written()`（内容修订号）认，不是"写完
忽略两秒"；**摘掉的只是注册表那几个路径，不是整批**（一次保存完全可能同时
改了脚本、生成了图片，并让刷新回写了注册表）。

**目录暂时不可用时快照返回 `None`，这一轮什么都不做**。空快照与"用户删光了
所有文件"在 diff 里长得一模一样，照它行事会让一次网盘抖动打掉整个项目的
渲染会话。

**`pool.py` 的 `start_watcher/stop_watcher/watched_dirs` 删除**，12 处调用方
（10 个测试夹具 + `desktop.py`）全部迁移。不留兼容代理的理由是一个具体的
失败模式：那个签名表达不了项目 watcher 需要的东西，留一个降级版代理的话，
任何一条老路径调它就会把功能完整的 watcher **替换成一个只盯清单的**——
两个 watcher 不会同时跑，但活下来的是残缺的那个，而且没有任何征兆。
`RefreshSink.watch` 同理删除（整棵树的 watcher 没有"盯谁"这个状态）。
新增 `pool.invalidate_project()` 与公开别名 `pool.norm_dir`。

### 关键 API（Prompt 06 及之后直接用）

```python
# src/tavotto/engine/project_watch.py
start(ctx, *, sink=None, interval=2.0, debounce=0.5, max_batch=5.0) -> ProjectWatcher
stop(figures_dir=None)                 # 不给目录 = 全停
watched_dirs() -> list[str]            # 诊断
watcher_of(figures_dir) -> ProjectWatcher | None

WatchSink(refresh=..., script_changed=..., error=...)   # 三个出口，app 层注入
ProjectWatcher.prime() / .poll() / .run() / .stop()     # 循环体可逐轮驱动
take_snapshot(root) -> Snapshot | None                  # None = 目录当前不可用
diff_snapshots(before, after) -> Delta                  # .scripts / .registry / .assets
DEFAULT_INTERVAL / DEFAULT_DEBOUNCE / DEFAULT_MAX_BATCH / REGISTRY_NAMES

# src/tavotto/app.py
_watch_sink(ctx) -> WatchSink          # refresh → refresh_project(reason="watcher")

# src/tavotto/engine/pool.py
invalidate_project(figures_dir)        # 本项目全部会话（paper_style* 走这条）
norm_dir(figures_dir)                  # 公开别名，池键 / watcher 键同一把尺
```

**`RefreshSink` 现在只有 `publish` 一个字段**（`watch` 已删）。

### SSE（前端类型已补齐，**仍然没有加任何 handler**）

```jsonc
// panel.file_changed —— 由 watcher 发：已登记脚本的**内容**变了，且文件还在
{"pj": "…", "scripts": ["fig1.py"], "stems": ["Fig1"]}

// project.error（新）—— 后台刷新失败，可恢复：内存里的注册表原封不动，
// watcher 线程继续，文件修好之后下一轮自动重试
{"pj": "…", "reason": "watcher", "code": "scan_failed", "params": {"reason": "…"}}

// registry.changed / assets.changed —— **只由统一刷新发**，形状与 04 完全一致，
// 只是多了一个 reason="watcher" 的来由
```

**没有新增错误 code**：`project.error` 复用刷新已有的 `scan_failed` /
`registry_reload_failed`（双语文案 Session 04 已备）。

### 迁移

**没有数据迁移，磁盘格式一个字节没动。** 唯一的兼容影响是
`pool.start_watcher/stop_watcher/watched_dirs` 消失了——它们没有出现在任何
公开契约里（不是 HTTP、不是 MCP、不是 CLI），调用方全在本仓库内。
旧前端收到 `project.error` 会静默忽略（`EVENT_KINDS` 里没有它就不注册监听）。

### 修改的文件

```text
新增  src/tavotto/engine/project_watch.py    项目 watcher（快照 / 批次 / 生命周期）
新增  tests/test_project_watch.py            44 条
新增  docs/adr/0026-project-file-watcher.md
改动  src/tavotto/engine/pool.py             删旧 watcher；+invalidate_project、+norm_dir
改动  src/tavotto/engine/project_refresh.py  删 RefreshSink.watch 与重挂那一段
改动  src/tavotto/app.py                     +engine_watch、+_watch_sink；open/close 换 watcher
改动  src/tavotto/desktop.py                 退出路径改 engine_watch.stop()
改动  web/src/lib/api.ts                     +project.error 的**类型**与 EVENT_KINDS
改动  tests/（10 个文件）                     夹具 engine_pool.stop_watcher() → engine_watch.stop()
改动  tests/test_projects.py                 watcher 替换用例改用新 API
改动  tests/test_project_refresh.py          删「重挂 watcher」那条，换成「钩子确实没了」
改动  docs/adr/0025-…                        摘要表里「watcher 重挂时机」那行作废
重建  codex-plugin/mcp/widget/canvas.html    改了 web/src 就要重建
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 改了 web/src 之后（排在所有前端改动**之后**）
python scripts/build_mcp_widget.py
```

结果见 `STATUS.md` 的表。后端 **3146 passed** / 34 skipped / 2 deselected（比 04 的 3102 整好 +44 = 新增的
`tests/test_project_watch.py`），exit 0，10 分 24 秒。前端 118 files / **1371** passed，
`build` / `i18n:check` / `lint` 三条 exit 0（本轮只动了类型，用例数不变）。
**变异反证 31 条，全部被打红**（记录见 `TEST_MATRIX.md`）。

> 中途有一轮全量红了两条（`test_mcp_server.py` 与 `test_windows_regressions.py`
> 的画布同步判据）——`web/src/lib/api.ts` 改了而 `canvas.html` 没重建。
> **重建要排在所有前端改动之后**，反了的话产物是对的、判据仍然红。

### 这一轮变异反证学到的（两件）

#### 一、变异自己可能不是变异

第一版的「注册表变化一律不刷新」写成了 `keep.registry = set() or {…}`——
`set()` 是假值，`or` 原样求值到后面那个推导式，**文本变了、行为一个字节
没变**，跑完全绿。

这是"变异显示绿"的**第四种**成因：不是判据弱（03/04 各撞过一次），不是那条
分支从没被执行过，也不是 `__pycache__` 命中旧 `.pyc`，而是**这条变异根本不是
一次变异**。变异脚本里"先证明产物真的变了"只比文本，挡得住 `s.replace()` 的
静默 no-op，挡不住语义 no-op。处置是整段替换，**并顺手补上反方向的那一条**
（自写也不摘）——一行判据的两个越界方向各来一次。

#### 二、一条守卫把另一条判据的行为面盖住了

`_dispatch()` 开头补上 `stop_event` 检查之后，「`stop()` 不清 pending」那条
变异**活了下来**——新守卫把它的行为面整个盖住。这是「抽掉不红」的两种成因
之一，而删错了会把刚防住的东西放回去。逐条问下来：那一句仍有独立价值
（停掉的 watcher 抱着一批永远不会结算的变化，是个会在下一个人手里变成 bug
的状态），只是它的维度是**状态**而那条用例断言的是**行为**。处置是换一把
量得到那个维度的尺（`assert not w._pending`），外加一句前提断言——否则
"这一批本来就没攒上"会让后半句恒真。

三条老纪律这一轮全部生效并且都没有再踩：脚本在**脏树上拒跑**、每轮清
`src/**/__pycache__`、锚点计数必须恰好 1。

### 尚存限制

1. **前端还没消费**：`assets.changed` / `project.error` 有类型、有 `EVENT_KINDS`
   登记，**没有 handler**；`refreshProject()` 有实现但界面上还没有入口。归 06。
2. **删除的脚本不会从注册表里消失**：`discover.merge()` 只追加、不删条目
   （注册表是用户策展数据）。"源文件不见了"要由就绪度模型表达，归 07/08。
3. **非 `paper_style` 的共享模块**（`helpers.py` 之类）改了不作废 worker，
   只触发一次刷新——依赖图我们不解析。用户显式重渲染即可。
4. **项目真的被删除**要等到用户下次打开时才报（`take_snapshot` 返回 `None`
   的那条取舍，ADR 0026 §5）。这是刻意的：误报删除比晚报贵得多。
5. **项目打开仍走自己的静态草稿逻辑**（`build_draft` + `write_config`），
   没有并进统一服务（为了不让打开项目扫两遍）。
6. `engine/` 里另外**五处**手写原子写仍未并入 `atomicio`（R-05）。
7. **autosave 仍在数据目录**（R-07）。
8. `/api/layouts/<name>` 仍不做 schema 校验（ADR 0023 §5a），前置是修 R-18。
9. 「编辑历史」仍在文档菜单里，不是左上区域的独立入口（归 08）。
10. **后端 `sse_publish` 的事件名与前端 `EVENT_KINDS` 没有同源门禁**——名字
    写错一个字，后端全绿而前端永远收不到。（`assets.changed` 在 04 也是这样
    加进来的。）做一个可靠的要先把发布点枚举清楚：有几处是变量传名
    （`project_refresh` 的 sink、`ai_bridge`），朴素 grep 会误报。本阶段用
    端到端用例把新加的 `project.error` 单独钉住，全仓门禁记在这里待办。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201**（`feat/product-ux-reliability-v2` → `main`）已开，带的是
  Session 01–04。**本阶段的 6 个提交还没有推**（`43281dd`…`03d54e0`）——
  用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，推上去会
  立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个提交
  一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让 cla-check
  在同一个仓库里数出两个贡献者；提交时用 `git -c user.email=… commit`，
  **别改共享的 `.git/config`**（linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 06：前端事件消费与派生元数据同步）

**从这里开始读**：`web/src/lib/api.ts` 的 `ServerEvent` 与 `EVENT_KINDS`
（四条事件的完整形状都在那儿）、`web/src/store/assetStore.ts`
（今天唯一消费 `panel.file_changed` 的地方）、`docs/adr/0026` §6「事件的归属」。

**Session 05 留给它的接口**：

| 事件 | 谁发 | 前端该做什么（06 要实现的） |
| --- | --- | --- |
| `registry.changed` | 统一刷新 | 更新脚本/stem 映射；`conflicts` **缺席 = 这轮没扫**，不是"没有冲突" |
| `assets.changed` | 统一刷新 | `added`/`removed`/`changed` 三类分别处理；**今天完全没有 handler** |
| `panel.file_changed` | watcher | 重渲染这些 stem（既有语义，不变） |
| `project.error` | watcher | 显示可恢复的项目级错误（`code` 走 `errors:*` 双语码表） |

显式刷新入口：`POST /api/project/refresh` → `refreshProject()`（已实现，
界面上还没有入口）。返回结构见 ADR 0025 与 `ProjectRefreshResult`。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` 把「载入」与「编辑」分开——载入不得触发回写。
2. `dirty` 同时盯 `doc` 与 `canvases`。
3. 收到 409 后**基线故意不推进**，本机兜底副本**不清**。
4. `HistoryEntry.label` 存 `UiMessage` 描述符，绝不存翻译后的字符串。
5. 落盘一律走 `engine/atomicio`（ADR 0023），不再手写 tmp+replace。
6. 保存状态只经 `setSaveState()` / `setDocNotice()` 改（ADR 0024）。
7. 排队稍后才发出的写入必须带走**排队那一刻**的 `pj`。
8. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）。watcher 不 merge、不 reload、不发第二套
   registry/assets 事件。
9. **无差异 = 零事件、零写盘、零 worker 失效。**
10. 「哪些文件算素材」只有 `iter_assets()` 一处判据；脚本遍历只有
    `discover.iter_all_scripts()` 一处。
11. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
12. 刷新失败时内存里的注册表**原封不动**，事件一条不发。
13. **派生数据刷新不得把文档标脏，也不得进普通撤销历史**（UX_CONTRACTS 不变式 A）
    ——06 最容易破的就是这一条：收到 `assets.changed` 就顺手 `setDoc()`。

**别做的事**：不要为事件消费再造一条并行通道（SSE 已经是唯一通道）；不要在
组件里直接改 `documentStore` 的文档字段（走 actions）；不要因为一条
`assets.changed` 就整份重取素材（`added`/`removed`/`changed` 已经给到 id 级）；
不要把 `project.error` 做成一个模态框（它是可恢复的后台状态，不是需要用户
当场决策的事）。
