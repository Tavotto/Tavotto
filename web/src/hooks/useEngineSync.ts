import { useEffect } from 'react'
import { isJustBakedBaseline } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { panelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { sampleDisplayState } from '@/diagnostics'
import { useUiStore } from '@/store/uiStore'
import {
  panelRotation,
  rotationSwaps,
  type CanvasObject,
  type PanelObject,
} from '@/types/document'

/** 文字/数值输入合并成一次渲染的窗口；颜色、开关、拖动结束走 immediate */
const DEBOUNCE_MS = 300

/**
 * 连续调整期间的预览 dpi。**只给含图像（imshow 等）的面板**：实测那里
 * 200→100 让一次渲染的往返降 16%、SVG 体积降 75%；纯矢量面板上耗时与字节数
 * 完全相同（docs/perf-baseline.md 补测两张表），给了只会白白让图变糊。
 * 定稿（immediate / flushRender）永远用默认 dpi。
 */
const INTERACTIVE_PREVIEW_DPI = 100

/**
 * 防抖计时器按**面板**索引，不按变体：连着改同一个值会走出一串变体键，
 * 按变体存的话每个中间值都会在 300ms 后各渲染一次（打十个字 = 十次渲染）。
 * 也不能按文件——同文件的两个副本各调各的，互相取消就会有一个永远渲染不出来。
 */
const timers = new Map<string, number>()

/** 该面板的图里有没有位图元素（imshow / 图片）——降质预览只对它们有收益 */
function hasImageElement(panel: PanelObject): boolean {
  // 走 panelRender：变体刚换、自己那份还没画出来时退回文件最近那份。
  // 元素构成不随 override 的取值变化，用哪个变体的 manifest 判断都一样
  const manifest = panelRender(useRenderStore.getState(), panel)?.manifest
  return !!manifest?.elements.some((el) => el.role === 'image')
}

/**
 * 渲染策略。**与历史无关**——无论选哪个，文档改动都已经经过
 * documentStore.commit 进了历史；这里决定的只是「什么时候麻烦 matplotlib」。
 *
 *   immediate  立刻发（定稿：松手、颜色定稿、枚举、撤销/重做）
 *   defer      防抖 300ms 后发（打字、连续数值输入）
 *   none       **本轮完全不发**，只登记 wantPatches 占位；由 flushRender
 *              在手势结束时定稿。假实时的 scrub / 取色走这条：拖动期间
 *              画面由 SVG 局部预览负责，matplotlib 一次都不用跑。
 *
 * `none` 必须照样写 wantPatches：syncEngine 的跳过判据就是它，不占位的话
 * 同步 effect 会立刻替这次改动发一次 immediate 渲染——比不加策略还糟。
 */
export type RenderPolicy = 'immediate' | 'defer' | 'none'

const policyOf = (p: boolean | RenderPolicy | undefined): RenderPolicy =>
  p === true ? 'immediate' : p === false || p == null ? 'defer' : p

/**
 * 请求渲染。同一面板的连续请求会被合并：debounce 期内只保留最后一次，
 * 真正发出后由 renderStore 的 busy/queued 再兜一层。
 */
export function requestRender(panel: PanelObject, immediate: boolean | RenderPolicy = false) {
  const policy = policyOf(immediate)
  const store = useRenderStore.getState()
  const key = renderKeyOf(panel)
  const want = JSON.stringify(panel.overrides)
  // 值没变就别写 store：patch() 会换掉 byKey 的引用，把依赖它的 effect
  // 全部重跑一遍——白白多一轮渲染，也是同步循环的燃料
  if (store.get(key).wantPatches !== want) {
    store.patch(key, { fileId: panel.fileId, wantPatches: want })
  }

  // 防抖那一路是「还在调」，可以先给一张低清；immediate 是定稿，永远默认 dpi
  const dpi = policy !== 'defer' || !hasImageElement(panel) ? undefined : INTERACTIVE_PREVIEW_DPI
  const patches = panel.overrides
  const fileId = panel.fileId
  const fire = () => {
    timers.delete(panel.id)
    void store.render(fileId, patches, dpi, policy)
  }
  window.clearTimeout(timers.get(panel.id))
  timers.delete(panel.id)
  if (policy === 'none') return
  if (policy === 'immediate') fire()
  else timers.set(panel.id, window.setTimeout(fire, DEBOUNCE_MS))
}

/**
 * 立刻冲刷该面板挂起的渲染，并保证最终那张是定稿质量（松开滑块、退出输入框）。
 *
 * 两件事都必须做：挂起的那次直接发出去；已经画完但用的是降质 dpi 的，
 * 补一张默认 dpi 的——否则用户手一松，图就永远停在临时低清上。
 */
export function flushRender(panelId: string) {
  // 按 id 从文档里现取，不信调用方手里那份：事件处理器闭包里的 panel 可能是
  // 上一帧的，拿它的 overrides 去渲染就等于把刚改的那一版丢了（而挂起的
  // 计时器已经被清掉，同步器又因为 wantPatches 相等而跳过 → 永远画不出来）
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') return
  const store = useRenderStore.getState()
  const pending = timers.get(panelId)
  window.clearTimeout(pending)
  timers.delete(panelId)
  const want = JSON.stringify(panel.overrides)
  const state = store.get(renderKeyOf(panel))
  // 判据是「这一版还没画出来」，不是「有没有挂起的计时器」。
  // 旧实现只看计时器：render:'none' 的手势（scrub / 取色）压根不设计时器，
  // 松手时就会一声不响地什么都不做——占位的 wantPatches 还挡着同步器，
  // 结果是用户改完之后**永远等不到那张定稿图**。
  if (state.lastPatches !== want) {
    void store.render(panel.fileId, panel.overrides, undefined, 'sync')
    return
  }
  // 已经是这一版了：只有「现在这张是拖动期的低清」才需要补一张定稿
  if (state.previewDpi != null) {
    void store.render(panel.fileId, panel.overrides, undefined, 'sync')
  }
}

/**
 * 需要引擎渲染的面板，**按 (fileId, overrides) 去重**。
 *
 * 这里曾经是「每个 fileId 只能有一个说了算的面板」的裁决：渲染态按文件索引，
 * 两个同文件不同 override 的副本会同步互顶 wantPatches，effect ↔ store
 * 无限互相触发（React #185）。代价是输家永远显示赢家的图。现在渲染态按变体
 * 分键（renderKeyOf），两个副本各有各的条目，互不覆盖——真正的多变体支持，
 * 去重只剩「完全相同的两个副本共用一次渲染」这一条。
 */
export function renderTargets(
  objects: readonly CanvasObject[],
  editingId: string | null,
  tracked: Record<string, boolean | undefined>,
  latest: Record<string, string | undefined> = {},
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
    const wants =
      o.fileKind === 'runtime'
        ? o.id === editingId || latest[o.fileId] != null
        : // 编辑中 / 有图内修改 / 脚本已领先磁盘文件（AI 改过）。
          // 「只带基线、还没动过」的面板不渲染：磁盘文件本身就是那个样子，
          // 白跑一次引擎（heavy 脚本要几分钟）没有意义。
          o.id === editingId ||
          !!tracked[o.fileId] ||
          (o.overrides.length > 0 && !isJustBakedBaseline(o))
    if (!wants) continue
    const key = renderKeyOf(o)
    if (seen.has(key)) continue
    seen.add(key)
    targets.push(o)
  }
  return targets
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
  const store = useRenderStore.getState()
  for (const panel of renderTargets(objects, editingId, store.tracked, store.latest)) {
    const want = JSON.stringify(panel.overrides)
    const state = store.byKey[renderKeyOf(panel)]
    if (state && (state.lastPatches === want || state.wantPatches === want)) continue
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
 */
export function useEngineSync() {
  const objects = useDocumentStore((s) => s.doc.objects)
  const editingId = useUiStore((s) => s.elementPanelId)
  const byKey = useRenderStore((s) => s.byKey)
  const tracked = useRenderStore((s) => s.tracked)

  useEffect(() => {
    syncEngine(objects, editingId)
    // byKey / tracked 进依赖表是为了「渲染回来了 → 再看一眼还有没有要发的」，
    // 判断本身在 syncEngine 里读的是最新 state
  }, [objects, editingId, byKey, tracked])

  // 渲染回来的图幅尺寸变了（改了 size_mm）→ 同步面板原生尺寸并按新纵横比调高度。
  // 按**面板自己那份变体**取尺寸：size_mm 本身就是可以被 override 的，
  // 同文件的另一个副本改了图幅，不该把这个副本一起拽走。
  useEffect(() => {
    const fixes: { id: string; wMm: number; hMm: number }[] = []
    for (const o of objects) {
      if (o.type !== 'panel') continue
      const size = byKey[renderKeyOf(o)]?.manifest?.size_mm
      if (!size) continue
      const [wMm, hMm] = size
      if (Math.abs(o.nativeW - wMm) <= 0.05 && Math.abs(o.nativeH - hMm) <= 0.05) continue
      fixes.push({ id: o.id, wMm, hMm })
    }
    if (!fixes.length) return
    useDocumentStore.getState().silent((d) => {
      for (const fix of fixes) {
        const o = d.objects.find((x) => x.id === fix.id)
        if (o?.type !== 'panel') continue
        o.nativeW = fix.wMm
        o.nativeH = fix.hMm
        // x/y/w/h 是旋转后的页面包围盒：90/270 时内容的长宽是互换的，
        // 直接按 hMm/wMm 调 o.h 会把旋转过的面板越调越偏
        if (rotationSwaps(panelRotation(o))) o.w = o.h * (fix.hMm / fix.wMm)
        else o.h = o.w * (fix.hMm / fix.wMm)
      }
    })
  }, [byKey, objects])
}
