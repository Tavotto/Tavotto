import { effectiveOverride } from '@/lib/effectiveOverride'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { exactPanelRender, renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'

/** 渲染迟迟没回来时占位的最长时间（ms）；明确失败与撤销会提前收起。 */
const PLACEHOLDER_MAX_MS = 20_000

/**
 * 松手后到新图回来之前，画布上只留虚线框（`interaction.figureFramePending`）：旧图按旧尺寸画在新位置上会错位闪一下。
 * 新图的判据 = 这张面板当前变体已有精确渲染（`exactPanelRender`）**且**面板的 nativeW / nativeH 已追上目标图幅（同步器校正完）；撤销 / 面板没了 / 超时也收。
 */
export function holdPlaceholder(
  panelId: string,
  box: { x: number; y: number; w: number; h: number },
  sizeMm: [number, number],
  before: Readonly<Record<string, PanelRender>>,
  /** 新图回来、几何校正到位的那一刻（只在成功时调；撤销 / 失败 / 超时不调）：排版拖边缩小后量「文字放不下」用 */
  onSettled?: () => void,
) {
  const ix = useInteractionStore
  const stop: (() => void)[] = []
  let timer = 0
  const initialPanel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  const initialRender = initialPanel?.type === 'panel'
    ? before[renderKeyOf(initialPanel)]
    : undefined
  let sawRendering = initialPanel?.type === 'panel' &&
    useRenderStore.getState().byKey[renderKeyOf(initialPanel)]?.status === 'rendering'
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
    // 只认当前变体的新失败；同一尺寸以前失败过留下的缓存不是这次请求的结果。
    const own = useRenderStore.getState().byKey[renderKeyOf(p)]
    if (own?.status === 'rendering') sawRendering = true
    if (own?.status === 'error' &&
        (initialRender?.status !== 'error' || sawRendering || own.error !== initialRender.error)) return done()
    // 新图回来**且** `useEngineSync` 已把面板几何（nativeW/H → w/h、x/y）校正到位才撤：
    // 新图一到就撤的话，校正在之后的 effect 里才落，中间那次 render 是新图配旧尺寸（又闪一帧）
    if (
      exactPanelRender(useRenderStore.getState(), p) &&
      Math.abs(p.nativeW - sizeMm[0]) <= 0.05 &&
      Math.abs(p.nativeH - sizeMm[1]) <= 0.05
    ) {
      done()
      onSettled?.()
    }
  }
  ix.getState().setFigureFramePending({ id: panelId, box, sizeMm })
  stop.push(useRenderStore.subscribe(check), useDocumentStore.subscribe(check))
  timer = window.setTimeout(done, PLACEHOLDER_MAX_MS)
  check()
}
