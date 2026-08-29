# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 04（2026-08-29）

### 目标

后端统一 refresh：让手动刷新、后续 watcher、Codex MCP、内置 AI 与
RegistryDialog 调同一条逻辑，并新增显式 `POST /api/project/refresh`。
本阶段**只做后端刷新核心**——不实现项目 watcher（05），不改前端消费（06），
不碰 matplotlib 解析算法。

### 实际完成

裁决全文在 **`docs/adr/0025-unified-project-refresh.md`**。

**新模块 `src/tavotto/engine/project_refresh.py`。** 编排全在
`refresh_project_index()`：

```text
项目锁 → registry 前快照 → 静态 merge（内容变了才写盘）→ reload
→ registry 后快照 → 结构化 diff → 素材清单（与**上一轮**比）
→ 作废关系真的变了的 worker（限本项目）
→ 被盯的脚本集合变了才重挂 watcher
→ 有差异才发 `registry.changed` / `assets.changed`
```

SSE 与 watcher 两个副作用出口由 app 层**注入**（`RefreshSink`）。模块本身纯
标准库、不 import Flask，**连 `engine/documents` 都不 import**——「派生刷新
不碰文档」因此是结构性的，不是一条注释。

**`app.py` 的 `reload_registry()` 已删。** 它的两件事（重装 + 重挂 watcher）
都在服务里，而它还漏了第三件：作废过期 worker。三个老调用点
（`/api/registry/scan`、probe 成功、`PUT /api/registry`）全部改走
`app.refresh_project()`，**旧响应与旧事件形状逐字保留**。

**三处判据上的裁决**（理由见 `DECISIONS.md` T-14 / T-15 / T-16）：

1. **素材 diff 与上一轮比**，不与同一次调用里的前后比——刷新一个字节都不碰
   素材，那样的 diff 恒等于空，而恒等成立的 diff 看起来和「什么都没变」
   一模一样。基线在项目打开时 `seed_state()` 落一份；没有基线的那一轮报
   `assets.baseline=true`，不报「没变化」。
2. **没跑静态扫描时 `conflicts` 是 `null`**，不是 `{}`：合并成空表等于把
   「不知道」说成「已确认没有冲突」（同 Session 03 的 T-12）。
3. **watcher 认自己写的那一下靠内容修订号**，不靠「写完忽略两秒」。顺带把
   源头堵上：无变化的刷新**不回写**注册表（按字节比），于是 mtime 不动。

**顺手清掉 R-05 的一处**：`discover.write_config()` 并进 `engine/atomicio`
（原来没有 fsync——`os.replace` 只保证「要么旧要么新」，掉电时 replace 出来的
是空文件，而注册表随图库走）。看护它的两条用例原本钉在 `Path.replace` 上，
`atomicio` 调的是 `os.replace`：**桩挂不上就会恒绿**，注入点一并挪过去。

### 关键 API（Prompt 05 及之后直接用）

```python
# src/tavotto/app.py —— app 层唯一入口，别在别处再写第二条
refresh_project(ctx, *, reason, allow_static_merge=True,
                changed_paths=None, publish=True) -> dict

# src/tavotto/engine/project_refresh.py
refresh_project_index(ctx, *, reason, changed_paths=None,
                      allow_static_merge=True, publish=True, sink=None) -> dict
RefreshSink(publish=..., watch=...)      # 两个副作用出口，app 层注入
RefreshError(code, message, params)      # 稳定 code：scan_failed / registry_reload_failed
RefreshState                             # 每项目一份，挂在 ProjectCtx 上随项目消亡
seed_state(ctx)                          # 项目打开时落基线（素材 + 注册表修订号）
state_of(ctx) -> RefreshState
is_self_written(ctx) -> bool             # ← 05 的 watcher 用它认自己写的那一下
iter_assets(root) -> [(Path, kind)]      # 「哪些文件算素材」的唯一判据
asset_inventory(root) -> {id: {kind, size, mtime_ns}}
registry_snapshot(reg) / diff_registry(a, b) / diff_assets(a, b)
normalize_reason(raw) -> str
REASONS = ("manual","watcher","registry","probe","codex","ai","open","external")
EXCLUDE_DIRS / PDF_EXT / IMG_EXT         # app.py 里同名常量只是别名
```

