import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  BODY_LINE_PX,
  MANUSCRIPT_SPECIMEN,
  PX_PER_MM,
  type InlineToken,
  type ManuscriptLayout,
  type ManuscriptProseBlock,
} from '@/lib/manuscriptPreview'
import { bucketFor } from '@/lib/previewBuckets'
import { cn } from '@/lib/utils'
import { ExportZoomStage } from './ExportZoomStage'

const pxMm = (mm: number) => mm * PX_PER_MM

const SERIF_STACK = '"Times New Roman", Times, "Nimbus Roman", STIXGeneral, Georgia, serif'
const SANS_STACK = '-apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif'

function InlineRuns({ runs }: { runs: readonly InlineToken[] }) {
  return (
    <>
      {runs.map((tok, i) => {
        if (typeof tok === 'string') return <span key={i}>{tok}</span>
        if ('bold' in tok) {
          return (
            <strong key={i} className="font-bold text-paper-ink">
              {tok.bold}
            </strong>
          )
        }
        if ('sup' in tok && !('italic' in tok) && !('roman' in tok)) {
          return (
            <sup key={i} style={{ fontSize: '0.68em', lineHeight: 0 }}>
              {tok.sup}
            </sup>
          )
        }
        const body = 'italic' in tok ? <i>{tok.italic}</i> : <span>{tok.roman}</span>
        return (
          <span key={i}>
            {body}
            {tok.sub && (
              <sub style={{ fontSize: '0.72em', lineHeight: 0 }}>
                {/[a-zA-Z]/.test(tok.sub) ? <i>{tok.sub}</i> : tok.sub}
              </sub>
            )}
            {tok.sup && (
              <sup style={{ fontSize: '0.68em', lineHeight: 0 }}>
                {tok.sup}
              </sup>
            )}
          </span>
        )
      })}
    </>
  )
}

function ProseBlockList({ blocks }: { blocks: readonly ManuscriptProseBlock[] }) {
  return (
    <>
      {blocks.map((block, idx) => {
        if (block.kind === 'heading') {
          return (
            <div
              key={idx}
              className="font-bold text-paper-ink"
              style={{
                fontSize: '12.6px',
                lineHeight: `${BODY_LINE_PX}px`,
                paddingTop: idx === 0 ? '4px' : '10px',
                paddingBottom: idx === 0 ? '12px' : '6px',
                height: `${BODY_LINE_PX * 2}px`,
                textAlign: 'left',
              }}
            >
              {block.text}
            </div>
          )
        }
        if (block.kind === 'equation') {
          return (
            <div
              key={idx}
              className="flex items-center justify-between text-paper-ink"
              style={{
                height: `${BODY_LINE_PX * 2}px`,
                paddingLeft: '16px',
                paddingRight: '2px',
                fontSize: '12px',
              }}
            >
              <span>
                <InlineRuns runs={block.runs} />
              </span>
              <span className="tabular-nums text-paper-ink-2">{block.num}</span>
            </div>
          )
        }
        return (
          <p
            key={idx}
            className="m-0 text-paper-ink-2"
            style={{
              lineHeight: `${BODY_LINE_PX}px`,
              textIndent: block.indent ? '14px' : undefined,
            }}
          >
            <InlineRuns runs={block.runs} />
          </p>
        )
      })}
    </>
  )
}

/**
 * 固定整数行高的前导正文块（用于左栏嵌排 / 版中嵌排的图片上方）。
 */
function FixedLinesFlow({
  blocks,
  lines,
}: {
  blocks: readonly ManuscriptProseBlock[]
  lines: number
}) {
  return (
    <div
      aria-hidden
      className="pointer-events-none shrink-0 overflow-hidden"
      style={{
        height: `${lines * BODY_LINE_PX}px`,
        fontFamily: SERIF_STACK,
        fontSize: '12px',
        lineHeight: `${BODY_LINE_PX}px`,
        textAlign: 'justify',
        hyphens: 'auto',
      }}
    >
      <ProseBlockList blocks={blocks} />
    </div>
  )
}

