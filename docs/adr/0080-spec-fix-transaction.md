# ADR 0080：「问题」面板的修复改走后端事务——改完真的过了才提交，不过就一个字不改

日期：2026-09-24 · 状态：**Accepted**
相关：[0030 一份验证服务](0030-validation-and-problem-navigation.md)（`safe_auto` 三条判据，本 ADR 修订「落地」那一半）、
[0051 保留式规范化](0051-preserving-normalization.md)（本 ADR 是它的桌面入口，复用约定 / 比对 / 边距修复）、
[0029 Style / Spec / Export](0029-style-spec-profiles.md)。

## 问题

用户反馈：左侧「问题」面板点「全部处理」之后「图都变了，而且变得不合适；字体没换成；
很多样式没按预期改；越改越乱」。在真实界面（examples/figures 的 Fig1）复现到四件事：

1. **修复拿的是错的数字**：面板宽度停在磁盘 PDF（`bbox_inches="tight"`，73.3 mm）而
   原生图幅已同步成 80 mm，缩放比静默成了 0.917，合规的 8 pt 被判成 7.33 pt，修复再
   按同一比例反推写进 9.28。——这一条是几何同步的缺陷，由 PR #543 单独修掉。
2. **逐条算、盲写**：`lib/issueFix.ts` 每条规则只算「对我这条最省事的那个数」，刻度被
   提到 8.5 pt、比 8.25 pt 的轴标题还大（层级倒挂）；刻度字变大把轴标题挤得更出图幅
   （1.87 → 2.04 mm）——没有任何一步看过改完的图。
3. **「全部处理」连建议档一起改**：轴标题被顺手加粗（`text-weight-policy` 是 suggestion）。
4. **字体一条都不修**：`font-family-substituted` 是 `fix: 'none'`；刻度也没有字体入口。

2、4 的根是同一个：**前端没有渲染，判断不了「改完对不对」**，只能要么盲写、要么不修。

## 裁决

