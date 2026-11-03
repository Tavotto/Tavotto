# ADR 0083：override 的目标身份——脚本结构变了，旧编辑不许静默落到别的对象上

日期：2026-09-25 · 状态：**Accepted**（实现随本 PR；字段取舍已由用户拍板，见「已定」）
相关：[0003 worker 协议 v1 + patch 规范化](0003-worker-protocol-v1.md) §8（本 ADR 给规范形加了一个可选键）、
[0049 写回像素门](0049-write-back-pixel-verification.md)（写回事务的 verify 段）、
[0034 图例条目绑定](0034-legend-entry-binding.md)（同样是「按什么认对象」的问题，范围只在图例里）

## 问题

QA 2026-09-24（PR #583）SCI-03-B1，高：override 按**位置式 gid**（`axes_i.lines_j`，
`engine/manifest.py` 的 `instrument`）匹配。用户先把曲线 alpha 改成洋红，之后在脚本里
重排 / 插入 / 删除曲线或在前面加一个子图——同一个 gid 指向了另一条曲线（beta / gamma /
另一个子图里的 delta），旧 override 静默落在它身上，warnings 为空；写回的一次性重放
按同一个 gid 重放，几何门与像素门比的是「热态 vs 重放」，两边错得一模一样，也放行，
错误的样子被写进用户的原件。QA 的四个变体 4/4 复现。

规范 SCI-03 的验收标准：旧 override 只匹配原逻辑对象；匹配不上要明确报告，绝不「成功」
套给另一条曲线。

## 裁决

### 一、不做对象身份系统，只加一道核对

gid 仍是位置式的、仍是唯一寻址方式（存量文档、别名、跨图同步都建在它上面）。每条 patch
可以额外带一个 `identity`：**写这条编辑那一刻**，manifest 里这个 gid 的目标身份。重放时
`overrides.apply` 拿它与 gid **此刻**指向的对象的身份比：

- 对得上 → 照常应用；
- 对不上（包括此刻那个对象没有身份）→ **这条 patch 当作不在列表里**（上次应用过的照常
  还原，全量列表语义不变），并报一条 warning「编辑的对象已找不到（脚本结构可能已改动，
  未应用）: gid.prop」；
- gid 整个不存在 → 走原来的「元素不存在」，不变；
- 没带 `identity`（旧文档、跨图同步）→ 按位置匹配，与引入前逐字节一致；
- 带了但不是非空字符串 → 同样不应用（不许退回按位置匹配，那正是要堵的路）；
- 非空但方案前缀不认识 → 不核对（给以后换方案留余地；老构建本来就不核对）。

会话快照同样带着身份：已应用条目的身份记在 `FigState.applied_identity`，`FigSession.snapshot()`
原样交出。native 屏障（`tavotto run`）离开时存的就是这份快照、下一个屏障 rebase 按它重放——
只存 gid / prop / value 的话，脚本在两个屏障之间删 / 插 / 重排带 label 的对象，编辑又按位置
落到别的对象上（#602 评审 P1）。

写回不用另加任何东西：verify 的一次性 worker 按同一组 patches 从零重放，被拒的那条是一条
worker warning，按写回事务的既有规矩**一条即阻断**（409 `write_back_warnings`），原件零改动。

### 二、身份取什么：只取脚本显式起的 label（`l1:` + sha256 前 16 位十六进制）

`overrides.artist_identity(artist)`：Artist 的 `get_label()`（系列伪元素取容器的），
**空串与 `_` 开头的不算**（`_child3` 按加入顺序编号、`_nolegend_`——本质仍是位置）。
在 **baseline 那一刻**采（`manifest._register`，`instrument` 在任何 apply 之前跑；native
屏障 rebase 先 `apply([])` 退回脚本原样再重登记，`instrument` 清表重采）——label 本身是
可编辑的 prop，编辑之后再采，采到的是用户的编辑而不是脚本里的对象。manifest 给有身份的
元素发 `identity` 字段。

逐项取舍（这是「最小正确」与「覆盖面」之间的选择）：