返回结构（`/api/project/refresh` 原样返回；TS 类型在
`web/src/lib/api.ts::ProjectRefreshResult`）：

```jsonc
{
  "reason": "manual",
  "registry": {
    "added_scripts": [], "removed_scripts": [], "changed_scripts": [],
    "script_changes": {"a.py": ["entry", "stems"]},
    "added_stems": [], "removed_stems": [],
    "moved_stems": [{"stem": "S", "from": "a.py", "to": "b.py"}],
    "conflicts": null,           // null = 这一轮没扫，**不是**「没有冲突」
    "conflicts_changed": false
  },
  "assets": {"added": [], "removed": [], "changed": [], "baseline": false},
  "scripts": {},                 // 刷新后的完整注册表 = 「当前已登记脚本集合」
  "changed_paths": [],           // 只认进程内调用方给的；HTTP 上忽略
  "merge": {"added_scripts": [], "added_stems": {}},   // 旧 scan 响应的 changes
  "registry_revision": "…",
  "published": ["registry.changed"]                    // 真的发出去的那些
}
```

SSE（前端类型已补齐，**没有加任何 handler**）：

```jsonc
// registry.changed —— 一次刷新至多一条，无差异一条不发
{"pj": "…", "reason": "manual",
 "scripts": [], "stems": [], "added_scripts": [], "removed_scripts": [],
 "changed_scripts": [], "conflicts": {},
 "script": "只有恰好一个脚本变时才有（老客户端）"}

// assets.changed（新）
{"pj": "…", "reason": "manual", "ids": [], "added": [], "removed": [], "changed": []}
```

新增错误 code：**`registry_reload_failed`**（`{reason}`，已进
`USER_VISIBLE_CODES` + 双语文案）。`tests/test_error_codes.py` 的扫描范围加上
了 `engine/project_refresh.py`——码表看不见的模块 = 没有门禁。

### 迁移

**没有数据迁移，磁盘格式一个字节没动。** `tavotto_registry.json` 的内容与
写法都不变（只是落盘换成 `atomicio`，多了 fsync）。旧前端收到批量
`registry.changed` 只会照旧重取清单；`assets.changed` 它不认，静默忽略。

### 修改的文件

```text
新增  src/tavotto/engine/project_refresh.py    统一刷新服务（唯一编排）
新增  tests/test_project_refresh.py            38 条
新增  docs/adr/0025-unified-project-refresh.md
改动  src/tavotto/app.py                       scan_panels 共用判据、refresh_project、
                                               新端点、错误漏斗、删 reload_registry
改动  src/tavotto/engine/discover.py           write_config → atomicio（R-05 少一处）
改动  tests/test_discover.py                   故障注入点 Path.replace → os.replace
改动  tests/test_error_codes.py                扫描范围 + RefreshError 漏斗
改动  web/src/lib/api.ts                       事件与 /api/project/refresh 的**类型**
改动  web/src/i18n/locales/{zh-CN,en-US}/errors.json + resources.d.ts
重建  codex-plugin/mcp/widget/canvas.html      改了 web/src 就要重建
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 前端
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 改了 web/src 之后
python scripts/build_mcp_widget.py
```

结果见 `STATUS.md` 的两张表。Session 04 首轮：后端 **3093 passed**、前端
1370 passed；评审回合 1 之后：后端 **3102 passed**（9 分 18 秒）、前端
**1371 passed**，七条命令全部 exit 0。**29 条变异逐一反证，全部被打红**
（记录见 `TEST_MATRIX.md`；其中三条第一轮活了下来，判据已加固）。

两个跑法上的坑：

