# SVG payload 的字节预算（2026-08-29，issue #181 Session 04）

> 原文出自 `web/AGENTS.md`「SVG payload 的字节预算（2026-08-29，issue #181 Session 04）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

**条目数不是字节预算。** `docs/rules/frontend/display-fallback-vs-geometry-authority.md` 里那条 `RECENT_VARIANTS = 4` 管的是「留几档语义
状态」，它对「留了多少字节」一无所知——hybrid 之后仍有 8～12 MiB 的预览 SVG
乘以 4 档乘以几个文件，就是几百 MB 常驻在 JS 堆里，而每一份都是合法的撤销
落点，`prune` 一个都不该清。所以 `renderStore` 里**两条策略并存**：

```text
entry-count policy   RECENT_VARIANTS = 4        管「留几档语义状态」
byte-budget policy   SVG_RECENT_BUDGET_PER_FILE = 16 MiB
                     SVG_RECENT_BUDGET_GLOBAL   = 64 MiB
```

* **超预算时丢掉的只有 `svg` 字符串**（`dropSvgPayload`）：manifest / rev /
  lastPatches / wantPatches / timings / preview 元数据 / status / stale 一个字
  都不动。**「budget exceeded → delete PanelRender」是错的**——那会连撤销、
  版本恢复与几何权威一起丢掉。语义状态 ≠ SVG 源 payload。
* 记账**每次现算**（`residentSvgBytes`），不维护计数器：账本是第二份权威，
  与 `byKey` 分叉之后没有任何信号说得清哪一份算数。判据吃 `svg != null`。
* 单份大小取后端的 `preview.svg_bytes`（= `stat().st_size`，与硬闸量的是同一
  个东西），老后端退回 `svg.length`。**两条路都不复制字符串**——为了量一个
  「太大了」的东西再复制一遍，正是这套预算要防的事。
* **三条 pin，一条都不能少**：画布上现存面板的变体键（`prune(live)` 每轮刷新
  的模块级 `liveKeys`）、每个文件的 `latest`（显示退路）、在途/渲染中。
  这三份是「清了就没画面」的那几份——**Codex 内嵌画布里那份 SVG 是唯一能
  显示的东西**（`previewPngUrl` 只对 raster 档有缓存位图，`panelSrc` 恒 null）。
  全被 pin 住时**宁可超预算**：预算管的是可驱逐的历史 payload，不是显示所需。
* 驱逐次序两维：先「不在 `recent` 里的」（没人会撤销回去），再按 `svgSeq`
  丢最久没更新的。只按第二维排（纯 LRU）会先丢真的撤销落点。
* 被丢掉的那一版在 `panelDisplayView` 里是**独立的一档 `evicted`**，不是
  `fallback`：画布挂的**就是这一版**，只是画法换成位图（与 `raster` 同一条
  链路）。掉进 `fallback` 的话诊断会说画布挂着**另一个变体**的图，而几何权威
  仍是这一版——issue #131 同款错配。
* `useEngineSync` 对 `svgEvicted` 的条目**重排一次渲染**（ADR 0022 §8 的
  「按 transport 可用路径重新请求」）：桌面/playground 有引擎位图顶着，
  内嵌画布没有第二条路。重画成功后那一版已经 live、被 pin 住，不会来回拉锯。
* 诊断事件 `render.svg_evicted`（file / variant / scope / bytes，全部按 ADR
  0016 的 allowlist 序列化）。
* 看护：`store/svgMemoryBudget.test.ts`、`canvas/panelPreviewMode.test.tsx`
  的 evicted 一条、`hooks/useEngineSync.test.ts` 的重排一组、
  `embedded/session.test.ts` 的种子记账两条。**新增判据前先跑一次变异**——
  本节 20 条错误实现逐条反证过，其中「种子那一帧不记账」最初是全绿的。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 条目数与字节两条策略并存
- 记账每次现算不维护计数器
- 三条 pin 一条不能少、全被 pin 住宁可超预算
- 被驱逐的是独立一档 `evicted`
- `useEngineSync` 对 `svgEvicted` 重排一次
