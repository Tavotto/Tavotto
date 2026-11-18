import type { ReactNode } from 'react'
import type { ManuscriptLayout } from '@/lib/manuscriptPreview'
import { bucketFor } from '@/lib/previewBuckets'
import { cn } from '@/lib/utils'
import { ExportZoomStage } from './ExportZoomStage'

const pct = (mm: number, totalMm: number) => `${((mm / totalMm) * 100).toFixed(4)}%`

/**
 * 导出对话框左栏「版面预览」：将待导出的真实图像按 1:1 毫米比例置入当前出版规范对应的学术内页（A4），
 * 用克制的排印骨架线（眉标、栏位基准、图注带、双栏正文节奏）呈现入版后的真实视觉尺度与栏宽贴合度。
 *
 * 复用 `ExportZoomStage`：同样支持滚轮 / 捏合在版面上放大核对小字号可读性、拖拽平移与双击复位。
 */
export function ExportManuscriptPreview({
  stageKey,
  layout,
  renderFigure,
}: {
  stageKey: string
  layout: ManuscriptLayout
  renderFigure: (pxFig: number) => ReactNode
}) {
  const {
    viewportW,
    viewportH,
    sheetW,
    sheetH,
    sheetOffsetX,
    sheetOffsetY,
    marginX,
    textBlockW,
    headerY,
    footerY,
    figX,
    figY,
    figW,
    figH,
    slotX,
    slotW,
    showSlotGuide,
    placement,
    caption,
    bodyLines,
  } = layout

  const cameraExpanded = viewportW > sheetW + 0.01 || viewportH > sheetH + 0.01

  return (
    <ExportZoomStage
      key={stageKey}
      ratio={viewportW / viewportH}
      framed={!cameraExpanded}
    >
      {(px) => {
        const pxFig = px > 0 ? bucketFor(px * (figW / viewportW)) : 0
        return (
          <div className="relative h-full w-full">
            {/* 学术内页纸张（纸色恒白，纸上排印纹样一律用 paper-ink，暗色下同样如印刷样张） */}
            <div
              data-export-manuscript
              data-placement={placement}
              data-on-paper=""
              className={cn(
                'absolute bg-paper shadow-page',
                cameraExpanded && 'outline outline-1 -outline-offset-1 outline-paper-ink/16',
              )}
              style={{
                left: pct(sheetOffsetX, viewportW),
                top: pct(sheetOffsetY, viewportH),
                width: pct(sheetW, viewportW),
                height: pct(sheetH, viewportH),
              }}
            >
              {/* 装饰性学术排印骨架：全树 aria-hidden、零文字字符，视线焦点完全留给真图 */}
              <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
                {/* 期刊眉标短标与顶部分隔细线 */}
                <span
                  className="absolute border-t-2 border-paper-ink/20"
                  style={{
                    left: pct(marginX, sheetW),
                    top: pct(10.6, sheetH),
                    width: pct(Math.min(26, textBlockW * 0.18), sheetW),
                  }}
                />
                <span
                  className="absolute border-t-2 border-paper-ink/20"
                  style={{
                    left: pct(marginX + textBlockW - 6, sheetW),
                    top: pct(10.6, sheetH),
                    width: pct(6, sheetW),
                  }}
                />
                <span
                  className="absolute border-t border-paper-ink/14"
                  style={{
                    left: pct(marginX, sheetW),
                    top: pct(headerY, sheetH),
                    width: pct(textBlockW, sheetW),
                  }}
                />

                {/* 图窄于目标栏位时：浅纸色底衬 + 两侧栏位虚线标出完整栏宽余量 */}
                {showSlotGuide && (
                  <div
                    data-export-manuscript-slot
                    className="absolute border-x border-dashed border-paper-ink/20 bg-paper-tint"
                    style={{
                      left: pct(slotX, sheetW),
                      top: pct(figY, sheetH),
                      width: pct(slotW, sheetW),
                      height: pct(figH, sheetH),
                    }}
                  />
                )}

                {/* 图超出规范版芯时：标出版芯左右限界虚线，直观对照越界幅度 */}
                {placement === 'overflow' && (
                  <div
                    data-export-manuscript-overflow
                    className="absolute border-x border-dashed border-danger/55"
                    style={{
                      left: pct(marginX, sheetW),
                      top: pct(Math.max(headerY + 2, figY - 2), sheetH),
                      width: pct(textBlockW, sheetW),
                      height: pct(Math.min(sheetH - headerY - 8, figH + 4), sheetH),
                    }}
                  />
                )}

                {/* 图注带（模拟 "Fig. 1 | ..." 粗前缀 + 多行图注） */}
                {caption && (
                  <>
                    <span
                      className="absolute border-t-2 border-paper-ink/45"
                      style={{
                        left: pct(caption.x, sheetW),
                        top: pct(caption.y, sheetH),
                        width: pct(caption.leadW, sheetW),
                      }}
                    />
                    <span
                      className="absolute border-t border-paper-ink/20"
                      style={{
                        left: pct(caption.x + caption.leadW + 1.6, sheetW),
                        top: pct(caption.y + 0.25, sheetH),
                        width: pct(Math.max(4, caption.w - caption.leadW - 1.6), sheetW),
                      }}
                    />
                    {caption.lines.map((ln, idx) => (
                      <span
                        key={idx}
                        className="absolute border-t border-paper-ink/16"
                        style={{
                          left: pct(caption.x, sheetW),
                          top: pct(caption.y + ln.dy, sheetH),
                          width: pct(caption.w * ln.widthRatio, sheetW),
                        }}
                      />
                    ))}
                  </>
                )}

                {/* 双栏学术正文排印节奏线 */}
                {bodyLines.map((ln, idx) => (
                  <span
                    key={idx}
                    className={
                      ln.heading
                        ? 'absolute border-t-2 border-paper-ink/28'
                        : 'absolute border-t border-paper-ink/12'
                    }
                    style={{
                      left: pct(ln.x, sheetW),
                      top: pct(ln.y, sheetH),
                      width: pct(ln.w, sheetW),
                    }}
                  />
                ))}

                {/* 页脚页码短标 */}
                <span
                  className="absolute border-t border-paper-ink/16"
                  style={{
                    left: pct(sheetW / 2 - 2.5, sheetW),
                    top: pct(footerY, sheetH),
                    width: pct(5, sheetW),
                  }}
                />
              </div>
            </div>

            {/* 待导出的真实图面：按 1:1 物理毫米落位直接印在纸面上（不叠第二层纸张阴影） */}
            <div
              data-export-manuscript-figure
              className={cn(
                'absolute overflow-hidden',
                placement === 'overflow' && 'outline outline-1 outline-danger/45',
              )}
              style={{
                left: pct(sheetOffsetX + figX, viewportW),
                top: pct(sheetOffsetY + figY, viewportH),
                width: pct(figW, viewportW),
                height: pct(figH, viewportH),
              }}
            >
              {renderFigure(pxFig)}
            </div>
          </div>
        )
      }}
    </ExportZoomStage>
  )
}
