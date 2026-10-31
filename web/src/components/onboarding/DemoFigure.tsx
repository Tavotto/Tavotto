import {
  forwardRef,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type Ref,
  type SVGProps,
} from 'react'
import { DEMO_FIGURE, legendBox, mathRuns, TITLE_SIZE, type DemoText } from '@/lib/onboarding/demoFigure'
import type { DemoEdits } from '@/store/onboardingStore'

/**
 * 新手教程的演示图（ADR 0136）：按 `demoFigure.json`（真 matplotlib 跑 `fig1_kinetics.py`
 * 量出来的几何）画成一张 SVG，单位 pt、viewBox 就是图幅。**受控教学演示，不是编辑器**：
 * 这里不读也不写 documentStore，不经 override、撤销历史与后端（见 `lib/onboarding/demoFigure`）。
 *
 * 同一个组件画所有地方的那张图：欢迎页的预览、三步里的那一张、完成页的「调整前 / 后」——
 * 前后对比是同一份数据按两份演示状态各画一次，不是一张图片挡掉一块再贴一块
 * （原型 PNG + SVG 覆盖层留下底色残影的那个问题在这里不存在）。
 *
 * 可交互的只有两处，由调用方按步骤给：图例（`legend`）与标题（`title`）。它们的事件、
 * 可达性属性都从调用方来，这里只负责把它们画在对的位置。
 */

type Interactive<T extends SVGElement> = Omit<SVGProps<T>, 'ref'> & {
  ref?: Ref<T>
  onPointerDown?: (e: PointerEvent<T>) => void
  onKeyDown?: (e: KeyboardEvent<T>) => void
}

export interface DemoFigureProps {
  demo: DemoEdits
  /** 图例此刻的位移（拖动中的实时值）；不给就用 `demo.legendOffset` */
  liveLegendOffset?: [number, number] | null
  /** 无障碍名（整张图是一幅图像；可交互的部分自带名字） */
  label: string
  legend?: Interactive<SVGGElement>
  title?: Interactive<SVGGElement>
  className?: string
}

const F = DEMO_FIGURE

/** 文字放进 matplotlib 量出来的外框里：水平居中、竖直居中（浏览器没有 STIX，按框居中最稳） */
function BoxedText({ t, weight }: { t: DemoText; weight?: number }) {
  const [x, y, w, h] = t.box
  const cx = x + w / 2
  const cy = y + h / 2
  return (
    <text
      x={cx}
      y={cy}
      fontSize={t.sizePt}
      fontWeight={weight ?? (t.bold ? 700 : 400)}
      textAnchor="middle"
      dominantBaseline="central"
      transform={t.rotation ? `rotate(${t.rotation} ${cx} ${cy})` : undefined}
    >
      {t.text}
    </text>
  )
}

/** 带 `$^{…}$` 上标的一行字（图例文字） */
function MathText({ text, sizePt, x, y }: { text: string; sizePt: number; x: number; y: number }) {
  const lift = sizePt * 0.38
  let shifted = false
  return (
    <text x={x} y={y} fontSize={sizePt} dominantBaseline="central">
      {mathRuns(text).map((run, i) => {
        const dy = run.sup ? -lift : shifted ? lift : 0
        shifted = run.sup
        return (
          <tspan key={i} dy={dy || undefined} fontSize={run.sup ? sizePt * 0.7 : undefined}>
            {run.text}
          </tspan>
        )
      })}
    </text>
  )
}

