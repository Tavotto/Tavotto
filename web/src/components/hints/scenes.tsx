/**
 * 动画卡提示的三个小场景（ADR 0177）：一段写好的光标戏，不是真画布、不碰任何 store。
 * **每个动作都要和真行为一致**（改了手势先改这里）：
 *   - fit：双击纸外灰色区域 → 适应画布（`CanvasStage.onDoubleClick` → `fitStage`）；
 *   - edge：多图排版里拖图边 = 改图幅、文字不变；按住 ⌥ = 裁剪、内容不动（`canvas/panelEdgeCrop`，ADR 0161）；
 *   - guide：从上方标尺往下拖出横向参考线，拖对象靠近时吸上去（`startGuideDrag` / 吸附）。
 * 胶囊里的字用真画布同一条译文（`workspace:contextBar.*`），键帽按平台（`ALT` / `MOD`）。
 * 舞台、光标、拖拽来自 vendor/stageshow（MIT）；本文件整块按需加载（`HintCard` 里 `lazy`）。
 */
import { useTranslation } from 'react-i18next'
import { ALT, MOD } from '@/lib/utils'
import type { CardHintKind } from '@/lib/onboarding/hints'
import { Stage } from '@/vendor/stageshow/stage'
import type { Act } from '@/vendor/stageshow/act'
import './hintScenes.css'

type Theme = 'light' | 'dark'
/** 一遍演完叫一次（`HintCard` 数到两遍就停在定格帧） */
type SceneProps = { theme: Theme; play: 'loop' | 'hover'; onLoop: () => void }
export const HINT_STAGE = { width: 320, height: 180 } as const
const W = HINT_STAGE.width
const H = HINT_STAGE.height

const looped =
  <S,>(script: (a: Act<S>) => Promise<void>, onLoop: () => void) =>
  async (a: Act<S>) => {
    await script(a)
    onLoop()
  }

/** 迷你 matplotlib 图：坐标轴框 + 一条曲线 + 刻度。宽度变 = 重新排版（字不变）。 */
function MiniPlot({ w, h, tag, tone = 0, t = 6 }: { w: number; h: number; tag: string; tone?: number; t?: number }) {
  const l = 13
  const r = 5
  const b = 12
  const aw = Math.max(8, w - l - r)
  const ah = h - t - b
  const pts =
    tone === 0
      ? [0, 0.15, 0.18, 0.5, 0.36, 0.72, 0.58, 0.85, 0.8, 0.92, 1, 0.95]
      : [0, 0.9, 0.2, 0.62, 0.42, 0.4, 0.62, 0.3, 0.82, 0.2, 1, 0.16]
  let d = ''
  for (let i = 0; i < pts.length; i += 2) {
    d += `${i ? 'L' : 'M'}${(l + pts[i] * aw).toFixed(1)} ${(t + ah - pts[i + 1] * ah).toFixed(1)}`
  }
  const ticks = [0.2, 0.5, 0.8]
  return (
    <svg width={w} height={h} className="hint-plot" style={{ display: 'block', flex: 'none' }}>
      <rect x={l + 0.5} y={t + 0.5} width={aw} height={ah} className="hint-plot__axes" />
      {ticks.map((k) => (
        <line key={`x${k}`} x1={l + k * aw} x2={l + k * aw} y1={t + ah} y2={t + ah + 2.5} className="hint-plot__tick" />
      ))}
      {ticks.map((k) => (
        <line key={`y${k}`} x1={l - 2.5} x2={l} y1={t + k * ah} y2={t + k * ah} className="hint-plot__tick" />
      ))}
      {ticks.map((k) => (
        <rect key={`lx${k}`} x={l + k * aw - 3} y={t + ah + 5} width={6} height={2.5} rx={1} className="hint-plot__label" />
      ))}
      {ticks.map((k) => (
        <rect key={`ly${k}`} x={2} y={t + k * ah - 1.25} width={6} height={2.5} rx={1} className="hint-plot__label" />
      ))}
      <path d={d} className="hint-plot__curve" data-tone={tone} />
      <text x={l + 5} y={t + 10} className="hint-plot__tag">
        {tag}
      </text>
    </svg>
  )
}

function Keys({ caps, down }: { caps: string[] | null; down?: boolean }) {
  return (
    <div className="hint-keys" data-show={caps ? true : undefined}>
      {(caps ?? []).map((c) => (
        <kbd key={c} className="hint-key" data-down={down || undefined}>
          {c}
        </kbd>
      ))}
    </div>
  )
}

/* ───────── 1. 双击空白处 → 画布回正 ───────── */

type FitS = { dx: number; dy: number; glide: boolean }
const FIT_AWAY = { dx: 118, dy: 74 }

