# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 07（2026-08-29）

### 目标

把「这张图能不能进图内编辑」变成**一句可以直接显示的事实**，主语固定为
`/api/panels` 的那一个素材。

本阶段**只做后端事实模型与 API**——不做界面（08）、不增强解析器、不跑用户
脚本、不 probe。前端只加了类型与一个 fetch 函数，一个组件都没动。

### 为什么需要它（真实的起始状态，不是 prompt 假设的那个）

「这张图能不能编辑」在 07 之前由三处各答一次，而**三处的主语都不一样**：

| 出处 | 主语 | 它能回答的 |
| --- | --- | --- |
| `/api/panels` 给不给 `script` | **素材** | 注册表映射了没有 |
| `/api/registry` 的 `candidates` | **stem** | 静态扫描认领了没有 |
| `probe.script_inventory()` 的 `reason` | **脚本** | 这个 .py 处于什么状态 |

同一张图于是在素材面板里「不可编辑」、在注册表对话框里「有候选脚本」、在
脚本清单里「可试运行」——三句话都对，合起来却没有一句回答了用户的问题。
决策写在 `DECISIONS.md` 的 T-31。

### 实际完成

**1. 新模块 `src/tavotto/engine/readiness.py`（纯标准库，Flask 父进程 import）。**
六个互斥状态 + 稳定 reason code，判定表如下（分支从上往下，每张图只落一个）：

| # | 条件 | status | reason_code |
| ---: | --- | --- | --- |
| 1 | 注册表映射了这个 stem，脚本文件**在** | `editable` | `registered_source` |
| 2 | 注册表映射了，脚本文件**不在** | `source_missing` | `registered_script_missing` |
| 3 | 这一轮静态扫描**没跑成** | `layout_only` | `source_scan_unavailable` |
| 4 | **多个**脚本认领同一个 stem | `conflict` | `multiple_source_candidates` |
| 5 | **恰好一个**脚本认领 | `auto_linkable` | 见下 |
| 6 | 项目里有产图但输出名要跑才知道的脚本 | `needs_probe` | `runtime_output_unknown` |
| 7 | 其余 | `layout_only` | `no_source_candidate` |

第 5 行的 reason 说的是**卡在哪一步**，优先级从「刷多少次都没用」往「下一次
刷新就好了」排：

```text
registry_invalid  >  project_read_only  >  registry_write_failed  >  static_unique_candidate
```

**注册表优先于静态报告**（第 1 行在第 4 行之上，T-34）：注册表文件就是人工
裁决的落处（`src/tavotto/AGENTS.md`：「归属有歧义的 stem，裁决结果记在各图库
自己的注册表文件里，**勿改**」）。静态冲突照旧出现在项目级 `conflicts` 里，
带上 `resolved_by`。

**2. `GET /api/project/readiness`。** 下面这份是**真的跑出来的**（一个含
`sub/fig_a.py`→`FigA`、两个脚本抢 `Dup`、一个动态输出名脚本的项目；只有
`generated_at` 换成了固定值，其余逐字照抄，fingerprint 也是真的）：

```json
{
  "project_id": "3f9c1a2b7d04",
  "fingerprint": "70b70db1f41d093425be7c0349362c76",
  "generated_at": 1756468800.42,
  "summary": {
    "total": 3, "editable": 1, "auto_linkable": 0, "needs_probe": 1,
    "conflict": 1, "source_missing": 0, "layout_only": 0
  },
  "panels": [
    { "id": "Dup.pdf", "status": "conflict",
      "reason_code": "multiple_source_candidates", "script": null,
      "candidates": ["old_version.py", "z_newer.py"],
      "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "panel" } },
    { "id": "FigA.pdf", "status": "editable",
      "reason_code": "registered_source", "script": "sub/fig_a.py",
      "candidates": [], "can_probe": false, "can_manual_link": true,
      "details": { "entry": "main", "cost": "light" } },
    { "id": "Mystery.pdf", "status": "needs_probe",
      "reason_code": "runtime_output_unknown", "script": null,
      "candidates": ["dyn.py"], "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "project" } }
  ],
  "conflicts": [
    { "stem": "Dup", "candidates": ["old_version.py", "z_newer.py"],
      "resolved_by": null }
  ],
  "project": { "writable": true, "registry_valid": true,
               "scan_ok": true, "can_rescan": true },
  "issues": []
}
```

