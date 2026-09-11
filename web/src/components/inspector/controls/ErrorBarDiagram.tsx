import { cn } from '@/lib/utils'

/**
 * 误差棒示意图：**哪个数字改图上的哪一段**（审计 T20）。
 *
 * 「端帽长度 / 端帽线宽」是准确术语，审计要求保留；难的不是词，是这两个词
 * 指的是同一根横线的两个方向——长度是它多长，线宽是它多粗。一句解释不如
 * 一张图：聚焦（或悬停）到哪个字段，图上那一段就亮起来。
 *
 * 三条纪律：
 *   * **无装饰**：没有底色、没有边框、没有说明文字。它是那三个字段的
 *     图注，不是一个区块。
 *   * **不是控件**：`aria-hidden`，不吃焦点也不吃点击。字段的可达名与单位
 *     照旧由那三行自己负责——图消失了用户也不会少任何能力。
 *   * **只画三段**：线、端帽长度、端帽线宽。颜色与透明度不在图上表达，
 *     它们不是几何。
 *
 * 尺寸是**示意**，不承诺与 pt 值成比例：真按 pt 缩放的话，端帽长度 0.5 pt
 * 的图上什么都看不见，而那正是用户最需要看懂的时候。
 *
 * 两个端帽档的画法**刻意不同**（真浏览器 72 px 实测定的，jsdom 看不出来）：
 *   * 长度档 —— 端帽保持原粗细，上方一条带端点的量尺，量的是水平那一维；
 *   * 线宽档 —— 不画量尺，直接把端帽画粗一倍半。3.4 单位的竖向量尺在
 *     72 px 宽的图上只有 3 个像素高，是个噪点，不是说明。
 */

/** 图上能高亮的三段——与 manifest 的 prop 名同名，调用方按 `data-prop` 传 */
export const ERRORBAR_DIAGRAM_PROPS = ['linewidth', 'capsize', 'cap_thickness'] as const
export type ErrorBarSegment = (typeof ERRORBAR_DIAGRAM_PROPS)[number]

export const isErrorBarSegment = (prop: string | null | undefined): prop is ErrorBarSegment =>
  (ERRORBAR_DIAGRAM_PROPS as readonly string[]).includes(prop ?? '')

const CX = 36
const TOP = 16
const BOTTOM = 44
const CAP_HALF = 11
/** 量尺离端帽的净空：贴太近两条线会读成一条 */
const MEASURE_Y = TOP - 9

export function ErrorBarDiagram({ active }: { active: ErrorBarSegment | null }) {
  const lineOn = active === 'linewidth'
  const capOn = active === 'capsize' || active === 'cap_thickness'
  // 底色用 ink-3 而不是 ink-faint：没聚焦时这张图也得看得见，它是图注不是水印
  const seg = (on: boolean) => (on ? 'text-ink' : 'text-ink-3')

  return (
    <svg
      aria-hidden
      viewBox="0 0 72 52"
      width="72"
      height="52"
      className="shrink-0"
      data-errorbar-diagram
      data-active={active ?? ''}
    >
      {/* 竖线（线宽） */}
      <line
        data-seg="linewidth"
        data-active={lineOn || undefined}
        x1={CX}
        y1={TOP}
        x2={CX}
        y2={BOTTOM}
        stroke="currentColor"
        strokeWidth={lineOn ? 2.8 : 1.2}
        className={cn('transition-[stroke-width]', seg(lineOn))}
      />
      {/* 两条端帽 */}
      {[TOP, BOTTOM].map((y) => (
        <line
          key={y}
          data-seg="cap"
          data-active={capOn || undefined}
          x1={CX - CAP_HALF}
          y1={y}
          x2={CX + CAP_HALF}
          y2={y}
          stroke="currentColor"
          strokeWidth={active === 'cap_thickness' ? 4 : 1.6}
          className={cn('transition-[stroke-width]', seg(capOn))}
        />
      ))}
      {/* 端帽长度：顶端帽上方一条带端点的量尺，两端与端帽两端对齐 */}
      {active === 'capsize' && (
        <path
          data-seg="capsize-measure"
          d={`M${CX - CAP_HALF} ${MEASURE_Y} H${CX + CAP_HALF}
              M${CX - CAP_HALF} ${MEASURE_Y - 2.5} V${MEASURE_Y + 2.5}
              M${CX + CAP_HALF} ${MEASURE_Y - 2.5} V${MEASURE_Y + 2.5}`}
          stroke="currentColor"
          strokeWidth="1"
          fill="none"
          className="text-ink"
        />
      )}
    </svg>
  )
}
