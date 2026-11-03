import { perfCount } from '@/perf/core'
import { useDocumentStore } from '@/store/documentStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'

/**
 * 页面外变淡 + 页面轮廓压在最上层（2026-09-28 用户反馈，参考可画「页面即蒙版」）。
 *
 * 伸出页面的内容导出时会被页框裁掉（PDF 的 MediaBox 就是页面），这里把它画淡，
 * 让所见与导出一致；页面轮廓画在内容**之上**——图比页面大时，用户仍一眼看得出页面
 * 在哪，而不是「图把画布盖住了」。只是画淡不是隐藏：伸出去的部分照样看得见、点得着
 * （整层不吃指针事件），挪回来是用户的事。
 *
 * **屏幕空间、四条色带**，不是世界层里一张挖洞的大图：世界层按缩放整体放大，8× 时
 * 一张覆盖整个工作区的元素会是几十万像素的合成层。也刻意不用 `<svg>`——e2e 有「舞台里
 * 第一个 svg / img」这种等渲染的定位，多一个常驻的 svg 会让那类等待提前满足。
 *
 * 色带用画布灰本身（`--color-canvas`）盖一层：空工作区上叠同色看不出变化，只有页面
 * 外真的有内容时才显出来。快速编辑那一屏没有页面，不挂。
 */
export function PageOutsideMask({ rectMm }: { rectMm?: { x: number; y: number; w: number; h: number } } = {}) {
  perfCount('render.PageOutsideMask')
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const pageW = useDocumentStore((s) => s.doc.page.w)
  const pageH = useDocumentStore((s) => s.doc.page.h)
  // 单图编辑有留白时（ADR 0161）框的是画布（图 + 留白），不是排版页面
  const x = panX + (rectMm ? mmToWorld(rectMm.x) * zoom : 0)
  const y = panY + (rectMm ? mmToWorld(rectMm.y) * zoom : 0)
  const w = mmToWorld(rectMm ? rectMm.w : pageW) * zoom
  const h = mmToWorld(rectMm ? rectMm.h : pageH) * zoom
  const band = 'absolute bg-canvas/70'
  return (
    <div data-page-outside-mask="" className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* 上、下两条横贯整宽；左、右两条只占页面那一段高度，四条不重叠（重叠处会更深） */}
      <div className={band} style={{ left: 0, right: 0, top: 0, height: Math.max(y, 0) }} />
      <div className={band} style={{ left: 0, right: 0, top: Math.max(y + h, 0), bottom: 0 }} />
      <div className={band} style={{ left: 0, top: y, height: h, width: Math.max(x, 0) }} />
      <div className={band} style={{ left: Math.max(x + w, 0), right: 0, top: y, height: h }} />
      <div
        data-page-outline=""
        className="absolute outline outline-1 outline-border-strong/60"
        style={{ left: x, top: y, width: w, height: h }}
      />
    </div>
  )
}
