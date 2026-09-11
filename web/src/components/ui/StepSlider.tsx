import {
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'

import { cn } from '@/lib/utils'

/** 刻度点 hover 的命中半径。按点自身的 3.5px 判定会一碰就掉，给一段宽裕的容差。 */
const DOT_HIT_PX = 14

/**
 * 离散步进滑杆。**每一格对应调用方给的一个真实取值**，绝不产生中间态——
 * 值域就是 `options`，`value` 是它的下标。
 *
 * 为什么不是一排同宽按钮：六档等宽按钮在 232–320px 的弹层里必然拥挤或截断
 * （见 docs/ux/img/ux-consistency-pass/before/zh-1440-ai-popover.png：
 * minimal 与 xhigh 两头都被切掉）。滑杆的宽度与档位数无关，加到八档也不会溢出。
 *
 * 无障碍：原生 `<input type="range">`，方向键 / Home / End 免费拿到，
 * 触屏免费拿到；`aria-valuetext` 报的是**当前语言的档位名**而不是下标数字
 * （屏幕阅读器念「3」毫无意义）。轨道上的小点表明它是离散的。
 *
 * 形态是一条**厚胶囊**（48px 轨 + 56px 白钮），不是系统默认的细线滑杆：
 * 看得见的那整块就是命中区，48px 高的轨本身即触控目标。白钮不描边，
 * 状态对比交给填充段与浅灰轨的边界（#2868b7 / #e3e3dd ≈ 4.9:1，过非文字
 * 3:1）；键盘焦点另有 focus ring，不靠描边表达。
 *
 * 看得见的每一层（轨 / 填充 / 刻度 / 白钮）都由 span 自己画，原生 `<input>`
 * 整层透明、只留交互与无障碍：原生拇指的位置由 value 直接算出、**无法 transition**，
 * 只要它还负责显示，换档就永远是硬跳。焦点环靠 `peer-focus-visible` 从 input
 * 接到白钮上；禁用态挂在最外层（视觉层已不在 input 里，挂它身上不起作用）。
 */
export function StepSlider({
  value,
  count,
  onChange,
  ariaLabel,
  valueText,
  disabled,
  className,
}: {
  /** 当前档位下标（0 基） */
  value: number
  /** 总档位数 */
  count: number
  onChange: (index: number) => void
  ariaLabel: string
  /** 当前档位的可读名，进 aria-valuetext */
  valueText: string
  disabled?: boolean
  className?: string
}) {
  const max = Math.max(0, count - 1)
  const isDisabled = disabled || max === 0
  const rootRef = useRef<HTMLDivElement>(null)
  // 鼠标当前压住的那一档：只影响刻度点的轻微放大，不参与取值
  const [hoveredDot, setHoveredDot] = useState<number | null>(null)
  // 拇指圆心的行程是 [半个拇指, 宽度 − 半个拇指]。填充的终点、每个刻度点、白钮的圆心
  // 都按这一段换算（共用 at()），否则末档的点会顶到轨道尽头、跟停下来的拇指对不上
  // （旧版 pct% + justify-between 就是这么错位的）。--thumb / --track / --dot / --track-w
  // / --edge 是这几个尺寸的唯一出处：拇指宽高、轨道厚度、刻度直径、整体宽度上限、轨道
  // 两端的缩进都只从这里读，别在下面写字面量。
  const at = (i: number) =>
    `calc((100% - var(--thumb)) * ${max === 0 ? 0 : i / max} + var(--thumb) / 2)`
  // 填充住在轨道里，它的 100% 是**轨道**宽（左右各比根窄一个 --edge），直接套 at() 会把
  // 缩进也按比例摊进行程里、越往右越偏。先把两个缩进补回成根宽、再减掉左边那个，右端
  // 就依旧精确落在拇指圆心上（edge + fillTo(i) === at(i)）。
  const fillTo = (i: number) =>
    `calc((100% + var(--edge) * 2 - var(--thumb)) * ${max === 0 ? 0 : i / max} + var(--thumb) / 2 - var(--edge))`
  // 刻度点自己收不到指针事件：原生 input 盖在最上面吃掉全部交互，而它是拖拽与键盘的
  // 唯一入口，不能让位。所以从冒泡到根节点的 pointermove 反算最近一档，复用 at() 的
  // 同一段行程——根节点高度就是 --thumb（h-[var(--thumb)]），行程 = 宽 − 拇指，
  // 圆心 = 拇指/2 + 行程 × i/max，不引入第二个尺寸来源。只认鼠标：触屏抬手没有
  // pointerleave，放大的点会留在那儿复不了位。
  const handlePointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = rootRef.current
    if (e.pointerType !== 'mouse' || isDisabled || !el) return
    const rect = el.getBoundingClientRect()
    const travel = rect.width - rect.height
    if (travel <= 0) {
      setHoveredDot(null)
      return
    }
    const x = e.clientX - rect.left
    const i = Math.min(max, Math.max(0, Math.round(((x - rect.height / 2) / travel) * max)))
    const center = rect.height / 2 + (travel * i) / max
    setHoveredDot(Math.abs(x - center) <= DOT_HIT_PX ? i : null)
  }
  return (
    <div
      ref={rootRef}
      onPointerMove={handlePointerMove}
      onPointerLeave={() => setHoveredDot(null)}
      className={cn(
        'relative flex h-[var(--thumb)] w-full min-w-0 max-w-[var(--track-w)] items-center',
        // 视觉层已从 input 搬到 span 上，禁用态只能在最外层压
        isDisabled && 'opacity-40',
        className,
      )}
      style={
        {
          '--thumb': '56px',
          '--track': '48px',
          '--dot': '7px',
          '--track-w': '460px',
          // 轨道两端的缩进 = 钮半径 − 轨半径。取这个值，最低档时轨道端头的圆（r=24）与
          // 白钮的圆（r=28）正好**同心**，端头四周留一样宽的 4px 白边。
          '--edge': 'calc((var(--thumb) - var(--track)) / 2)',
        } as CSSProperties
      }
    >
      {/* 厚胶囊轨道：整条是一个 pill（48px ≈ 拇指直径的 85%，不是细线），未走过的一段
          是浅灰底。它同时是填充的**裁剪框**（overflow-hidden + 同一个 rounded-full）：
          填充自己的 border-radius 在宽度小于轨高时会被浏览器等比压扁成尖头，交给轨道裁，
          画多宽都只能留在槽里。
          左右各缩进一个 --edge：轨道端头的圆（r=24）原本与白钮的圆（r=28）**内切**于最左
          端的中点，白钮盖到那里是零余量，抗锯齿一混就沿着端头漏出一线蓝——最低档看到的
          那几个蓝像素就是它，宽度取整不同还会时有时无。缩进后端头与白钮同心、四周 4px
          余量，蓝色再也够不到槽边；最高档那头的灰端头同理不露边。 */}
      <span
        aria-hidden
        className="pointer-events-none absolute left-[var(--edge)] right-[var(--edge)] top-1/2 h-[var(--track)] -translate-y-1/2 overflow-hidden rounded-full bg-border"
      >
        {/* 已走过的一段：同一条胶囊的左半边，主题色，右端收在拇指圆心——那半个圆头正好
            被 56px 的白钮盖住，两段之间不会露出接缝或断层。第 0 档照常画到圆心：缩进后
            轨道端头整个退进白钮里（同心，四周 4px 余量），这一档的填充全躲在钮底下，所以
            不需要「归零」特例——归零反而会让填充从 at(1) 一路缩到 0，中间每帧都是被压扁
            的尖头。宽度与白钮的 left 用同一档时长和缓动过渡，蓝色边界始终贴着钮走，不会
            先到或落后半拍。 */}
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-ink transition-[width] duration-[var(--duration-base)] ease-[var(--ease-pop)]"
          style={{ width: fillTo(value) }}
        />
      </span>
      {/* 离散刻度点：一档一个，显式压在胶囊的垂直中心线上（top-1/2 + -translate-y-1/2，
          不靠 flex 的静态位置猜），表明这不是连续滑杆。走过的一段用同色系的浅蓝
          （在主题色上 4.9:1，看得清又不抢戏），没走到的用稍深的灰；换色跟着钮一起过渡。 */}
      {count > 1 &&
        Array.from({ length: count }, (_, i) => (
          <span
            key={i}
            aria-hidden
            className={cn(
              'pointer-events-none absolute top-1/2 h-[var(--dot)] w-[var(--dot)] -translate-x-1/2 -translate-y-1/2 rounded-full',
              'transition-[background-color,scale] duration-[var(--duration-base)] ease-[var(--ease-pop)]',
              i <= value ? 'bg-selected' : 'bg-ink-faint',
            )}
            // scale 是独立属性，与 -translate-x-1/2 用的 translate 属性互不干扰，放大时
            // 圆心不会跑偏（换成 transform: scale 就会顶掉那半格居中位移）。
            style={{ left: at(i), scale: !isDisabled && hoveredDot === i ? '1.35' : '1' }}
          />
        ))}
      {/* 交互层：全透明的原生 range。方向键 / Home / End、触屏拖拽、role 与 aria 全在
          它身上，只是不负责显示。拇指保持 56px，让拖拽命中区和看得见的白钮严格重合。 */}
      <input
        type="range"
        min={0}
        max={max}
        step={1}
        value={value}
        disabled={isDisabled}
        aria-label={ariaLabel}
        aria-valuetext={valueText}
        onChange={(e) => onChange(Number(e.target.value))}
        className={cn(
          'peer relative h-[var(--thumb)] w-full cursor-pointer appearance-none bg-transparent outline-none',
          'disabled:cursor-default',
          // 轨道由上面两层 span 画，原生轨道让位：透明 + 与输入框等高，这样拇指和
          // 轨道等高、天然垂直居中，不用给拇指补 margin-top 去凑。
          '[&::-webkit-slider-runnable-track]:h-[var(--thumb)] [&::-webkit-slider-runnable-track]:bg-transparent',
          '[&::-moz-range-track]:bg-transparent',
          // 原生拇指只当命中区：等尺寸、但完全透明（它的 left 由 value 直接算出，
          // 没有可过渡的属性，让它显示就永远做不出滑过去的动画）。
          '[&::-webkit-slider-thumb]:h-[var(--thumb)] [&::-webkit-slider-thumb]:w-[var(--thumb)]',
          '[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full',
          '[&::-webkit-slider-thumb]:border-0 [&::-webkit-slider-thumb]:bg-transparent',
          '[&::-moz-range-thumb]:h-[var(--thumb)] [&::-moz-range-thumb]:w-[var(--thumb)]',
          '[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0',
          '[&::-moz-range-thumb]:bg-transparent',
        )}
      />
      {/* 看得见的拇指：56px 实心白圆钮，无描边、无蓝色轮廓，只有系统唯一允许的那层轻投影
          （--shadow-pop）让它略微悬浮。left 走过渡，所以换档是滑过去而不是跳过去；关掉
          动效（prefers-reduced-motion）后位置依旧正确。焦点环从 input 接过来
          （peer-focus-visible）——原生 focus ring 会套住整条轨道，所以不用它。 */}
      <span
        aria-hidden
        className={cn(
          'pointer-events-none absolute top-1/2 h-[var(--thumb)] w-[var(--thumb)] -translate-x-1/2 -translate-y-1/2 rounded-full',
          'bg-surface shadow-pop',
          'transition-[left] duration-[var(--duration-base)] ease-[var(--ease-pop)]',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-accent/50',
        )}
        style={{ left: at(value) }}
      />
    </div>
  )
}