| 候选字段 | 取不取 | 理由 |
| --- | --- | --- |
| 显式 label | **取** | 用户起的名字，图例里显示的那个；重排 / 插入 / 删除 / 挪子图都不变，换数据也不变 |
| artist 类型 / role | 不取 | gid 前缀已经带着类型；role 是 Tavotto 的内部命名，将来改名会把全部存量编辑判失效 |
| 数据点数 / 数据摘要 | 不取 | 用户换一批数据重跑是日常，那时编辑本该跟着同一条曲线走；按数据核会把正确的编辑拒掉（误拒同样是可见的，但它让「按数据重跑」这件最常见的事每次都丢编辑） |
| 父 axes 指纹（标题 / 轴标签文字） | 不取 | 轴标签文字常被用户在脚本里改（修错字），改一个字就把这个子图上的全部编辑判失效；子图挪位在叶子这一级已经被 label 核到 |
| 文字内容（Text 的字） | 不取 | 同上：修一个错字不该让拖过位置的注释失效 |

存摘要不存原文：文档与诊断包里只出现十六进制串，label 原文不出门。

**已知边界（明写，不假装覆盖）**：没有显式 label 的对象（大量 `ax.plot(x, y)`、坐标轴本身、
标题 / 轴标签 / 刻度 / 注释文字）没有身份，仍按位置匹配——对它们来说位置之外没有稳定、
不误拒的身份可取。两条同名曲线互换位置同样核不出来。旧文档里的 patch 不带身份，也仍按位置。

### 三、patchspec 规范形：`identity` 是可选的第四个键（两侧 + 向量一起改）

带了 `identity` 的 patch，渲染结果取决于它，所以它必须参与内容寻址（`patch_hash` 是写回
「热态是不是这组 patches」的判据、导出回执的语义身份、基线条目的身份）：

- 规范条目键序 `gid < identity < prop < value`（`sort_keys` 的结果）；**没带的条目规范形
  与引入前逐字节相同**——存量文档、写回基线里记着的 `patch_hash` 一个都不变；
- last-wins 以整条为单位（后一条没带就是没有，不从前一条继承）；
- 带了却不是非空字符串 → 剔除，原因 `bad_identity`（可见的剔除，与 `bad_gid` 同类）。

`engine/patchspec.py` ↔ `workerd/src/patchspec.rs` 严格同源对一起改，`tests/golden/patch_vectors.json`
加三组（`identity_participates` / `identity_last_wins_whole_entry` / `bad_identity_dropped`），
两侧各自逐字节断言。worker 协议本身（信封、命令表、`protocol_version`）不变：发给 worker
的一直是请求里那份原始列表，多一个键 worker 早就原样收到，只是以前不看。

### 四、前端：提交时抄写，不在写入点手填

`PanelOverride.identity?`（可选，不升文档 schema：旧构建读新文档时引擎不看这个键，行为
与今天一样；新构建读旧文档时没有这个键 = 按位置）。抄写在 **`documentStore` 的两个写入口**
（`commit` / `txnUpdate`）之后跑一遍 `lib/overrideIdentity.stampOverrideIdentities`，由渲染
同步方（`useEngineSync`）经 `registerOverrideStamper` 登记——二十来个写 override 的地方一个都
不用改，新增一个写入点也不会因为忘了抄而静默退回按位置。补丁并进同一条历史 / 事务。

抄写规则（`lib/overrideIdentity.ts`）：

1. 新写或改了值、且自己不带身份的 → 抄**提交之前用户看着的那一版** manifest（`panelRender`，
   显示用的就够：身份是脚本结构的事实，与 overrides 无关）里这个 gid 的身份；那个元素没有
   身份就去掉；manifest 里没有这个 gid / 还没画出来就不动；
2. 原地改值、身份是从旧条目原样继承来的（upsert 的展开写法）→ 同 1，按此刻重抄（用户在
   「现在的 lines_0」上改，这条编辑属于现在的它）；