> **别再多加一个 `-q`。** `pytest.ini` 的 `addopts` 里已经有一个，叠成 `-qq`
> 会把最后那行统计**整个吞掉**——退出码还在，数字没了。本轮为了拿这一行
> 多跑了一遍全量（每遍 9 分钟）。

> `pnpm i18n:check` 会因为 `resources.d.ts` 过期而红：加了 errors 的 key 之后
> 跑一次 `pnpm exec i18next-cli types` 并提交生成结果。

### 这一轮变异反证抓到的（值得下一个 Session 记住）

**三条活下来的变异是同一个形状：判据把结论当成了前提。**

1. **并行刷新**——在测试里拿住 A 的那把锁，等于假设被测代码用的正是那把；
   换成全局大锁照样绿。改成从**里面**卡住 A（让它停在自己的临界区）。
   补完还不够：「B 最终回来了」在两种实现下都成立（A 的等待迟早超时放行），
   要断言的是 B 刷完的**那一刻** A 还没出来。
2. **`changed_paths`**——只喂项目外的路径，而那些本来就会被规整器丢掉，
   「端点认不认这个字段」根本没被量到。
3. **registry 快照**——只量「`load_data` 之后还在」，而今天 `load_data` 把每个
   容器都重建，浅拷贝碰巧也够；量的是 `Registry` 的实现细节，不是这个函数
   自己的承诺。补第二维：原地改同一个 list。

另外两条经验：

* **恒等成立的 diff 与「什么都没变」长得一模一样。** 素材 diff 第一版照着
  Prompt 的流程写（同一次调用里前后比），跑起来永远是空的，没有任何信号提醒
  ——是一条真的加了图片的用例把它红出来的。
* **桩挂不上的用例会恒绿。** `write_config` 换成 `atomicio` 之后，钉在
  `Path.replace` 上的故障注入打不中了；那条用例是以 `DID NOT RAISE` 红出来的
  （它的注释早写明了这个红法），换一条形状就会静默变成空门禁。

### 评审回合 1（PR #201，`7efe8e0`）

Codex 报了 1 条 P1 + 2 条 P2，**三条都成立**，都已修 + 配会红的用例
（裁决 `DECISIONS.md` T-18/T-19/T-20，用例表见 `TEST_MATRIX.md`）：

1. **P1 自动保存的判据与写入之间有缝。** `_revision_conflict` 两条边都钉住了
   （T-11），但判完到写完之间没有互斥——两个标签页同时新建同一份文档，双方
   都在对方落盘前读到「磁盘上没有」、都判没冲突，后写的整份盖掉先写的，**而
   两边都收到 200**。整段进锁（按落盘路径取的固定 64 条锁带）。
   **T-11 说判据的内容要对，这条说判据的位置要对**——判据与它守护的动作
   之间只要有间隙，它就退化成「在没人跟我抢的时候成立」。
2. **P2 目录 fsync 失败被和「Windows 打不开目录」一起吞了。** 两个 `except`
   挡的不是同一件事，吞掉后者 = 调用方收到成功、前端据此删掉本机兜底副本。
   顺带把 `engine/atomicio.py` 加进 `test_error_codes.py` 的扫描范围——它的
   三个 code 早就会落到界面上，却一直没有英文文案。
3. **P2 没有修订号时「覆盖」点一次没反应**（不是评审说的死循环：第二次点会
   成功）。现在拿不到 hash 就去摘要那里补一个真基线。

**这一轮的两条教训**：

> **又踩了一次「变异前先提交」。** 变异用 `git checkout --` 还原，把同一个
> 文件里**还没提交的修复**一起吃掉了，于是下一条变异在"没有修复"的代码上跑、
> 锚点找不到、`s.replace` 静默 no-op，结果看起来像"变异活下来了"。
> Session 03 记过一次，这是同一形状的第二次——**变异脚本应该自己拒绝在
> 脏树上跑**。