/**
 * 弹性填满剩余栏高、并按 16px 整数行高收尾的正文流，保证底边绝不切破半行英文字母。
 */
function BodyColumnFlow({
  blocks,
  className,
}: {
  blocks: readonly ManuscriptProseBlock[]
  className?: string
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [snappedH, setSnappedH] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = hostRef.current
    if (!el) return
    const update = () => {
      const h = el.clientHeight
      if (h > 0) setSnappedH(Math.floor(h / BODY_LINE_PX) * BODY_LINE_PX)
    }
    update()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div
      ref={hostRef}
      aria-hidden
      className={cn('pointer-events-none min-h-0 flex-1 overflow-hidden', className)}
    >
      <div
        className="overflow-hidden"
        style={{
          height: snappedH !== null ? `${snappedH}px` : '100%',
          fontFamily: SERIF_STACK,
          fontSize: '12px',
          lineHeight: `${BODY_LINE_PX}px`,
          textAlign: 'justify',
          hyphens: 'auto',
        }}
      >
        <ProseBlockList blocks={blocks} />
      </div>
    </div>
  )
}

/**
 * 真实期刊图表浮动块（纯白纸底、无灰块与四角标尺）：图面居中排入栏宽，下方紧接 Nature 风格 Fig. 1 图题。
 */