| 问题 | 裁决 |
|---|---|
| 谁算面板内部的修复 | **后端**：`engine/specfix.py`（纯标准库）+ `POST /api/engine/specfix`。前端不再算面板的计划（`issueFix.ts` 只剩画布标注字号与页宽） |
| 哪里违规 | 只认 `preflight.run()` 报出来的 gid（与问题面板同一个求值器，两侧靠 golden vectors 对齐），逐 gid 展开后按 `(规则, gid)` 配对 |
| 改成多少 | 按**页面上读者量到的 pt** 算、再按面板缩放换回脚本坐标（取整方向跟着目标走）；字号按角色层级抬升：标题 ≥ 轴标题 ≥ 其余，抬了下层就把上层补齐（`keep-hierarchy`），上层受自己的上限约束；线宽吸到最近档、等距取细；字体改成规范的 `font_family.latin` |
| 改完对不对 | 候选真实渲染一遍，`normalize.compare()` 对着 B0：受保护属性、结构、字体真的落成了那张脸、几何新增 / 加重；另加两条：**点名的问题真的不见了**（`not_resolved`）、**修复引入的 warn 级规范问题也挡**（规范化那边只挡 error——那边的目标是用户点的，这边每一处改动都是我们替用户挑的） |
| 装不下 | 新增 / 加重的裁切或文字压进别的子图时，用 ADR 0051 的外边距重排（`adapt_margins`）最多三轮；「收不收这一轮」只有 `normalize.better_candidate()` 一处 |
| 字体没装 | 字体那几条单独退出（`font_unavailable`，报出规范要的字体名），其余照修 |
| 不通过 | worker 回到 B0，回 `ok: false` 与原列表；前端**文档零改动**，原因走闭集 `FixFailureReason` |
| 回滚干不干净 | 只有**事务里每一次渲染都没有 warning、没有抛**才算回滚成功（判据收在端点的 `render` 闭包一处，不逐个回滚点各查各的）。否则——B0 重放、候选、外边距那一轮、任何一次回滚带 warning，或渲染 / 事务本体抛了——热态不是它声称的那一份：worker 一律作废（`app._retire_hot_worker` → `pool.invalidate`，与「重新构建」同一个原语），下一次请求重新起、按全量列表重放，响应带 `worker_retired: true` 与 `replay_required: true`（干净时两者都是 `false`），前端没提交结果时按此刻的列表重放，与结果不确定同一条路。引擎另有一条通用保证：`overrides.apply()` **还原失败不遗忘**（键留在 applied / originals，记进 `FigState.unrestored`，下一次 apply 重试，欠着一天每次渲染报一次 warning，还原成功才清账；Codex #549 第八轮 P1） |
| native 图（`tavotto run`） | **暂不支持自动修复**（2026-09-25 用户决定）。端点在任何一次渲染之前回 409 `specfix_native_unsupported`，那张 live 图一个字节都不碰——**先按描述符里存的档案判（`enginesession.profile_of`），再去解析会话**，会话已结束的 native 图也拿到这个码而不是 `native_session_offline`；事务途中档案变了（另一个 `tavotto run` 会话到了屏障、把描述符换成 native），每次渲染之前与提交之前各再比一次，变了就中止、回同一个码、不提交、safe worker 作废（Codex #549 第十一轮）；事务里**每一处**解析 worker（入口、依赖修复重试 `_engine_attempt` 里重新解析的那一次）都走 `_engine_worker(..., safe_only=True)`，解析出 native 会话就在调它之前抛 `NativeWorkerRefused`、同样中止（第十二轮；AST 守卫 `test_every_worker_resolution_inside_the_specfix_transaction_is_safe_only`）；前端问题照常列出，修复按钮不可用、悬停说一句为什么（`isNativePanelIssue`，判据与面板角标同一个出处 `runtimeAssetStore.profile`，未知按 safe），批量里它们计为 `native_unsupported`。理由：native 进程归用户（ADR 0021），不能作废重起，而事务回滚不干净时的兜底恰恰是作废 worker——在 native 上这条兜底不成立，热态与文档会不一致。native 的完整保证（图与文档不一致的标记、请求串行化、不一致时不放行 continue）在叠栈 PR（分支 `fix/native-figure-inconsistent`，base 为本 PR 分支）里做，那里决定何时放开（#626 维持禁用）。下一行的「与文档不一致」机制补的是**任何渲染**（不只是修复）留下的不一致；修复事务是多次渲染，要放开还得让整个事务在会话锁里成一段，未做 |
| native 图与文档不一致（所有渲染的通用保证） | 引擎还原失败留账（上一行的 `unrestored`）时，v1 render / export / preview_png 的结果带结构化的 `unrestored`（判据只看它，不解析 warning；export / preview 报的是**临时套用之后还原回会话列表**那一刻的账），`NativeSession` 据此记下这张图：只放行同一份列表的重渲染（重试，报 0 即自动解除），换列表的编辑、导出、历史预览、continue / detach 一律 `native_figure_inconsistent`（409，与 `native_session_offline` 同一条路，ADR 0021 §9.3）；「查标记 → 发请求 → 更新标记（含 continue 成功后的状态切换）」在会话锁里成一整段；runner 恢复不回去同样不放行（§8.1）。前端 `renderStore.inconsistent`（文件级）→ `nativePanelState` 的 `'inconsistent'` 角标「有改动没能还原，重新运行原命令可继续编辑」。重新运行 = 新会话、新 Figure |
| 结果不确定 | 请求抛了（回程断线、非 2xx）、或成功体里**任何一个下游要读的字段**形状不对（`ok` / `exit` / `patches` 每一项的 `{gid, prop, value}` / `skipped` 每一项的 `{rule, gid, reason}` / `worker_retired` / `replay_required`，校验在 `api.engineSpecfix` 一处）：文档不改、按此刻的列表重放 worker；读响应（`settle()`）也在同一个 catch 里，读到一半炸了同样算不确定 |
| 通过 | 回这张图**最终的全量 override 列表**；前端所有面板都回来之后**一次 commit**（⌘Z 一次撤回） |
| 等待期间文档被改过 | 丢弃结果（override 列表或 `loadSeq` 对不上），报 `stale`；同一时刻只跑一轮（`busy`） |
| 「全部处理」的集合 | `batchable()` 唯一出处：本画布、`safe_auto`、**不含建议档**；组头的「全部修复」是点名那一组，带 `includeSuggestions`；逐条的「修复」照修建议档 |
| 点了名却不修 | 规则不在可修清单 / B0 上没这条：逐条回 `skipped: not_found`，前端不计入「已修复」 |
| 出界（`element-outside-figure`） | 也进可修清单，但不在 `plan()` 里算：由外边距重排修（挪子图、不挪那条文字，`specfix.LAYOUT_RULES`），修复前就已出界的也修（只要这一轮「点名没修好的严格更少、阻断不增」，`specfix.progressed()`）；到预算仍放不下就记 `no_fit`，**不连累同批别的修复** |
| 修复结果的提示 | 后台渲染通知（渲染完成 / 正在构建）是**被动** toast（`uiStore.statusPassive`），不顶掉还挂着的非被动结果——验收里「已修复 8 项」只活了 30–50 ms 就被「渲染完成」盖掉 |
| 可修规则集 | `specfix.FIXABLE_RULES` ↔ `issueFix.ENGINE_FIX_RULES` 严格同源对 |
| 允许集合 | 按 prop 放行全图（刻度组 / 图例的字号字体落到子元素上是合法连带），外加 `COUPLED_PROPS` 里**实测**到的连带（`spine_linewidth` → 四边线宽；`linewidth` → 跟随源的图例示意线） |