export const DemoFigure = forwardRef<SVGSVGElement, DemoFigureProps>(function DemoFigure(
  { demo, liveLegendOffset, label, legend, title, className },
  ref,
) {
  const clipId = useId()
  const [ax, ay, aw, ah] = F.axes.box
  const { lengthPt, widthPt, inward } = F.ticks.style
  const tickDir = inward ? 1 : -1
  const offset = liveLegendOffset !== undefined ? liveLegendOffset : demo.legendOffset
  const [lx, ly, lw, lh] = legendBox(offset)
  const titleSize = demo.titleSizePt ?? TITLE_SIZE.initial
  const { ref: legendRef, ...legendRest } = legend ?? {}
  const { ref: titleRef, ...titleRest } = title ?? {}
  // 标题文字此刻的外框（pt）：字号一变就重量；量不到（jsdom）按字数估一个
  const titleText = useRef<SVGTextElement>(null)
  const [hit, setHit] = useState<{ x: number; y: number; width: number; height: number } | null>(null)
  useLayoutEffect(() => {
    const el = titleText.current
    let box: { x: number; y: number; width: number; height: number } | null = null
    try {
      const b = el?.getBBox()
      if (b && b.width > 0) box = { x: b.x, y: b.y, width: b.width, height: b.height }
    } catch {
      box = null
    }
    if (!box) {
      const w = F.title.text.length * titleSize * 0.5
      box = { x: F.title.anchor[0] - w / 2, y: F.title.anchor[1] - titleSize * 0.8, width: w, height: titleSize }
    }
    setHit(box)
  }, [titleSize])
  return (
    <svg
      ref={ref}
      viewBox={`0 0 ${F.figure.width} ${F.figure.height}`}
      // 有可交互的部分时是一组（里面有按钮）；纯展示时是一幅图像——role=img 里不能套可聚焦的东西
      role={legend || title ? 'group' : 'img'}
      aria-label={label}
      data-demo-figure=""
      className={className}
      // 文档字体（与界面字体严格分离）；pt 就是 viewBox 的单位，字号随图一起缩放
      style={{ fontFamily: 'var(--font-doc)', overflow: 'visible' }}
    >
      <rect width={F.figure.width} height={F.figure.height} fill="var(--color-paper)" />
      <defs>
        <clipPath id={clipId}>
          <rect x={ax} y={ay} width={aw} height={ah} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`} aria-hidden="true">
        {F.lines.map((line) => (
          <polyline
            key={line.label}
            points={line.points.map(([x, y]) => `${x},${y}`).join(' ')}
            fill="none"
            stroke={line.color}
            strokeWidth={line.widthPt}
            strokeDasharray={line.dash?.join(' ')}
            strokeLinejoin="round"
          />
        ))}
      </g>
      <g aria-hidden="true" fill="var(--color-paper-ink)" stroke="none">
        <rect
          x={ax}
          y={ay}
          width={aw}
          height={ah}
          fill="none"
          stroke="var(--color-paper-ink)"
          strokeWidth={F.axes.spineWidthPt}
        />
        {F.ticks.x.map((t) => (
          <line
            key={`x${t.at}`}
            x1={t.at}
            x2={t.at}
            y1={ay + ah}
            y2={ay + ah - tickDir * lengthPt}
            stroke="var(--color-paper-ink)"
            strokeWidth={widthPt}
          />
        ))}
        {F.ticks.y.map((t) => (
          <line
            key={`y${t.at}`}
            x1={ax}
            x2={ax + tickDir * lengthPt}
            y1={t.at}
            y2={t.at}
            stroke="var(--color-paper-ink)"
            strokeWidth={widthPt}
          />
        ))}
        {[...F.ticks.x, ...F.ticks.y].map((t, i) => (
          <BoxedText key={i} t={t.label} />
        ))}
        <BoxedText t={F.xlabel} />
        <BoxedText t={F.ylabel} />
      </g>
      <g
        ref={legendRef}
        data-demo-legend=""
        transform={`translate(${lx} ${ly})`}
        fill="var(--color-paper-ink)"
        {...legendRest}
      >
        {/* 命中面：脚本里 frameon=False，图例没有底色——透明的矩形只负责接住指针 */}
        <rect width={lw} height={lh} fill="transparent" pointerEvents={legend ? 'all' : 'none'} />
        <g aria-hidden="true" pointerEvents="none">
          {F.legend.entries.map((entry, i) => {
            const line = F.lines[i]
            const [[x1, y1], [x2, y2]] = entry.handle
            return (
              <g key={entry.text}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={line?.color}
                  strokeWidth={line?.widthPt}
                  strokeDasharray={line?.dash?.join(' ')}
                />
                <MathText
                  text={entry.text}
                  sizePt={entry.sizePt}
                  x={entry.box[0]}
                  y={entry.box[1] + entry.box[3] / 2}
                />
              </g>
            )
          })}
        </g>
      </g>
      <g
        ref={titleRef}
        data-demo-title=""
        fontSize={titleSize}
        fill="var(--color-paper-ink)"
        {...titleRest}
      >
        {/* 命中面：WebKit 只按字形命中 SVG 文字，点在两个字母之间会落空——量出文字外框垫一块透明的 */}
        {hit && (
          <rect
            x={hit.x - 1.5}
            y={hit.y - 1}
            width={hit.width + 3}
            height={hit.height + 2}
            fill="transparent"
            pointerEvents={title ? 'all' : 'none'}
          />
        )}
        <text ref={titleText} x={F.title.anchor[0]} y={F.title.anchor[1]} textAnchor="middle" pointerEvents="none">
          {F.title.text}
        </text>
      </g>
    </svg>
  )
})