注意 `Dup.pdf` 那一条：`z_newer.py` 的名字更像"新版本"，`old_version.py` 的
名字更像"旧的"——**机器一个都不选**（`tests/…::test_two_scripts_claiming_one_stem_is_a_conflict_and_is_never_auto_resolved`
连 mtime 更新的那一个也不许赢）。

三个字段的取值是**三档不是两档**，08 不要把它们压扁：

* `conflicts`：`null` = 这一轮没跑静态扫描；`[]` = 扫过了、没有冲突；
* `project.registry_valid`：`null` = 项目里根本没有注册表文件（还没起草过）；
  `false` = 有、但读不回来；
* `details.candidate_scope`：`"panel"` = 这张图的候选；`"project"` = 项目里
  这几个脚本的产物静态解不出来，跑一个才知道是不是它。

**3. `/api/panels` 每项多一个 `capability`**（六个字段，`CAPABILITY_FIELDS`）。
它是**同一次 `compute()` 的投影**，`/api/panels` 不自己再判一遍——两处各算
一遍的话，「素材面板说可编辑、就绪度面板说要试运行」只是时间问题。

**老字段一个没动。** `script` 的语义仍然是「注册表声明了映射」，`editable`
时照旧有值；`auto_linkable` / `conflict` 有候选，但候选**不塞进 `script`**
（塞了的话旧前端会当场给它画上 ⚡）。`source_missing` 仍带 `script` ——那是
注册表里真实记着的那一条，不是伪造；要分辨「脚本还在」与「指着的文件没了」
就看 `capability.status`。

**4. fingerprint = 报告自身的内容哈希**（T-32）：规范化 JSON（**键排序**）
的 SHA-256 前 32 位，输入是 body 去掉 `generated_at` 与 `fingerprint`。
于是要求里那四条自动成立，不用逐条去防——时间戳不在 body 里所以进不来；
素材 / 脚本的 mtime 没有进报告所以变了它不动；绝对路径本来就一个都不在；
键序由 `sort_keys` 排掉。

**5. 项目级缓存**（挂在 `RefreshState.readiness`，随项目消亡）：两层，键都是
**输入的内容签名**——贵的那层是 `discover.discover()`（逐脚本 `ast.parse`），
外层是整份报告。**扫描失败的那一份不进缓存**（缓存一次失败等于让一次瞬时
错误把就绪度永久钉死）。**进出都深拷贝**（缓存里那份是唯一权威）。

**6. 三处很小的既有代码改动**（都在同一条链路上，不是顺手重构）：

* `discover.claims_of()` —— 从 `discover()` 里抽出的纯函数（「stem 被谁认领」
  的唯一判据）。`discover()` 的输出**一字未变**，它现在是这个函数的第一个
  消费者，就绪度是第二个；
* `RefreshState.registry_write_failed` —— 静态合并**写**注册表失败时置位、
  成功时清零。对外的 `scan_failed` code **没改**（老 `/api/registry/scan` 的
  契约），区分只留在状态里给就绪度用；
* `RefreshState.readiness` —— 缓存槽位。刷新在**确认事实真的动了之后**把它
  清成 `None`（`project_refresh` 不 import `readiness`，否则依赖成环）。
  这是签名之外的**第二道**判据：签名盖不住「同尺寸 + 同一个 mtime_ns 刻度里
  的就地改写」，而那正是刷新自己写注册表时的形状。

### 关键 API（Prompt 08 直接用）