function FigureFloatPlate({
  figW,
  figH,
  captionWidthPx,
  showCaption,
  position,
  children,
}: {
  figW: number
  figH: number
  captionWidthPx: number
  showCaption: boolean
  position: 'top' | 'mid' | 'bottom'
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        'flex shrink-0 flex-col items-center',
        position !== 'top' && 'border-t border-paper-ink/14',
        position !== 'bottom' && 'border-b border-paper-ink/14',
      )}
      style={{
        width: `${captionWidthPx}px`,
        marginTop: position !== 'top' ? '10px' : '0px',
        paddingTop: position !== 'top' ? '10px' : '0px',
        paddingBottom: position !== 'bottom' ? '8px' : '0px',
        marginBottom: position !== 'bottom' ? '10px' : '0px',
      }}
    >
      <div
        data-export-manuscript-figure
        className="shrink-0 overflow-hidden"
        style={{
          width: `${pxMm(figW)}px`,
          height: `${pxMm(figH)}px`,
        }}
      >
        {children}
      </div>
      {showCaption && (
        <div
          aria-hidden
          className="pointer-events-none w-full"
          style={{
            marginTop: `${pxMm(2.6)}px`,
            fontFamily: SERIF_STACK,
            fontSize: '10.67px',
            lineHeight: '14.6px',
            textAlign: 'justify',
            hyphens: 'auto',
          }}
        >
          <span
            className="font-bold text-paper-ink"
            style={{ fontFamily: SANS_STACK, fontSize: '10px' }}
          >
            {MANUSCRIPT_SPECIMEN.captionLead}
          </span>{' '}
          <strong className="font-bold text-paper-ink">{MANUSCRIPT_SPECIMEN.captionTitle}</strong>{' '}
          <span className="text-paper-ink-2">
            <InlineRuns runs={MANUSCRIPT_SPECIMEN.captionRuns} />
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * 导出对话框左栏「版面预览」：支持多种真实期刊排布版位（右上栏 / 左栏段间嵌排 / 右下沉底 / 左上栏；
 * 通栏顶置 / 版中嵌排 / 底置通栏），纯白纸面不加任何灰块或定位角标，保持 96 DPI 单层矢量锐利度。
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
    gutterMm,
    slotW,
    oppositeColW,
    textBlockTop,
    textBlockBottom,
    headerRuleY,
    footerRuleY,
    figX,
    figY,
    figW,
    figH,
    placement,
    slotVariant,
    leadLines,
    showCaption,
  } = layout

  const cameraExpanded = viewportW > sheetW + 0.01 || viewportH > sheetH + 0.01
  const logicalW = pxMm(viewportW)
  const logicalH = pxMm(viewportH)
  const textBlockHeightPx = pxMm(textBlockBottom - textBlockTop)

  return (
    <ExportZoomStage
      key={stageKey}
      ratio={viewportW / viewportH}
      framed={!cameraExpanded}
      logicalSize={{ w: logicalW, h: logicalH }}
    >
      {(px) => {
        const pxFig = px > 0 ? bucketFor(px * (figW / viewportW)) : 0
        const figureNode = renderFigure(pxFig)

        return (
          <div className="relative h-full w-full">
            <div
              data-export-manuscript
              data-placement={placement}
              data-slot-variant={slotVariant}
              data-on-paper=""
              className={cn(
                'absolute bg-paper shadow-page',
                cameraExpanded && 'outline outline-1 -outline-offset-1 outline-paper-ink/18',
              )}
              style={{
                left: `${pxMm(sheetOffsetX)}px`,
                top: `${pxMm(sheetOffsetY)}px`,
                width: `${pxMm(sheetW)}px`,
                height: `${pxMm(sheetH)}px`,
                WebkitFontSmoothing: 'antialiased',
                textRendering: 'geometricPrecision',
              }}
            >
              {/* 顶部期刊眉标与细线 */}
              <div
                aria-hidden
                className="pointer-events-none absolute flex items-baseline justify-between border-b border-paper-ink/22"
                style={{
                  left: `${pxMm(marginX)}px`,
                  top: `${pxMm(9.6)}px`,
                  width: `${pxMm(textBlockW)}px`,
                  height: `${pxMm(headerRuleY - 9.6)}px`,
                }}
              >
                <div className="flex min-w-0 items-baseline truncate">
                  <span
                    className="font-bold text-paper-ink-2"
                    style={{ fontFamily: SANS_STACK, fontSize: '8.8px', letterSpacing: '0.11em' }}
                  >
                    {MANUSCRIPT_SPECIMEN.category}
                  </span>
                  <span className="mx-2 text-paper-ink-3" style={{ fontSize: '9.5px' }}>
                    ·
                  </span>
                  <span
                    className="truncate italic text-paper-ink-3"
                    style={{ fontFamily: SERIF_STACK, fontSize: '10px' }}
                  >
                    {MANUSCRIPT_SPECIMEN.runningTitle}
                  </span>
                </div>
                <span
                  className="shrink-0 tabular-nums text-paper-ink-3"
                  style={{ fontFamily: SERIF_STACK, fontSize: '9.6px' }}
                >
                  {MANUSCRIPT_SPECIMEN.volumeFolio}
                </span>
              </div>

              {/* 图超出规范版芯时：标出版芯左右限界虚线 */}
              {placement === 'overflow' && (
                <div
                  aria-hidden
                  data-export-manuscript-overflow
                  className="pointer-events-none absolute border-x border-dashed border-danger/55"
                  style={{
                    left: `${pxMm(marginX)}px`,
                    top: `${pxMm(Math.max(headerRuleY + 1.5, figY - 1.5))}px`,
                    width: `${pxMm(textBlockW)}px`,
                    height: `${pxMm(Math.min(sheetH - headerRuleY - 8, figH + 3))}px`,
                  }}
                />
              )}

              {/* 版芯双栏排印区 */}
              <div
                className="absolute overflow-hidden"
                style={{
                  left: `${pxMm(marginX)}px`,
                  top: `${pxMm(textBlockTop)}px`,
                  width: `${pxMm(textBlockW)}px`,
                  height: `${textBlockHeightPx}px`,
                }}
              >
                {placement === 'single' ? (
                  <div
                    className="flex h-full w-full"
                    style={{ columnGap: `${pxMm(gutterMm)}px` }}
                  >
                    {slotVariant === 'top-right' && (
                      <>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(oppositeColW)}px` }}
                        >
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.primaryBlocks} />
                        </div>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(slotW)}px` }}
                        >
                          <FigureFloatPlate
                            figW={figW}
                            figH={figH}
                            captionWidthPx={pxMm(slotW)}
                            showCaption={showCaption}
                            position="top"
                          >
                            {figureNode}
                          </FigureFloatPlate>
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} />
                        </div>
                      </>
                    )}

                    {slotVariant === 'mid-left' && (
                      <>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(slotW)}px` }}
                        >
                          {leadLines > 0 && (
                            <FixedLinesFlow
                              blocks={MANUSCRIPT_SPECIMEN.primaryBlocks}
                              lines={leadLines}
                            />
                          )}
                          <FigureFloatPlate
                            figW={figW}
                            figH={figH}
                            captionWidthPx={pxMm(slotW)}
                            showCaption={showCaption}
                            position={leadLines > 0 ? 'mid' : 'top'}
                          >
                            {figureNode}
                          </FigureFloatPlate>
                          <BodyColumnFlow
                            blocks={
                              leadLines > 0
                                ? MANUSCRIPT_SPECIMEN.primaryBlocks.slice(2)
                                : MANUSCRIPT_SPECIMEN.primaryBlocks
                            }
                          />
                        </div>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(oppositeColW)}px` }}
                        >
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} />
                        </div>
                      </>
                    )}

                    {slotVariant === 'bottom-right' && (
                      <>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(oppositeColW)}px` }}
                        >
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.primaryBlocks} />
                        </div>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(slotW)}px` }}
                        >
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} />
                          <FigureFloatPlate
                            figW={figW}
                            figH={figH}
                            captionWidthPx={pxMm(slotW)}
                            showCaption={showCaption}
                            position="bottom"
                          >
                            {figureNode}
                          </FigureFloatPlate>
                        </div>
                      </>
                    )}

                    {slotVariant === 'top-left' && (
                      <>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(slotW)}px` }}
                        >
                          <FigureFloatPlate
                            figW={figW}
                            figH={figH}
                            captionWidthPx={pxMm(slotW)}
                            showCaption={showCaption}
                            position="top"
                          >
                            {figureNode}
                          </FigureFloatPlate>
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.primaryBlocks} />
                        </div>
                        <div
                          className="flex h-full flex-col overflow-hidden"
                          style={{ width: `${pxMm(oppositeColW)}px` }}
                        >
                          <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} />
                        </div>
                      </>
                    )}
                  </div>
                ) : placement === 'span' ? (
                  <div className="flex h-full w-full flex-col overflow-hidden">
                    {slotVariant === 'mid-span' && leadLines > 0 && (
                      <div
                        className="grid shrink-0 grid-cols-2 overflow-hidden"
                        style={{ columnGap: `${pxMm(gutterMm)}px` }}
                      >
                        <FixedLinesFlow blocks={MANUSCRIPT_SPECIMEN.primaryBlocks} lines={leadLines} />
                        <FixedLinesFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} lines={leadLines} />
                      </div>
                    )}

                    {slotVariant !== 'bottom-span' && (
                      <FigureFloatPlate
                        figW={figW}
                        figH={figH}
                        captionWidthPx={pxMm(textBlockW)}
                        showCaption={showCaption}
                        position={slotVariant === 'mid-span' && leadLines > 0 ? 'mid' : 'top'}
                      >
                        {figureNode}
                      </FigureFloatPlate>
                    )}

                    <div
                      className="grid min-h-0 flex-1 grid-cols-2 overflow-hidden"
                      style={{ columnGap: `${pxMm(gutterMm)}px` }}
                    >
                      <BodyColumnFlow
                        blocks={
                          slotVariant === 'mid-span' && leadLines > 0
                            ? MANUSCRIPT_SPECIMEN.primaryBlocks.slice(2)
                            : MANUSCRIPT_SPECIMEN.primaryBlocks
                        }
                      />
                      <BodyColumnFlow
                        blocks={
                          slotVariant === 'mid-span' && leadLines > 0
                            ? MANUSCRIPT_SPECIMEN.secondaryBlocks.slice(2)
                            : MANUSCRIPT_SPECIMEN.secondaryBlocks
                        }
                      />
                    </div>

                    {slotVariant === 'bottom-span' && (
                      <FigureFloatPlate
                        figW={figW}
                        figH={figH}
                        captionWidthPx={pxMm(textBlockW)}
                        showCaption={showCaption}
                        position="bottom"
                      >
                        {figureNode}
                      </FigureFloatPlate>
                    )}
                  </div>
                ) : (
                  /* 越界模式：上方预留图高与图注，下方排布双栏正文；真图由外层绝对层绘制以完整展示越界边缘 */
                  <div className="flex h-full w-full flex-col overflow-hidden">
                    <div className="shrink-0" style={{ height: `${pxMm(figH)}px` }} />
                    {showCaption && (
                      <div
                        aria-hidden
                        className="pointer-events-none shrink-0 border-b border-paper-ink/14"
                        style={{
                          marginTop: `${pxMm(2.6)}px`,
                          paddingBottom: '8px',
                          marginBottom: '10px',
                          fontFamily: SERIF_STACK,
                          fontSize: '10.67px',
                          lineHeight: '14.6px',
                          textAlign: 'justify',
                        }}
                      >
                        <span
                          className="font-bold text-paper-ink"
                          style={{ fontFamily: SANS_STACK, fontSize: '10px' }}
                        >
                          {MANUSCRIPT_SPECIMEN.captionLead}
                        </span>{' '}
                        <strong className="font-bold text-paper-ink">
                          {MANUSCRIPT_SPECIMEN.captionTitle}
                        </strong>{' '}
                        <span className="text-paper-ink-2">
                          <InlineRuns runs={MANUSCRIPT_SPECIMEN.captionRuns} />
                        </span>
                      </div>
                    )}
                    <div
                      className="grid min-h-0 flex-1 grid-cols-2 overflow-hidden"
                      style={{ columnGap: `${pxMm(gutterMm)}px` }}
                    >
                      <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.primaryBlocks} />
                      <BodyColumnFlow blocks={MANUSCRIPT_SPECIMEN.secondaryBlocks} />
                    </div>
                  </div>
                )}
              </div>

              {/* 底部页脚细线与页码 */}
              <div
                aria-hidden
                className="pointer-events-none absolute flex items-center justify-between border-t border-paper-ink/16"
                style={{
                  left: `${pxMm(marginX)}px`,
                  top: `${pxMm(footerRuleY)}px`,
                  width: `${pxMm(textBlockW)}px`,
                  paddingTop: '5px',
                }}
              >
                <span
                  className="italic text-paper-ink-3"
                  style={{ fontFamily: SERIF_STACK, fontSize: '9px' }}
                >
                  {MANUSCRIPT_SPECIMEN.footerNote}
                </span>
                <span
                  className="font-bold tabular-nums text-paper-ink"
                  style={{ fontFamily: SERIF_STACK, fontSize: '10px' }}
                >
                  {MANUSCRIPT_SPECIMEN.pageNumber}
                </span>
              </div>
            </div>

            {/* 越界模式下真图放在外层以越出纸面或版芯边界 */}
            {placement === 'overflow' && (
              <div
                data-export-manuscript-figure
                className="absolute overflow-hidden outline outline-1 outline-danger/45"
                style={{
                  left: `${pxMm(sheetOffsetX + figX)}px`,
                  top: `${pxMm(sheetOffsetY + figY)}px`,
                  width: `${pxMm(figW)}px`,
                  height: `${pxMm(figH)}px`,
                }}
              >
                {figureNode}
              </div>
            )}
          </div>
        )
      }}
    </ExportZoomStage>
  )
}
