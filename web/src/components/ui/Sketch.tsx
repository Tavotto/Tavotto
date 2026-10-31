import { useId, type ReactNode } from 'react'

/**
 * 手帐线稿（2026-10-28，framed-icons 审计 B 栏）：空状态的小插图。
 *
 * 一套封闭的黑白线稿——54×42，全部 1px 发丝线，坐标落在 .5 上（1× 与 2× 都是锐利的单像素线），
 * 填充一律 45° 铅笔排线（与进度条 `.progress-track`、缩略图占位 `.hatch-box` 同一种排线语言），
 * 只用 `currentColor`（调用处给 ink-faint，亮暗两套主题各自成色）；不画底座、不上色、不描粗。
 *
 * 它不是图标：图标集（`icons/`）管「小控件里的符号」，这里管「一屏的空状态主图」。
 * 加新画法之前先问：已有的这些里有没有意思相同的？同义复用，不为一个调用处新画。
 *
 * 排线瓦片是 4×4 的整像素阶梯（SVG `crispEdges`），不靠 `patternTransform` 旋转——
 * 旋转的 1px 线在 2× 屏上会糊成两像素的灰。
 */
export type SketchName =
  | 'select' // 选一张图：纸 + 折角排线 + 虚线选框与四角手柄
  | 'canvas' // 空画布：纸 + 裁切角标 + 侧边排线
  | 'history' // 任务历史：叠放的纸，最上一张页眉排线
  | 'versions' // 版本：错位叠放的三张实线纸
  | 'ok' // 没有问题：纸 + 对勾
  | 'code' // 脚本：纸 + 尖括号
  | 'search' // 无匹配：纸 + 放大镜
  | 'warn' // 出错：纸 + 感叹号
  | 'pencil' // 改图 / 样式：纸 + 铅笔
  | 'sheet' // 通用：折角纸 + 几行字
  | 'drop' // 拖入：.py 文件卡（折角排线、三行代码）落进托盘，三道错落的下落节拍

export const SKETCH_SIZE = { w: 54, h: 42 } as const
/**
 * 尺寸例外（2026-10-28，redesign-3 C1）：首页拖放区是整屏的主图，画布放大到 96×72；
 * 其余仍是 54×42。线宽、.5 坐标、排线瓦片都不变——只是画布更大，不是缩放（缩放会糊）。
 */
export const SKETCH_SIZE_OVERRIDE: Partial<Record<SketchName, { w: number; h: number }>> = {
  drop: { w: 96, h: 72 },
}

const SHEET = 'M12.5 3.5H33.5L42.5 12.5V38.5H12.5Z'
const EAR = 'M33.5 3.5V12.5H42.5Z'
const PAPER = 'var(--color-surface)'

function Paper({ hatch }: { hatch: string }) {
  return (
    <>
      <path d={SHEET} fill={PAPER} />
      <path d={EAR} fill={hatch} />
      <path d="M33.5 3.5V12.5H42.5" />
    </>
  )
}

const HANDLES = [
  [17.5, 19.5],
  [37.5, 19.5],
  [17.5, 33.5],
  [37.5, 33.5],
] as const