```python
# src/tavotto/engine/readiness.py
compute(ctx) -> dict            # 报告本体（**不含** generated_at）；ctx 只要 path/id/registry
capability_map(ctx) -> dict     # 素材 id → capability 子集（/api/panels 用的就是它）
invalidate(ctx) -> None         # 丢掉缓存（用例与非刷新路径用）
fingerprint(body) -> str        # 报告 → 内容哈希
STATUSES, REASONS_BY_STATUS, CAPABILITY_FIELDS   # 枚举与判定表的机器可读版本
```

```ts
// web/src/lib/api.ts
fetchReadiness(): Promise<ReadinessReport>
PanelInfo.capability?: PanelCapability
type ReadinessStatus   // 六个状态的闭集
type ReadinessReason   // 十个 reason code 的闭集
```

### 迁移

**没有迁移，磁盘格式一个字节没动。** 就绪度不写盘。唯一的接口变化是
`/api/panels` 每项**多**了一个可选 `capability`——旧前端忽略未知字段。

### 修改的文件

```text
新增  src/tavotto/engine/readiness.py         事实模型（纯诊断，不执行、不写盘）
新增  tests/test_project_readiness.py         53 条
改动  src/tavotto/engine/discover.py          抽出 claims_of()（discover() 输出不变）
改动  src/tavotto/engine/project_refresh.py   +registry_write_failed、+readiness 缓存槽、
                                              _static_merge 记账、有差异才失效缓存
改动  src/tavotto/app.py                      +GET /api/project/readiness；
                                              scan_panels 挂 capability（同源投影）
改动  web/src/lib/api.ts                      +六状态/十 reason 的类型、+fetchReadiness、
                                              PanelInfo.capability
重建  codex-plugin/mcp/widget/canvas.html     改了 web/src 就要重建（指纹 47aee0ca4eee6e47）
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 只跑本阶段
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_project_readiness.py
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 改了 web/src 之后
python scripts/build_mcp_widget.py
```

后端全量 **exit 0 —— 3199 passed / 34 skipped / 2 deselected**，9 分 57 秒
（Session 06 的 3145 passed + 53 新增 + 1 = 3199，数字对得上）。
前端 **124 files / 1456 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。
**变异反证 35 条全部被打红**（第一轮活下来 7 条，两种成因与处置见 `TEST_MATRIX.md`）。

**Session 06 那条红本轮两次全量都绿**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
本轮 `tavotto run` 那条线一个字节没改，所以两次绿**不构成"它被修好了"**——
它是偶发的，仍留在 `STATUS.md` 的遗留表里。

### 这一轮踩到的坑

**七条变异第一轮活了下来**，两种成因，都值得下一个 Session 记住：

1. **同一条保证有两个实现，谁也杀不死谁（2 条）。** 排序做了两遍
   （素材清单一次、报告 panels 一次），删掉任意一处都还有另一处兜着。
   这不是"多一层保险"，是**判据量不到自己**。处置：删掉冗余的那一处
   （T-36），顺序的契约只留一份。
2. **用例只跑了「方便的那个时刻」（5 条）。** 只读项目、非法注册表、内存
   注册表、深拷贝出口、结构校验——五条的形状完全一样：用例把状态**摆好之后
   才第一次读**，于是缓存里根本没有旧值可以过期，"缓存键含这一维"就量不到。
   处置：先热一遍缓存，再改条件，再读第二遍。

变异脚本带 `PYTHONDONTWRITEBYTECODE=1`，还原走**备份文件**而不是
`git checkout --`（工作树里有未提交的新文件）。

### 尚存限制

1. **就绪度只覆盖磁盘素材**（`/api/panels` 的 id 空间）。runtime figure 素材
   （ADR 0013，`runtime:` 前缀）不在报告里——它们按定义就有脚本，且 id 空间
   不同，混进来会破坏「id 与 `PanelInfo.id` 逐字相同」这条。
2. **`needs_probe` 的候选是项目级的**：静态解不出那些脚本的产物，所以说不出
   「这张图来自其中哪一个」。项目里有一个动态脚本，所有没有专属候选的图都会
   变成 `needs_probe`——`details.candidate_scope: "project"` 就是为了让 08 能
   如实措辞（「跑一个就知道了」，而不是「这张图来自其中之一」）。