3. 带着别的身份来的 → **不重抄**：它们有来历，按此刻重抄等于把旧编辑按位置绑到新对象上；
   **恢复不靠这一条认**：从存储原样读回来的一整份（布局版本 `restoreLayoutVersion`、写回历史
   `restorePanelOverrides`、写回基线 `seedBakedOverrides` / 换素材 / 重链接）由调用方显式
   `commit(…, { overrides: 'restored' })`，整次提交不抄，存的是什么就是什么（#602 评审 P1：
   当前文档与要恢复的版本在同一 (gid, prop) 上记着同一个身份、只是值不同——最常见的恢复——
   与「原地改值」长得一模一样，从身份相等推断会把它当 upsert 按此刻重抄）；
4. 值没变、身份却丢了（filter + push 同值重建）→ 放回原来的身份。

跨图同步（`/api/engine/sync_overrides`）是**有意**按位置映射到另一张图的另一个对象：后端
映射时去掉 `identity`（前端 `SyncOverridesButton` 的 `clean()` 本来就只留三个字段）。

界面不造新 UI：`ElementInspector` 已有的「清除失效修改」（孤儿 override）把「gid 还在、
身份对不上」也算进去（`isStaleIdentityOverride`，与引擎同一条判据）；导出结果区照旧列出
引擎 warnings；写回 409 照旧列出 warnings。

### 五、改名不是顶替：数据检查与 override 重放对“身份变了”的两种读法（2026-10-26 修订）

身份 = 显式 label 的摘要，所以同一个 gid 上“身份变了”有两种来源：**重编号**（前面删了一条，后面的对象顶上来，数据也变）
和**改名**（用户或 AI 改了 `label=`，数据一个字节没动）。§一的 override 重放分不开这两者——它只比身份，宁可拒绝
（拒绝是可见的、可清除的）。但 AI 改图的数据检查不该照抄：用户明确说“把 B 曲线的图例改成 …”，改 `label=` 是合法的，
把它判成 `subject_removed` 会让所有改图例的请求都过不了数据检查（实测 `multi_panel` 只改 B 的 `label=` 即得
`data.axes_1.lines_0 failed subject_removed`）。

**数据检查（`figureverify.data_unchanged`，提交后 L2 / 内置执行器 `check` 共用这一个函数）**：数据指纹是把两者分开的
独立证据。同 gid、两侧身份都有且不同时：

| 数据指纹（两侧都量到） | 新身份是不是改前**另一个数据元素**的旧身份 | 结论 |
| --- | --- | --- |
| 逐部件相同 | 否 | **verified**，`observed.relabeled = {from_identity, to_identity}`（改名，只有摘要） |
| 逐部件相同 | 是（名字对调 / 抢了别人的名字） | **failed `label_swapped`**（`observed.taken_from`）：数据顶着别人的名字，是贴错标签 |
| 不同，或任一侧没量到 | — | failed `subject_removed`（与改动前相同：保守） |

gid 没了仍是 `subject_removed`；身份相同数据变仍是 `data_changed`；任一侧缺身份不比（§二已知边界）。按序号定位的断言
（`line_label` / `line_data_unchanged`，FigureBench FB-S10 / FB-S11）同样把“只改名”当主体还在；其余断言（按 label 找
曲线、按位置的样式 / 文字断言）身份一变仍算顶替，行为不变。**不判意图**：把 B 改成与任何旧名字都不冲突的新名字、
但用户其实没要求改——数据检查放行；内置执行器 `check` 的报告会列出每一处可见文字变化让模型自己核对该不该留
（`ai_figure_edit.text_changes_note`），不做“目标范围”之类的机械判据（用户裁决）。

**override 重放仍是 §一的读法（身份对不上就拒绝）**——改名之后，画布上写在旧身份上的编辑会被拒，一条改图例的请求就让这条线的
全部编辑静默失效。所以**后端报改名、前端改写身份**：

- 后端：提交后 L2 里 verified 且带 `relabeled` 的数据检查，收成 `task.renamed = [{stem, gid, from_identity, to_identity}]`
  （闭集形状、只有身份摘要，`ai_figure_edit.renamed_from_verification`；`ai.done.task` 与会话详情带它，历史库的验证段里存着同一份，
  所以后端重启后 `get()` 仍能重建）。撤销（`POST /api/ai/sessions/<sid>/revert`）的回应带同样的 `renamed`。名字对调 /
  数据也变的不在其中——那些身份对不上本来就该被用户看见。