const fitScript = async (a: Act<FitS>) => {
  await a.wait(500)
  // 起因：把画布拖跑了
  await a.move({ x: 150, y: 86 })
  await a.drag({ x: 150 + FIT_AWAY.dx, y: 86 + FIT_AWAY.dy }, (p, o) => a.put({ dx: p.x - o.x, dy: p.y - o.y, glide: false }), {
    duration: 900,
  })
  await a.wait(500)
  // 双击纸外的灰色区域
  await a.click({ x: 78, y: 62 }, { double: true })
  await a.set({ dx: 0, dy: 0, glide: true })
  await a.wait(2000)
  await a.set({ glide: false })
  await a.move({ x: 250, y: 150 })
  await a.wait(300)
}

function FitScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<FitS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={{ dx: 0, dy: 0, glide: false }}
      poster={{ dx: 0, dy: 0, glide: false }}
      script={looped(fitScript, onLoop)}
      cursor={{ at: { x: 250, y: 150 }, ripple: true, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div
            className="hint-page"
            data-glide={s.glide || undefined}
            style={{ left: 50, top: 36, width: 220, height: 108, transform: `translate(${s.dx}px, ${s.dy}px)` }}
          >
            <div style={{ position: 'absolute', left: 10, top: 10 }}>
              <MiniPlot w={96} h={88} tag="a" />
            </div>
            <div style={{ position: 'absolute', left: 114, top: 10 }}>
              <MiniPlot w={96} h={88} tag="b" tone={1} />
            </div>
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ───────── 2. 拖边改图幅；按住 ⌥ 改为裁剪 ───────── */

type EdgeLabel = { kind: 'grow' | 'crop'; mm: number }
type EdgeS = { w: number; crop: number; held: boolean; keys: string[] | null; down: boolean; label: EdgeLabel | null; ease: boolean }
const EDGE_W = 104
const EDGE0: EdgeS = { w: EDGE_W, crop: 0, held: false, keys: null, down: false, label: null, ease: false }
const MM = 0.5 // 1px = 0.5 mm（示意）

const edgeScript = async (a: Act<EdgeS>) => {
  await a.wait(500)
  // 不按键：改图幅，图重新排版
  await a.move('edge')
  let from = a.center('edge')
  await a.set({ held: true, ease: false })
  await a.drag(
    { x: from.x - 30, y: from.y },
    (p, o) => {
      const w = Math.round(EDGE_W + (p.x - o.x))
      a.put({ w, label: { kind: 'grow', mm: w * MM } })
    },
    { duration: 800, cursor: 'preserve' },
  )
  await a.set({ held: false })
  await a.wait(1100)
  // 撤销（看得见的原因）
  await a.set({ keys: [MOD, 'Z'], down: true, label: null })
  await a.wait(160)
  await a.set({ w: EDGE_W, ease: true, down: false })
  await a.wait(500)
  await a.set({ keys: null })
  await a.wait(350)
  // 按住 ⌥：裁剪，内容不动
  await a.move('edge')
  from = a.center('edge')
  await a.set({ keys: [ALT], down: true, ease: false })
  await a.wait(350)
  await a.set({ held: true })
  await a.drag(
    { x: from.x - 30, y: from.y },
    (p, o) => {
      const crop = Math.round(-(p.x - o.x))
      a.put({ crop, label: { kind: 'crop', mm: crop * MM } })
    },
    { duration: 800, cursor: 'preserve' },
  )
  await a.set({ held: false })
  await a.wait(1900)
  await a.set({ keys: [MOD, 'Z'], down: true, label: null })
  await a.wait(160)
  await a.set({ crop: 0, ease: true, down: false })
  await a.wait(500)
  await a.set({ keys: null, ease: false })
  await a.move({ x: 262, y: 160 })
  await a.wait(300)
}

function EdgeScene({ theme, play, onLoop }: SceneProps) {
  const { t } = useTranslation('workspace')
  return (
    <Stage<EdgeS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={EDGE0}
      poster={{ ...EDGE0, crop: 30, keys: [ALT], down: true, label: { kind: 'crop', mm: 30 * MM } }}
      script={looped(edgeScript, onLoop)}
      cursor={{ at: { x: 262, y: 160 }, idle: false }}
    >
      {(s) => {
        const vis = s.w - s.crop
        const label = !s.label
          ? ' '
          : s.label.kind === 'grow'
            ? t('contextBar.layoutGrowWidth', { from: String(EDGE_W * MM), to: String(Math.round(s.label.mm)) })
            : t('contextBar.layoutCropContent', { side: t('contextBar.layoutSideR'), mm: s.label.mm.toFixed(1) })
        return (
          <div className="hint-work">
            <div className="hint-page" style={{ left: 40, top: 22, width: 240, height: 116 }}>
              <div style={{ position: 'absolute', left: 150, top: 12 }}>
                <MiniPlot w={80} h={92} tag="b" tone={1} />
              </div>
              <div
                className="hint-panel"
                data-ease={s.ease || undefined}
                data-selected
                style={{ left: 12, top: 12, width: vis, height: 92 }}
              >
                <div className="hint-panel__clip">
                  <MiniPlot w={s.w} h={92} tag="a" />
                </div>
                <i className="hint-handle" data-side="nw" />
                <i className="hint-handle" data-side="sw" />
                <i className="hint-handle" data-side="ne" />
                <i className="hint-handle" data-side="se" />
                <i className="hint-edge" data-target="edge" data-cursor="resize-ew" data-held={s.held || undefined} />
                <span className="hint-capsule" data-show={s.label ? true : undefined}>
                  {label}
                </span>
              </div>
            </div>
            <Keys caps={s.keys} down={s.down} />
          </div>
        )
      }}
    </Stage>
  )
}

/* ───────── 3. 从标尺拖出对齐线 ───────── */

type GuideS = { gy: number | null; by: number; drag: boolean; snapped: boolean; hide: boolean }
const RULER = 12
const A_TOP = 48
const B_TOP0 = 60
const GUIDE0: GuideS = { gy: null, by: B_TOP0, drag: false, snapped: false, hide: false }

const guideScript = async (a: Act<GuideS>) => {
  await a.wait(500)
  // 从上方标尺拖出一条横线，停在 a 图上沿
  await a.move({ x: 208, y: RULER / 2 })
  await a.drag({ x: 208, y: A_TOP }, (p) => a.put({ gy: Math.max(RULER, Math.round(p.y)) }), {
    duration: 900,
    cursor: 'preserve',
    path: (t, from, to) => ({ x: from.x, y: from.y + (to.y - from.y) * t }),
  })
  await a.set({ gy: A_TOP })
  await a.wait(500)
  // 拖 b 图：靠近就吸上去
  await a.move({ x: 226, y: 104 })
  await a.set({ drag: true })
  await a.drag(
    { x: 226, y: 104 - (B_TOP0 - A_TOP) },
    (p, o) => {
      const raw = B_TOP0 + (p.y - o.y)
      const snap = Math.abs(raw - A_TOP) <= 4
      a.put({ by: snap ? A_TOP : Math.round(raw), snapped: snap })
    },
    { duration: 1000 },
  )
  await a.set({ by: A_TOP, snapped: true })
  await a.wait(500)
  await a.set({ drag: false, snapped: false })
  await a.move({ x: 276, y: 162 })
  await a.wait(1600)
  // 重来：淡出再回到开头
  await a.set({ hide: true })
  await a.wait(320)
  await a.snap({ ...GUIDE0, hide: true })
  await a.set({ hide: false })
  await a.wait(200)
}

function Ruler({ axis }: { axis: 'x' | 'y' }) {
  const n = axis === 'x' ? 26 : 14
  return (
    <div className="hint-ruler" data-axis={axis} data-cursor={axis === 'x' ? 'resize-ns' : 'resize-ew'}>
      {Array.from({ length: n }, (_, i) => (
        <i key={i} data-major={i % 5 === 0 || undefined} style={axis === 'x' ? { left: 6 + i * 12 } : { top: 6 + i * 12 }} />
      ))}
    </div>
  )
}

function GuideScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<GuideS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={GUIDE0}
      poster={{ ...GUIDE0, gy: A_TOP, by: A_TOP }}
      script={looped(guideScript, onLoop)}
      cursor={{ at: { x: 276, y: 162 }, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" style={{ left: 44, top: 34, width: 244, height: 124 }}>
              <div style={{ position: 'absolute', left: 14, top: A_TOP - 34 }}>
                <MiniPlot w={100} h={84} tag="a" t={1} />
              </div>
              <div className="hint-panel" data-selected={s.drag || undefined} style={{ left: 130, top: s.by - 34, width: 100, height: 84 }}>
                <MiniPlot w={100} h={84} tag="b" tone={1} t={1} />
              </div>
            </div>
            {s.gy != null && <div className="hint-guide" data-snap={s.snapped || undefined} style={{ top: s.gy }} />}
          </div>
          <Ruler axis="x" />
          <Ruler axis="y" />
          <div className="hint-ruler-corner" />
        </div>
      )}
    </Stage>
  )
}

const SCENES: Record<CardHintKind, (p: SceneProps) => React.JSX.Element> = {
  fit_canvas: FitScene,
  edge_crop: EdgeScene,
  ruler_guides: GuideScene,
}

/** 一张卡的动画。`key` 由调用方给（换提示 / 重播时重挂） */
export default function HintScene({ kind, ...rest }: SceneProps & { kind: CardHintKind }) {
  const Scene = SCENES[kind]
  return <Scene {...rest} />
}
