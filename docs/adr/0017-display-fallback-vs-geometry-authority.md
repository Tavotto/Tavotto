# ADR 0017：显示回退 ≠ 几何权威

状态：已实施（2026-08-26）
相关：`web/AGENTS.md` 的「渲染态：按文件 + 变体分键」（Phase F，本轮不改键的
定义，只改**谁能读它**）；issue #131。

## 背景

Phase F 之后，渲染态按 `fileId + JSON.stringify(overrides)` 分键，并留了一条
刻意的退路：面板自己那份变体还没画出来时，`panelRender()` 把 manifest / SVG /
rev **退回该文件最近画好的那份**（`latest[fileId]`），否则每敲一个字画布都会
闪回磁盘原图。这条退路本身是对的，用户体验上也必须留着。

问题在于**没有人规定谁可以读那个退回来的对象**。`usePanelManifest()` 返回的是
裸 `Manifest | null`，调用点看不出这一份是自己的还是别人的。于是几何写路径也
读它：

- 多选左对齐拿**上一版的墨迹 bbox**配**当前版的锚点**算落点——`alignEntries`
  的 anchor 走 `anchorOf()`（优先读 override，已经是新的），bbox 却直接取
  `el.bbox`（还是旧的）。两个变体的数混在一个减法里，算出来的位置不属于任何
  一版；
- 命中测试、框选、选择框、吸附候选、axes 随行、孤儿 override 判定同理。

issue #131 的用户环境把窗口放得很大：Windows 桌面版、脚本 heavy，诊断包里
每次渲染往返 0.55–0.78 秒，而日志显示用户的编辑动作最密时相隔约 0.7 秒。
改完字号立刻点左对齐，几乎必然落在退回窗口里。那份 app.log 一行错误都没有
（`recent_errors: []`、无 worker 重启），因为事故整个发生在前端「哪个变体
说了算」这一层。

## 决策

**旧 SVG 可以继续显示；旧 manifest 不得作为几何写操作的权威输入。**

两套 API，职责写在名字里：

| | 显示 | 几何权威 |
| --- | --- | --- |
| 取值 | `panelRender` / `usePanelRender` / `usePanelDisplayManifest` / `panelDisplayView` | `exactPanelRender` / `exactPanelManifest` / `useExactPanelManifest` |
| 可以退回 `latest[fileId]` | 是 | **否** |
| 用途 | 画布贴图、列元素、认 role、角标 | 一切读 bbox / anchor / position / geometry / arrow_endpoints / follow_gids / size_mm 之后**写文档**的操作 |

权威判据四条，缺一不可：

1. 条目就是 `byKey[renderKeyOf(panel)]`——不许退回 `latest[fileId]`；
2. 真的有 manifest；
3. `lastPatches` 与当前 overrides **逐字**相等（这一版确实画出来过）；
4. 没被 `markStale` 标记（脚本改过，旧墨迹框可能整个不作数）。

**刻意不要求 `status === 'ready'`**：同一个键重发一次（松手补定稿 dpi）会把
状态打回 `rendering`，而键相同 = overrides 相同 = 几何不变；渲染失败时条目里
留着的也是**这一版**最后一次成功的结果。真正会让几何失效的是「换了变体」与
「脚本变了」，前两条已经盖住。

类型层兜底：`panelDisplayView` 是判别联合，`fallback` / `empty` 分支在类型上
就**没有** `manifest` 字段。退回来的墨迹框想进写路径，得先过 TypeScript 这关，
而不是靠一句注释提醒后来人。

## 权威缺席时的行为

不是「禁用一切」，也不是「用错的几何硬撑」：

- 画布**照常显示**上一张 SVG（`fallback`），不闪白；
- 命中层整个停摆（`pointer-events: none`），不 hover、不命中、不框选；
- 选择框一个都不画——画在上一版位置上的框既对不上图、还能拖；
- `selectedGids` **不清空**，精确 manifest 回来后框自己复位；
- 对齐等入口置灰并说明「正在同步图内布局」；
- 纯样式类改动（颜色、线宽、线型、alpha、visible）不依赖 bbox，继续走局部
  SVG 预览，不受这道闸影响。

## 配套：撤销的落点必须还在

权威判据修好之后还剩一个视觉症状：撤销回到上一版时，那一版的精确渲染可能
已经被 `prune` 清掉（对齐渲染成功的那一刻 `latest` 就挪走了，旧变体不再被
任何人引用），于是撤销只剩重渲染，而重渲染期间画布继续显示撤销前的样子——
用户看到的就是「撤销没反应」。

- 每个文件保留最近 **4 档**成功变体（`recent`），上限是硬的；`markStale` /
  `reset` / `clear` 一并释放。撤销回到刚才那一版时精确 SVG 与 manifest 当场
  就在，同步器也不会为它再跑一次引擎。
- `latest` 改按**请求序号**推进，序号在**请求进来的那一刻**取（忙时排队的
  那次带着自己的序号走完全程）。乱序返回时旧变体只入库、不挪 `latest`——
  但也**不丢弃**，同文件的另一个副本可能仍在等它。

## 代价

- 内存：每文件 4 份 SVG。imshow 面板最坏约几 MB/文件，有明确上限，
  `prune` 同时收敛 `recent` 索引本身。
- 交互：权威缺席的那 0.5–0.8 秒里图内几何交互不可用。这是刻意的——那段时间
  里做的任何几何操作本来就写不对，只是以前不报错。

## 看护

`web/src/store/geometryAuthority.test.ts`（权威判据、同文件双副本隔离、乱序、
有界缓存）、`web/src/store/alignAction.test.ts`（权威闸、no-op、事务边界、
不许部分写入）、`web/src/canvas/alignUndoConvergence.test.tsx`（对齐 → 撤销 →
乱序返回后文档/SVG/manifest/选择框收敛到同一版）、
`web/src/lib/authorityTrace.test.ts`（诊断环的有界性与脱敏边界）。

测试装置 `web/src/test/renderFixtures.ts` 的 `seedExactRender()` 是「这一版已经
精确画好」的唯一定义：手写 `{manifest, status:'ready'}` 造出来的是真实渲染
永远不会出现的形状（有 manifest 没 `lastPatches`），那正是权威判据要挡的东西。