- 前端（`store/aiRenameRebind.ts` + `lib/overrideIdentity.rebindOverrideIdentities`）：`ai.done`（状态 done 且 changed）时把该 stem 的
  面板上、该 gid 处 `identity == from_identity` 的 override 改写成 `to_identity`（激活画布一条历史、`commit(…, {overrides:'restored'})`
  不让抄写器重抄；非激活画布直接写快照，同 `renameCanvas` 的先例）；撤销成功后（`aiStore.revert` / 历史列表的撤销）反向改回。
  **两个方向都幂等**：只改身份恰好等于起点的条目，没带身份的、带着别的身份的、已经是目标身份的一律不动，所以重放 / 另一窗口
  先改过 / 重复事件都无副作用；已被撤销的会话（状态 `reverted`）不会再被前向改写。
- 渲染：改写的是 `overrides` 内容，`useEngineSync` 自己渲染一次；撤销时改写先于 `markStale`，合并成一次。AI 提交后随
  `panel.file_changed` 来的重建先于 `ai.done`，所以前向改写前会有一次按旧身份的渲染（带“编辑的对象已找不到”warning），改写后
  再渲染一次；这是事件顺序决定的，不为省这一次把渲染挪到 `ai.done` 之后。
- 已知边界：存下来的布局版本 / 写回历史里的 override 仍是写下那一刻的身份（§四“恢复”不重抄），恢复到改名之后的脚本上会被拒、
  走既有的“清除失效修改”；只改名的数据检查依赖数据指纹量到（`over_budget` / `unavailable` 时保守按 `subject_removed`）。

## 看护

- `tests/test_override_identity.py`（真 matplotlib）：QA 的四个变体按 label 找洋红，只许在
  alpha 上或谁都没有 + warning 点名；同结构 / 换数据不误拒；不带身份按位置（兼容承诺）；
  同一热会话里被拒即还原；写回 409 `write_back_warnings` 原件零改动；跨图同步不带身份。
- `tests/test_patchspec.py` + `tests/golden/patch_vectors.json`（Python）/ `workerd/tests/golden_vectors.rs`（Rust）。
- `web/src/components/inspector/staleIdentityOverrides.test.tsx`：身份对不上的进「清除失效修改」、对得上的不进。
- `web/src/lib/overrideIdentity.test.ts`：抄写四条规则 + 真实写入口（`setOverride` / `setOverrides`
  经 `useEngineSync` 登记的抄写）+ 撤销一次连身份一起回去 + 两条真实恢复入口在「同一身份、
  值不同」时原样保留存下的身份（带普通编辑的对照）。
- `tests/test_figure_verifier.py`（改名 / 改名且数据变 / 名字对调 / 抢名字 / gid 没了，合成 manifest + 真 worker）、
  `tests/test_ai_figure_edit_app.py`（`task.renamed`、重启后重建、撤销回应）、`tests/test_override_identity.py::
  test_rebinding_the_identity_after_an_ai_relabel_keeps_the_edits_applying`（引擎：改写后的身份照常应用、旧身份被拒）、
  `web/src/store/aiRenameRebind.test.ts`（前后向改写、一条历史、幂等、多画布、`ai.done` 与撤销接线）。

## 已定（2026-09-25 用户拍板）

**只用显式 label。** 身份只取脚本显式起的 label，无 label 的对象仍按位置匹配（§二的已知边界）。

**「数据点数」方案被否**：给无 label 的曲线加点数一维能多核出一部分重排，但用户换一批数据重跑
（点数变了）时会把**正确的编辑**判失效并要求清除——换数据重跑是日常，误拒它的代价高于它多拦下
的那部分重排。以后若要加强，改 `artist_identity` 与方案前缀即可（已写下的 `l1:` 编辑要么同时
按旧规则核、要么明示迁移），其余链路不动。