3. **`/api/panels` 的 `capability` 可能缺席**：就绪度扫描与素材遍历之间新出现
   的素材这一轮没有它。`undefined` 的意思是「这一轮还不知道」，**不是**
   `layout_only`——08 不要给它补默认值。
4. **签名的分辨率与 watcher 同级**（`(size, mtime_ns)`）：「同尺寸 + 同一个
   mtime_ns 刻度里的就地改写」两边都发现不了。刻意不在就绪度这一侧单独收紧
   ——收紧一侧只会让两个模块对「变了没有」给出不同答案。刷新那一侧的显式
   失效是第二道判据。
5. **项目打开仍走自己的静态草稿逻辑**，没并进统一服务（为了不扫两遍）。
6. 04/05/06 的其余遗留（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、没有 SSE 事件名的同源门禁、
   「编辑历史」入口位置）原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 / 06 / 07 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 08：Readiness 前端与常驻左栏）

**从这里开始读**：`src/tavotto/engine/readiness.py` 的模块文档（判定表与三档
取值都在里面）、`web/src/lib/api.ts` 的 `ReadinessStatus` / `ReadinessReason` /
`ReadinessReport`、`DECISIONS.md` 的 T-31~T-36。

**Session 07 留给它的**：一份**已经算好**的事实面。

| 东西 | 位置 | 08 可以直接依赖的性质 |
| --- | --- | --- |
| 每张图的能力 | `PanelInfo.capability`（`/api/panels` 每项都带） | 与整份就绪度**同一次计算**，不会互相矛盾 |
| 整份报告 | `GET /api/project/readiness` → `fetchReadiness()` | 带 summary、conflicts、项目级 issues |
| 「变了没有」 | `fingerprint` | 同一份事实下不变；`generated_at` 与无关文件的 mtime 都进不来 |
| 状态与文案的对应 | `REASONS_BY_STATUS`（后端）/ `ReadinessReason`（前端类型） | 闭集，且有用例钉住「不许冒出没备案的组合」 |
| 动作能力 | `can_probe` / `can_manual_link` / `can_rescan` | 只说"界面可以提供"，执行仍归既有端点 |

**绝不要做的事**：

1. **不许在前端重新猜状态。** 按 `script` 有没有值自己判一遍，就是把改造前
   那三个互相矛盾的答案又请回来一个。能力事实只有 `capability.status` /
   `reason_code` 一个出处。
2. **不许另起同义状态。** 六个就是六个；界面上要分得更细的话，回后端加
   reason code（并在 `REASONS_BY_STATUS` 里备案），不要在组件里再分一层。
3. **不许把三档压成两档**（`conflicts` 的 `null`、`registry_valid` 的 `null`、
   `capability` 的 `undefined`）。「没测量」不是「测量结果是零」，把它补成
   默认值，用户会一直等一个永远不来的提示。
4. **不许把 reason code 翻译成的句子存进文档或 history**（存 message key +
   结构化参数——`HistoryEntry.label` 的既有约定）。
5. **不许让就绪度界面去执行动作。** 试运行走 `/api/registry/probe`（用户显式
   触发、可取消、有进度），手工关联走 `PUT /api/registry`，重扫走
   `POST /api/project/refresh` → `refreshProjectNow()`（素材面板的刷新按钮
   已经在用这一条）。
6. **不许在 UI 上暴露实现术语**（stem / registry / AST / manifest）——这正是
   reason code 存在的理由：后端给枚举，前端给人话。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）；保存状态只经 `setSaveState()` /
   `setDocNotice()` 改（ADR 0024）。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（本轮新增）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。
6. 「哪些文件算素材」只有 `iter_assets()` 一处判据；脚本遍历只有
   `discover.iter_all_scripts()` / `iter_scripts()` 两个视图；「谁认领了这个
   stem」只有 `discover.claims_of()` 一处。
7. **就绪度不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE**
   （磁盘 CANARY + 桩两层证据钉着）。
8. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史。**
9. **素材不在清单里 ≠ 脚本关系失效**（T-28）。
10. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