> **一条变异第一轮活了下来**：「修订号挪到锁外读」。黑盒看到的两种实现在
> 单个请求下完全一样，差别只在锁决定的那个交错窗口里。补的是白盒判据
> （交回去的那个修订号是不是在锁里读的）——**量不到的维度只能换一把尺，
> 不是放宽容差**。

### 尚存限制

1. **项目 watcher 还没有**（R-13）：脚本 watcher 仍是 mtime 轮询、事件一条一条
   直接 `sse_publish`。入口已经在（`app.refresh_project()`），归 Prompt 05。
2. **前端还没消费**：`assets.changed` 有类型、有 `EVENT_KINDS` 登记，**没有
   handler**；`refreshProject()` 有实现但界面上还没有入口。归 Prompt 06。
3. **项目打开仍走自己的静态草稿逻辑**（`build_draft` + `write_config`），
   没有并进统一服务——为了不让打开项目扫两遍（Prompt 04 §六 明确允许）。
4. `engine/` 里另外**五处**手写原子写仍未并入 `atomicio`（R-05；本次清掉的是
   `discover.write_config`，它原本不在那五处的清单里）。
5. **autosave 仍在数据目录**（R-07）。
6. `/api/layouts/<name>` 仍不做 schema 校验（ADR 0023 §5a），前置是修 R-18。
7. 「编辑历史」仍在文档菜单里，不是左上区域的独立入口（归 Prompt 08）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- 已推送并开出 **PR #201**（`feat/product-ux-reliability-v2` → `main`），
  一次带走 Session 01–04；评审回合 1 的三条修复也在同一个 PR 里
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个提交
  一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让 cla-check
  在同一个仓库里数出两个贡献者；提交时用 `git -c user.email=… commit`，
  **别改共享的 `.git/config`**
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 05：项目 watcher、批次合并、SSE）

**从这里开始读**：`src/tavotto/engine/project_refresh.py`（整份，尤其
`refresh_project_index` 与 `is_self_written`）、`engine/pool.py` 的
`start_watcher/stop_watcher`（现状的脚本 watcher，mtime 轮询约 2 秒），
以及 `app.py` 的 `_script_change_handler`。

**Session 04 留给它的接口**：

- watcher 发现变化后**调 `app.refresh_project(ctx, reason="watcher",
  changed_paths=[...])`**，让服务去 merge、去 diff、去发事件；
- 判「这次注册表变动是不是我们自己写的」用
  `project_refresh.is_self_written(ctx)`——**内容修订号**，不是时间窗口；
- `changed_paths` 会被规整成项目相对路径，项目外的自动丢掉。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` 把「载入」与「编辑」分开——载入不得触发回写。
2. `dirty` 同时盯 `doc` 与 `canvases`。
3. 收到 409 后**基线故意不推进**，本机兜底副本**不清**。
4. `HistoryEntry.label` 存 `UiMessage` 描述符，绝不存翻译后的字符串。
5. 落盘一律走 `engine/atomicio`（ADR 0023），不再手写 tmp+replace。
6. 保存状态只经 `setSaveState()` / `setDocNotice()` 改（ADR 0024）。
7. 排队稍后才发出的写入必须带走**排队那一刻**的 `pj`。
8. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）：watcher
   不得自己 `discover.merge`、不得自己发第二套事件、不得自己 reload 注册表。
9. **无差异 = 零事件、零写盘、零 worker 失效、零 watcher 重挂。**
10. 「哪些文件算素材」只有 `iter_assets()` 一处判据（`/api/panels` 与刷新共用）。
11. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
12. 刷新失败时内存里的注册表**原封不动**，事件一条不发。

**别做的事**：不要引入 `watchdog`（保持纯标准库轮询）；watcher **不得**把
Tavotto 自己的写入当成素材/注册表变化（autosave 目录、`tavottofile/`、
刷新自己写的注册表——前两个已被 `EXCLUDE_DIRS` 剪掉，第三个用
`is_self_written()`）；不要为批次合并再造一条并行的事件通道；不要因为
watcher 而重写 `documentStore` 的事务/撤销。
