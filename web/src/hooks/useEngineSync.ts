import { artifactValidationIssue, isUnadoptedPngSource, artifactEntryPending, artifactEditIntentChanged } from '@/lib/artifactValidation'
/**
 * 引擎渲染的**订阅与生命周期装配**。调度（防抖 / 立即 / 占位 / 定稿）在
 * `store/renderScheduler.ts`，「只带基线、还没动过」的判据在 `lib/bakedBaseline.ts`；
 * 这个文件只回答「此刻哪些面板该发」（`renderTargets` / `syncEngine`）并把它挂到
 * React 的订阅上。**不 import `store/actions`**——actions 调调度器，不反过来调 hook。
 */
import { useEffect } from 'react'
import { isJustBakedBaselineOf, type BakedBaselineFacts } from '@/lib/bakedBaseline'
import { useAssetStore } from '@/store/assetStore'
import {
  registerOverrideStamper,
  registerTxnFinalizer,
  txnAnchorOf,
  useDocumentStore,
  type TxnAnchor,
} from '@/store/documentStore'
import { panelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { requestRender } from '@/store/renderScheduler'
import { sampleDisplayState } from '@/diagnostics'
import { useUiStore } from '@/store/uiStore'
import { currentVisibleRectMm } from '@/store/viewportStore'
import { useWorkspaceStore, type WorkspaceMode } from '@/store/workspaceStore'
import { panelRotation } from '@/types/document'
import { syncPanelNativeSize } from '@/lib/panelNativeSize'
import { stampOverrideIdentities } from '@/lib/overrideIdentity'
import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'

/**
 * 需要引擎渲染的面板，**按 (fileId, overrides) 去重**。
 *
 * 这里曾经是「每个 fileId 只能有一个说了算的面板」的裁决：渲染态按文件索引，
 * 两个同文件不同 override 的副本会同步互顶 wantPatches，effect ↔ store
 * 无限互相触发（React #185）。代价是输家永远显示赢家的图。现在渲染态按变体
 * 分键（renderKeyOf），两个副本各有各的条目，互不覆盖——真正的多变体支持，
 * 去重只剩「完全相同的两个副本共用一次渲染」这一条。
 */
const NO_NEIGHBOURS: ReadonlySet<string> = new Set()

export function renderTargets(
  objects: readonly CanvasObject[],
  editingId: string | null,
  tracked: Record<string, boolean | undefined>,
  latest: Record<string, string | undefined> = {},
  // 素材的基线事实由调用方给；不给就读素材表（与 syncEngine 同一份）
  assets: Record<string, BakedBaselineFacts | undefined> = useAssetStore.getState().byId,
  /**
   * 这张画布绑定了样式（ADR 0081）。绑定说的是「这张画布上的图长成那套样式的样子」，
   * 要对齐一张图就得先读到它的 manifest——所以绑定画布上「只带基线、还没动过」的
   * 面板也渲染一次。这不是「白跑」：用户选绑定的那一刻就是在要这个结果。
   * runtime 面板照旧（重开文档绝不自动执行脚本）。
   */
  styleBound = false,
  /**
   * 跨图对齐的邻居（ADR 0128 §二，`crossFigureNeighbours` 算出的面板 id）：进图内编辑时，
   * 视口里看得见的别的图也要有权威 manifest，否则跨图候选一条线都出不了。与 `styleBound` 同一个
   * 理由与同一条纪律：「只带基线、还没动过」的非 runtime 面板也渲染一次；runtime 面板照旧
   * （重开文档绝不自动执行脚本）。谁算邻居由调用方按当前状态给，这里保持纯函数。
   */
  neighbours: ReadonlySet<string> = NO_NEIGHBOURS,
): PanelObject[] {
  const seen = new Set<string>()
  const targets: PanelObject[] = []
  for (const o of objects) {
    if (o.type !== 'panel' || !o.script) continue
    // runtime 面板（ADR 0013 lazy rehydrate）：**重开文档绝不自动执行脚本**。
    // 只有「正在编辑」或「本会话已经跑过一次（latest 里有它）」才进同步——
    // 带着 overrides 重开的文档先显示 cache 占位，进入编辑 / 显式重跑那一刻
    // 才 build 并重放。tracked（脚本变更）对 runtime 只表达 stale 提示，
    // 不构成自动重跑的理由。
    // 未采用的 PNG 源图（#1145）：邻居（`crossFigureNeighbours` 已剔）与 tracked 这两条「顺手补渲染」只能发
    // 无 source_policy 的 patches=[]，让脚本重跑出一张没对过字节的图；它的唯一合法首渲染是 admitPngEntry
    // 的带策略采用请求（进编辑才走）。**styleBound 例外，照旧渲染**（Codex #1179 r4313254705）：
    // 绑定画布上 admitPngEntry 因「挂着样式 / 有样式债」拒绝进入，采用请求永远发不出；而样式债只有
    // 这张图拿到 manifest 才能由 `alignNewFigures` 结清——这次渲染是它唯一的 manifest 来源。后端对缺
    // policy 的 PNG 渲染只标 `source_unverified`、不拒绝（旧请求语义不变），所以这条路本来合法。
    const unadopted = !Object.hasOwn(o, 'artifactValidation') && isUnadoptedPngSource(o)
    const wants =
      o.fileKind === 'runtime'
        ? o.id === editingId || latest[o.fileId] != null
        : // 编辑中 / 有图内修改 / 脚本已领先磁盘文件（AI 改过）。
          // 「只带基线、还没动过」的面板不渲染：磁盘文件本身就是那个样子，
          // 白跑一次引擎（heavy 脚本要几分钟）没有意义。
          o.id === editingId ||
          styleBound ||
          (!unadopted && (neighbours.has(o.id) || !!tracked[o.fileId])) ||
          (o.overrides.length > 0 && !isJustBakedBaselineOf(o.overrides, assets[o.fileId]))
    if (!wants) continue
    const key = renderKeyOf(o)
    if (seen.has(key)) continue
    seen.add(key)
    targets.push(o)
  }
  return targets
}

/**
 * 进图内编辑时需要补渲染的「跨图邻居」（ADR 0128 §二）：排版模式（快速编辑里别的图让开了，
 * 不跨图）、有正在编辑的面板，别的面板满足——当前视口里看得见（与跨图候选同一个判据：外框与
 * `visibleRectMm` 相交，视口尺寸未知不剔）、未隐藏、非 runtime、`cost !== 'heavy'`、未旋转未翻转、
 * 有脚本。**不订阅视口**：平移 / 缩放不触发补渲染，邻居在进编辑（`elementPanelId` 变化）与
 * 文档变化时按当时的视口算一次；之后才平移进视口的图，下次进编辑才补。
 */
export function crossFigureNeighbours(
  objects: readonly CanvasObject[],
  editingId: string | null,
  mode: WorkspaceMode,
  visible: { x: number; y: number; w: number; h: number } | null,
): Set<string> {
  const ids = new Set<string>()
  if (!editingId || mode === 'fast_edit') return ids
  for (const o of objects) {
    if (o.type !== 'panel' || o.id === editingId || o.hidden || !o.script) continue
    if (o.fileKind === 'runtime' || o.cost === 'heavy') continue
    // 未采用的 PNG 源图没有权威 manifest 可补：补渲染只能发无策略的 patches=[]，让脚本重跑出一张
    // 没对过字节的图，还会和它自己的采用请求抢同一份首渲染。要先走 admitPngEntry（带 source_policy）。
    if (!Object.hasOwn(o, 'artifactValidation') && isUnadoptedPngSource(o)) continue
    if (panelRotation(o) || o.flipH || o.flipV) continue
    if (
      visible &&
      (o.x >= visible.x + visible.w ||
        o.x + o.w <= visible.x ||
        o.y >= visible.y + visible.h ||
        o.y + o.h <= visible.y)
    )
      continue
    ids.add(o.id)
  }
  return ids
}

/** 文档里现存（含其它画布）的全部面板变体键——prune 的保留名单 */
function liveRenderKeys(objects: readonly CanvasObject[]): Set<string> {
  const keys = new Set<string>()
  const add = (objs: readonly CanvasObject[]) => {
    for (const o of objs) if (o.type === 'panel') keys.add(renderKeyOf(o))
  }
  add(objects)
  // 非激活画布的面板也在渲染（常驻图层），它们的条目同样不能被清掉
  for (const c of useDocumentStore.getState().canvases) add(c.objects)
  return keys
}

/**
 * 同步一轮：把还没排期的变体发出去，再清掉没人引用的旧变体。
 * effect 与测试共用同一份判断——「同步会不会自己把自己转起来」这件事必须
 * 能在测试里直接跑（旧实现的死循环就是在这一层）。
 */
export function syncEngine(objects: readonly CanvasObject[], editingId: string | null): void {
  const active = objects.find((o): o is PanelObject => o.type === 'panel' && o.id === editingId)
  if (artifactEditIntentChanged(active)) {
    useUiStore.getState().setElementPanel(null)
    editingId = null
  }
  const store = useRenderStore.getState()
  const assets = useAssetStore.getState().byId
  const style = useDocumentStore.getState().doc.style
  const styleBound = !!style && !style.detached
  const neighbours = crossFigureNeighbours(
    objects,
    editingId,
    useWorkspaceStore.getState().mode,
    currentVisibleRectMm(),
  )
  for (const panel of renderTargets(
    objects,
    editingId,
    store.tracked,
    store.latest,
    assets,
    styleBound,
    neighbours,
  )) {
    const want = JSON.stringify(panel.overrides)
    const state = store.byKey[renderKeyOf(panel)]
    // `svgEvicted` 打断这条跳过：这一版确实画出来过（lastPatches 对得上、
    // manifest 与几何权威都在），但它的 SVG payload 被内存预算清掉了。撤销
    // 回到这一档时**不重画就没有矢量图可挂**——桌面 / playground 还能走引擎
    // 位图顶一阵，Codex 内嵌画布里连那条路都没有（`previewPngUrl` 只对 raster
    // 档有缓存位图），画面会直接空掉。ADR 0022 §8 允许「重新请求」，这就是它。
    // 重画成功后 `svgEvicted` 归 false，而那一版此刻已经 live，被 pin 住不会
    // 再被驱逐——不会来回拉锯。
    if (state && !state.svgEvicted && (state.lastPatches === want || state.wantPatches === want))
      continue
    // 进入编辑态的首次渲染立即发出，其余（打字等）走防抖
    requestRender(panel, !state)
  }
  // 编辑期每改一个值就多一条变体（各带一份 SVG）：没人再引用的当场清掉
  useRenderStore.getState().prune(liveRenderKeys(objects))
  // 诊断：三个变体身份的采样点就挂在这里——同步这一轮**本来就只在真状态
  // 变化时跑**，而 sampleDisplayState 载荷没变就不记，于是稳态下它一条都不写。
  // 不挂在 React render 里：那会在每一帧算一遍 JSON（ADR 0016 §15）
  for (const o of objects) if (o.type === 'panel') sampleDisplayState(o)
}

/**
 * 引擎渲染的唯一驱动点：只要「文档里的 overrides」与「已渲染的 patches」不一致
 * 就重渲染。撤销/重做、AI 改脚本、文件变更全部经由同一条路径，无需各自触发。
 *
 * 由两半组成：文档一侧（`useEngineDocumentSync`）与渲染态一侧（`EngineRenderSync`）。
 * 主应用把两半挂在不同的地方（见 `useEngineDocumentSync` 的注释）；嵌入式画布 /
 * playground 没有那棵大树，用这个合起来的版本。
 */
export function useEngineSync() {
  useEngineDocumentSync()
  useEngineRenderSync()
}

/**
 * 同步器的**文档一侧**：文档 / 编辑态 / 素材事实变了 → 同步一轮。
 *
 * 渲染态那一侧必须挂在一个不画任何东西的叶子组件里（`EngineRenderSync`），不能跟
 * 这一侧一起挂在 Workspace 上：宿主订阅了什么，它下面整棵树（顶栏 / 左栏 / 属性栏）
 * 就跟着重画什么，而渲染态在每次新图到达时要变两三回（响应入库、`prune` 清掉掉出
 * 近期档的旧变体、`wantPatches` 占位）——挂在 Workspace 上，新图到达就会出现第二次
 * 同样重的整树提交（2026-09-24 剖析：58 个元素的图上每次 App 约 7ms）。
 */
export function useEngineDocumentSync() {
  const objects = useDocumentStore((s) => s.doc.objects)
  const editingId = useUiStore((s) => s.elementPanelId)
  // renderTargets 的判据里有 isJustBakedBaselineOf，喂给它的是素材表（baked_overrides /
  // baked_current）。素材表变了（写回完成、SSE 报文件被外部改写后 load()）
  // 判据结论可能翻转——不订阅的话，「磁盘产物被外部刷回脚本原值」那一刻
  // 没有任何东西会让同步器重新看一眼，面板就此停在磁盘原图上。
  const assets = useAssetStore((s) => s.byId)
  // 绑定 / 解绑样式只改 `doc.style`、对象数组可能一个都没换（图已经合样式）——
  // 不盯着它的话，刚绑上的画布里没渲染过的图要等到下一次别的变化才去渲染
  const styleBound = useDocumentStore((s) => !!s.doc.style && !s.doc.style.detached)

  useEffect(() => {
    syncEngine(objects, editingId)
  }, [objects, editingId, assets, styleBound])
}

/** 同步器的渲染态一侧，挂成不画任何东西的叶子（理由见 `useEngineDocumentSync`）。 */
export function EngineRenderSync(): null {
  useEngineRenderSync()
  return null
}

/**
 * 渲染回来了 / 跟踪位变了 → 再看一眼还有没有要发的。判断本身在 syncEngine 里读的是
 * 最新 state，文档与编辑态也在那一刻现取（这一侧不订阅编辑态）。
 */
function useEngineRenderSync() {
  const objects = useDocumentStore((s) => s.doc.objects)
  const byKey = useRenderStore((s) => s.byKey)
  const tracked = useRenderStore((s) => s.tracked)

  useEffect(() => {
    syncEngine(useDocumentStore.getState().doc.objects, useUiStore.getState().elementPanelId)
  }, [byKey, tracked])

  const txnOpen = useDocumentStore((s) => s.txn != null)

  // 渲染回来的图幅尺寸变了（改了 size_mm）→ 同步面板原生尺寸，页面尺寸按同一比例跟着走。
  // 平时是由渲染反推的派生值，不进历史（silent）。**事务开着时一个字都不写**：
  // 缩放 / 裁剪 / 数值拖动都在按下时抓了几何，每一帧按它写绝对的 w/h——中途改掉
  // nativeW，下一帧就把缩放后的尺寸盖回旧几何，而 nativeW 已经对上、这里从此不再修，
  // 横纵缩放比就此不一致。留到事务收尾（`registerTxnFinalizer`）在压缩前并进同一条
  // 历史：撤销整体回到事务前的旧图幅（同步器随即 silent 补一次），重做回到松手那一刻。
  // 丢弃的事务不跑收尾，回滚后由这里 silent 补（txnOpen 在依赖里：空事务回滚不换 doc）
  // 已经落进历史的条目不在这里修：它们各自记着 w/h 是按哪个原生图幅量的（`sizeBasis`），
  // 撤销 / 重做落地时由 `documentStore` 换算到此刻的图幅
  useEffect(() => {
    if (txnOpen) return
    const fixes = nativeSizeFixes(objects, byKey)
    if (fixes.length) useDocumentStore.getState().silent((d) => applyNativeSizeFixes(d, fixes))
  }, [byKey, objects, txnOpen])

  useEffect(
    () =>
      registerTxnFinalizer((d) =>
        applyNativeSizeFixes(
          d,
          nativeSizeFixes(d.objects, useRenderStore.getState().byKey).map((f) => ({
            ...f,
            // 手势刻意钉住的那一点（缩放的对边 / 裁剪的整图锚点）
            anchor: txnAnchorOf(f.id),
          })),
        ),
      ),
    [],
  )

  // override 的目标身份（ADR 0083）：提交时给新写 / 改了值的 override 抄上**用户此刻
  // 看着的那一版** manifest 里的身份（显示用的就行：身份是脚本结构的事实，与这个面板
  // 的 overrides 无关；退回来的同文件另一版同样作数）
  useEffect(
    () =>
      registerOverrideStamper((draft, base, next) =>
        stampOverrideIdentities(draft, base, next, (panel) => panelRender(useRenderStore.getState(), panel)?.manifest),
      ),
    [],
  )
}

type NativeSizeFix = {
  id: string
  wMm: number
  hMm: number
  /** 换算时在页面上不动的点；不给 = 包围盒左上角（x/y 不变） */
  anchor?: TxnAnchor
}

/**
 * 按**面板自己那份变体**取渲染回来的尺寸：size_mm 本身就是可以被 override 的，
 * 同文件的另一个副本改了图幅，不该把这个副本一起拽走。
 */
function nativeSizeFixes(
  objects: readonly CanvasObject[],
  byKey: ReturnType<typeof useRenderStore.getState>['byKey'],
): NativeSizeFix[] {
  const fixes: NativeSizeFix[] = []
  for (const o of objects) {
    if (o.type !== 'panel' || artifactValidationIssue(o) || artifactEntryPending(o.id)) continue
    if (!o.overrides.length && o.fileKind === 'raster' && /\.png$/i.test(o.fileId) && useUiStore.getState().elementPanelId !== o.id && !useRenderStore.getState().tracked[o.fileId]) continue
    const size = byKey[renderKeyOf(o)]?.manifest?.size_mm
    if (!size) continue
    const [wMm, hMm] = size
    if (Math.abs(o.nativeW - wMm) <= 0.05 && Math.abs(o.nativeH - hMm) <= 0.05) continue
    fixes.push({ id: o.id, wMm, hMm })
  }
  return fixes
}

function applyNativeSizeFixes(d: FigureDocument, fixes: readonly NativeSizeFix[]) {
  for (const fix of fixes) {
    const o = d.objects.find((x) => x.id === fix.id)
    // 换算（缩放比不变、旋转时宽高互换、按锚点反推 x/y）只在 `lib/panelNativeSize`
    // 一处：撤销 / 重做落地时的换基走的也是它
    if (o?.type === 'panel') syncPanelNativeSize(o, fix.wMm, fix.hMm, { anchor: fix.anchor })
  }
}