## 为什么不在前端修好

前端能把层级、区间合并都算对，但算不出「刻度字变大之后轴标题还在不在图幅里」、
「这台机器上有没有 Times New Roman」、「改完是不是真的过了」——这些只有真实渲染
知道。把计划放在后端、贴着渲染与裁决，是 ADR 0051 已经走通的路；前端只剩「通过
才写」这一件事，写入仍是一次普通的 `documentStore.commit`（dirty / 撤销 / 自动保存
照常）。

## 代价与边界

* 点一下要等一到几次真实渲染（几秒）。界面在这期间把所有修复入口置灰并说「正在修复…」。
* 内嵌画布 / playground（装了替代传输）没有这个端点：面板内部的问题回 `unavailable`，
  画布层照修。
* 图例在自己子图里换位置（ADR 0051 的第二族局部修复）桌面这条暂不做：挡住就如实退出。
* 出界的修法只有外边距重排：它挪的是子图，放不下（例如 labelpad 大得离谱）就是 `no_fit`，缩字号、放大图幅仍要用户自己决定。
* 引擎渲染不套 `bbox_inches="tight"`，所以画布上「磁盘原图 → 引擎重渲染」本身就会让
  几何变一下——这是 ADR 0051 §3 记着的未决问题，本 ADR 不解决。

## 看护

`tests/test_specfix_real.py`（真 matplotlib：全部处理真的过了且层级不倒挂 × 两档缩放、
建议档不进批量、点名时照修、缺字体退出其余照修、裁决不过回到 B0、合规图 nothing_to_do、
点名未处理逐条报出、端点入参校验）、`tests/test_specfix.py`（合成 manifest 上的计划：
缩放换算与取整方向、层级补齐、图例区间交集、空区间不硬修、等距取细、批量集合、
逐 gid 展开、同源对）、`web/src/lib/issueFix.test.ts`（发出去的是什么、通过才写且只
写一次、五种退出码 → 原因、抛错 / 缺字体 / 过期 / busy / 无后端、批量集合、画布层、缺字段与 `worker_retired` / `replay_required` 走重放）、`web/src/lib/specfixResponse.test.ts`（成功体逐字段校验）、`tests/test_specfix_real.py` 的端点五条（干净拒绝不作废 / 回滚带 warning、回滚抛、B0 带 warning 都作废 / native 图在任何渲染之前被拒 / 会话已结束的 native 图拿到同一个码 / 事务途中变成 native：渲染前中止、提交前中止）、`tests/test_restore_failure_retry.py`（还原失败留账重试、欠账期间点回来原样不丢、别名组组员的代采原样不回收、还原成功后热态与冷启动重放像素 + manifest 逐字节相同）、
`web/src/components/left/problemPanel.test.tsx`（修复在跑时置灰；native 图的修复按钮不可用）、`web/src/lib/issueFix.test.ts` 的 native 一组（不发请求、不重放、后端拒绝报同一个原因）、`tests/native/test_native_inconsistent.py`（只放行同一份列表、报 0 自动解除、导出 / 历史预览与 offline 一样被拦、export / preview 还原失败记账、并发请求串行、continue 成功后的状态切换在锁内、continue / detach 拒绝与 terminate 放行、runner 拒绝放行的两条真进程链）、`web/src/store/renderStore.test.ts` / `nativeSessionStore.test.ts`（不一致标记按文件记、干净即解除、`'inconsistent'` 角标判据）、`tests/test_restore_failure_retry.py` 的 v1 字段用例。