function Drawing({ name, hatch }: { name: SketchName; hatch: string }): ReactNode {
  switch (name) {
    case 'select':
      return (
        <>
          <Paper hatch={hatch} />
          <rect x="17.5" y="19.5" width="20" height="14" strokeDasharray="2 2" />
          {HANDLES.map(([x, y]) => (
            <rect key={`${x}-${y}`} x={x - 1.5} y={y - 1.5} width="3" height="3" fill={PAPER} />
          ))}
        </>
      )
    case 'canvas':
      return (
        <>
          <path d="M42.5 8.5H45.5V38.5H15.5V35.5H42.5Z" fill={hatch} stroke="none" />
          <rect x="12.5" y="5.5" width="30" height="30" fill={PAPER} />
          <path d="M7.5 5.5H3.5M12.5 0.5V3.5M42.5 0.5V3.5M47.5 5.5H50.5M7.5 35.5H3.5M12.5 37.5V40.5M42.5 37.5V40.5M47.5 35.5H50.5" />
        </>
      )
    case 'history':
      return (
        <>
          <rect x="19.5" y="3.5" width="24" height="30" strokeDasharray="2 2" />
          <rect x="12.5" y="9.5" width="24" height="30" fill={PAPER} />
          <rect x="12.5" y="9.5" width="24" height="6" fill={hatch} />
          <path d="M12.5 15.5H36.5M17.5 22.5H31.5M17.5 27.5H31.5M17.5 32.5H25.5" />
        </>
      )
    case 'versions':
      return (
        <>
          <rect x="20.5" y="3.5" width="24" height="30" fill={PAPER} />
          <rect x="16.5" y="6.5" width="24" height="30" fill={PAPER} />
          <rect x="12.5" y="9.5" width="24" height="30" fill={PAPER} />
          <rect x="12.5" y="9.5" width="24" height="6" fill={hatch} />
          <path d="M12.5 15.5H36.5M17.5 23.5H31.5M17.5 28.5H25.5" />
        </>
      )
    case 'ok':
      return (
        <>
          <Paper hatch={hatch} />
          <path d="M20.5 25.5L25.5 30.5L35.5 20.5" strokeLinecap="round" />
        </>
      )
    case 'code':
      return (
        <>
          <Paper hatch={hatch} />
          <path d="M24.5 21.5L19.5 26.5L24.5 31.5M30.5 21.5L35.5 26.5L30.5 31.5" strokeLinecap="round" />
        </>
      )
    case 'search':
      return (
        <>
          <Paper hatch={hatch} />
          <path d="M17.5 19.5H26.5M17.5 24.5H23.5" />
          <circle cx="32.5" cy="27.5" r="6" fill={PAPER} />
          <path d="M37 32L42.5 37.5" strokeLinecap="round" />
        </>
      )
    case 'warn':
      return (
        <>
          <Paper hatch={hatch} />
          <path d="M27.5 20.5V28.5M27.5 32.5V33.5" strokeLinecap="round" />
        </>
      )
    case 'pencil':
      return (
        <>
          <rect x="10.5" y="5.5" width="26" height="32" fill={PAPER} />
          <path d="M15.5 12.5H29.5M15.5 17.5H25.5M15.5 22.5H21.5" />
          <path d="M42.5 10.5L46.5 14.5L35.5 25.5L28.5 28.5L31.5 21.5Z" fill={PAPER} />
          <path d="M31.5 21.5L35.5 25.5L28.5 28.5Z" fill={hatch} />
          <path d="M31.5 21.5L35.5 25.5" />
        </>
      )
    case 'sheet':
      return (
        <>
          <Paper hatch={hatch} />
          <path d="M18.5 21.5H36.5M18.5 26.5H36.5M18.5 31.5H27.5" />
        </>
      )
    case 'drop':
      // 构图：文件卡（焦点）在托盘里；托盘前壁遮住卡底 = 景深；顶部三道错落的短线 = 下落的节拍。
      // `sk-fall` 一组（卡 + 节拍）拖入时整体下移 4px，落点虚线 `sk-landing` 换 accent（index.css，由 data-dragging 驱动）
      return (
        <>
          <g className="sk-fall">
            <path d="M40.5 1.5v3M47.5 3.5v3M54.5 1.5v3" />
            <path d="M29.5 9.5H55.5L66.5 20.5V52.5H29.5Z" fill={PAPER} />
            <path d="M55.5 9.5V20.5H66.5Z" fill={hatch} />
            <path d="M55.5 9.5V20.5H66.5" />
            <path d="M36.5 28.5H52.5M40.5 33.5H59.5M40.5 38.5H50.5" strokeLinecap="round" />
          </g>
          <path d="M10.5 43.5H85.5V64.5H10.5Z" fill={PAPER} />
          <rect x="10.5" y="58.5" width="75" height="6" fill={hatch} stroke="none" />
          <path d="M10.5 58.5H85.5M10.5 43.5V64.5H85.5V43.5Z" />
          <path
            className="sk-landing"
            d="M22.5 51.5H73.5"
            strokeDasharray="1 2"
            strokeLinecap="butt"
            style={{ stroke: 'var(--sk-landing, currentColor)' }}
          />
        </>
      )
  }
}

export function Sketch({ name, className }: { name: SketchName; className?: string }) {
  const id = `sk${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const { w, h } = SKETCH_SIZE_OVERRIDE[name] ?? SKETCH_SIZE
  return (
    <svg
      aria-hidden="true"
      data-sketch={name}
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1"
      strokeLinejoin="round"
      className={className}
    >
      <defs>
        <pattern id={id} patternUnits="userSpaceOnUse" width="4" height="4" shapeRendering="crispEdges">
          <g fill="currentColor" stroke="none">
            <rect x="3" y="0" width="1" height="1" />
            <rect x="2" y="1" width="1" height="1" />
            <rect x="1" y="2" width="1" height="1" />
            <rect x="0" y="3" width="1" height="1" />
          </g>
        </pattern>
      </defs>
      <Drawing name={name} hatch={`url(#${id})`} />
    </svg>
  )
}
