import type { PointerEvent as ReactPointerEvent } from 'react'
import { t as translate } from '@/i18n'
import { resolvePageColumns, type PageColumn } from '@/lib/pageColumns'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import { planFigureFrame, type PageEdge } from '@/lib/pageEdgeResize'
import { setFigureFrame } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { exactPanelManifest, exactPanelRender, useRenderStore } from '@/store/renderStore'
import { getTransform, pxToMm, useViewportStore } from '@/store/viewportStore'
import { trackPointer } from './interactions'

/** 渲染没回来时占位的最长时间（ms）：渲染失败 / 被取代时不让旧图永远藏着 */
const PLACEHOLDER_MAX_MS = 20_000

/**
 * 松手后到新图回来之前，画布上只留虚线框（`interaction.figureFramePending`）：旧图按旧尺寸画在新位置上会错位闪一下。
 * 新图的判据 = 这张面板当前变体已有精确渲染（`exactPanelRender`）**且**面板的 nativeW / nativeH 已追上目标图幅（同步器校正完）；撤销 / 面板没了 / 超时也收。
 */
function holdPlaceholder(panelId: string, box: { x: number; y: number; w: number; h: number }, sizeMm: [number, number]) {
  const ix = useInteractionStore
  const stop: (() => void)[] = []
  let timer = 0
  const done = () => {
    stop.forEach((f) => f())
    stop.length = 0
    window.clearTimeout(timer)
    if (ix.getState().figureFramePending?.id === panelId) ix.getState().setFigureFramePending(null)
  }
  const check = () => {
    const p = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
    if (ix.getState().figureFramePending?.id !== panelId) return done()
    if (!p || p.type !== 'panel') return done()
    // 撤销 / 别处改了图幅：这次的目标已作废
    const ov = effectiveOverride(p.overrides, 'figure', 'size_mm')?.value
    if (!Array.isArray(ov) || ov[0] !== sizeMm[0] || ov[1] !== sizeMm[1]) return done()
    // 新图回来**且** `useEngineSync` 已把面板几何（nativeW/H → w/h、x/y）校正到位才撤：
    // 新图一到就撤的话，校正在之后的 effect 里才落，中间那次 render 是新图配旧尺寸（又闪一帧）
    if (
      exactPanelRender(useRenderStore.getState(), p) &&
      Math.abs(p.nativeW - sizeMm[0]) <= 0.05 &&
      Math.abs(p.nativeH - sizeMm[1]) <= 0.05
    )
      done()
  }
  ix.getState().setFigureFramePending({ id: panelId, box, sizeMm })
  stop.push(useRenderStore.subscribe(check), useDocumentStore.subscribe(check))
  timer = window.setTimeout(done, PLACEHOLDER_MAX_MS)
  check()
}

function columnLabel(specLabel: string, c: PageColumn): string {
  const name = translate(`canvas.presets.${c.id}.label`, { ns: 'inspector' })
  return specLabel ? `${specLabel} ${name}` : name
}

/**
 * 单图编辑里拖这张图的四条边之一改**图幅**（`figure.size_mm`，属性栏「图幅」W / H 同一条 override）——
 * 与多图排版里拖页面边（`startPageEdgeResize`）同一套手感：框变大、**内容不缩放**（字号线宽不变，图按新图幅重新渲染）。
 *
 * - **拖动中不写文档、不发渲染**：只在 `interaction.figureEdgeBox` 里放一个虚线预览框，尺寸标注读它；
 *   松手才 `setFigureFrame` 一次（一条历史 + 一次渲染）。Esc / pointercancel / 没拖动 = 什么都没发生。
 * - 松手后新图回来之前只留虚线框占位（`holdPlaceholder`），不闪错位的旧图。
 * - 拖左 / 上边时对侧边不动：松手时同一次提交里把 x / y 挪好（渲染回来后 `useEngineSync` 以左上角为锚点静默校正 w / h）。
 * - 拖动是对视口的直接操纵：每帧 `setPan` 退出「适应画布」（否则图幅一变适应模式把图挪走），Esc 取消时视口整体还原。
 * - 宽度吸栏宽 ±2 mm、⌥ 关、0.1 mm 一格（页面 mm 上做，`planFigureFrame` → `resizeFigureEdge`）。
 * - 权威只认精确 manifest（`figureFrameFacts`）：没有就不起手势。
 */
export function startFigureEdgeResize(e: ReactPointerEvent, edge: PageEdge, panelId: string) {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const store = useDocumentStore.getState()
  const target = store.doc.objects.find((o) => o.id === panelId)
  if (!target || target.type !== 'panel' || target.locked) return
  const facts = figureFrameFacts(target, exactPanelManifest(useRenderStore.getState(), target))
  if (!facts) return
  const start = { x: target.x, y: target.y, w: target.w, h: target.h }
  // 先把视口定住（在飞的取景补间落到终点）再取快照：取消时整体还原
  const view0 = useViewportStore.getState().freezeView()
  const t0 = getTransform()
  const columnsNow = () => {
    const ps = useProfileStore.getState()
    const r = resolvePageColumns(store.doc.profile, toCatalog(ps.specs), ps.loaded)
    return { columns: r.ready ? r.columns : [], specLabel: r.specLabel }
  }
  if (!useProfileStore.getState().loaded) void useProfileStore.getState().load()
  const horizontal = edge === 'l' || edge === 'r'
  const interaction = useInteractionStore.getState()

  interaction.begin('figureedge')
  interaction.setPageEdge({ edge, snapLabel: null, rolls: false })
  interaction.setFigureEdgeBox({ ...start })

  let last: ReturnType<typeof planFigureFrame> | null = null
  let prevSnap: string | null = null
  let prevAlt = false
  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const { columns, specLabel } = columnsNow()
      const plan = planFigureFrame({
        edge,
        start,
        sizeMm: facts.sizeMm,
        delta: pxToMm(horizontal ? dxPx : dyPx, t0),
        columns,
        noSnap: ev.altKey,
        limits: facts.limits,
      })
      last = plan
      // 直接操纵视口：退出「适应画布」（否则松手后图幅变了，适应模式按新图幅重新取景，图在屏幕上被挪走）
      useViewportStore.getState().setPan(t0.panX, t0.panY)
      const snapId = plan.snap?.id ?? null
      const rolls = snapId !== prevSnap || ev.altKey !== prevAlt
      prevSnap = snapId
      prevAlt = ev.altKey
      const ix = useInteractionStore.getState()
      ix.setFigureEdgeBox(plan.box)
      ix.setPageEdge({ edge, snapLabel: plan.snap ? columnLabel(specLabel, plan.snap) : null, rolls })
    },
    onEnd: (moved, _ev, end) => {
      useInteractionStore.getState().end()
      if (end.cancelled || !moved || !last) {
        if (moved) useViewportStore.getState().restoreFrozenView(view0)
        return
      }
      const [w, h] = last.sizeMm
      if (w === facts.sizeMm[0] && h === facts.sizeMm[1]) return
      setFigureFrame(panelId, last.sizeMm, edge === 'l' || edge === 't' ? last.pos : undefined)
      holdPlaceholder(panelId, last.box, last.sizeMm)
    },
  })
}
