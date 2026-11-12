/**
 * 动画卡提示的三个小场景（ADR 0177）：一段写好的光标戏，不是真画布、不碰任何 store。
 * **每个动作都要和真行为一致**（改了手势先改这里）：
 *   - fit：双击纸外灰色区域 → 适应画布（`CanvasStage.onDoubleClick` → `fitStage`）；
 *   - edge：多图排版里拖图边 = 改图幅、文字不变；按住 ⌥ = 裁剪、内容不动（`canvas/panelEdgeCrop`，ADR 0161）；
 *   - guide：从上方标尺往下拖出横向参考线，拖对象靠近时吸上去（`startGuideDrag` / 吸附）；
 *   - axis：拖对象时按住 ⇧ 只沿位移较大的那个方向走（`startMoveDrag` 的 `axisLock`）；
 *   - snap：拖对象靠近别的对象的边会吸上去，按住 ⌘ / Ctrl 这一下不吸（`startMoveDrag` 的 `metaKey || ctrlKey`）；
 *   - nudge：方向键一步 0.5 mm、⇧ 一步 5 mm（`canvas/nudge.ts` 的 `NUDGE_STEP_MM`）；
 *   - text：双击图里带文字内容的元素 = 快速改字，弹层贴着光标、内容输入框已聚焦（`PanelView` 双击 → `openQuickEdit({ focusText })`）；
 *   - draw：矩形 / 椭圆工具拖的时候按住 ⇧ 锁成正方形 / 正圆，锚在起点（`startDraw`）；
 *   - pan：按住空格，指针变成手，拖动平移画布（`CanvasStage` 的 `spaceDown` → `startPan`）；
 *   - multi：图内 ⇧ 点击加选（`PanelView.onPrimaryDown` 的 `toggleSelectedGid`），属性改动落到选中的每一个；右栏只示意成一角；
 *   - help：按 ? 打开快捷键帮助（`useKeyboard` → `ShortcutHelp`），表里的键位只是示意，不是全表；
 *   - rotate：拖标注上方的旋转柄，按住 ⇧ 按 15° 一档（`canvas/rotateDrag.ts` 的 `ROTATE_SNAP_STEP`）；真画布转的时候没有角度读数，这里也不画。
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

/* ───────── 小工具：淡出再回到开头（看不见的重置） ───────── */

async function fadeReset<S extends { hide: boolean }>(a: Act<S>, initial: S) {
  await a.set({ hide: true } as Partial<S>)
  await a.wait(320)
  await a.snap({ ...initial, hide: true })
  await a.set({ hide: false } as Partial<S>)
  await a.wait(200)
}

/** 两张图的小版面：a 固定，b 由场景摆 */
function TwoPanels({
  bx,
  by,
  selected,
  ease,
  children,
}: {
  bx: number
  by: number
  selected?: boolean
  ease?: boolean
  children?: React.ReactNode
}) {
  return (
    <div className="hint-page" style={{ left: 40, top: 26, width: 240, height: 128 }}>
      <div style={{ position: 'absolute', left: 16, top: 26 }}>
        <MiniPlot w={84} h={76} tag="a" t={1} />
      </div>
      <div
        className="hint-panel"
        data-target="b"
        data-selected={selected || undefined}
        data-move-ease={ease || undefined}
        style={{ left: bx, top: by, width: 84, height: 76 }}
      >
        <MiniPlot w={84} h={76} tag="b" tone={1} t={1} />
      </div>
      {children}
    </div>
  )
}
const B0 = { x: 116, y: 26 }
const REST = { x: 28, y: 164 }

/* ───────── 4. 按住 ⇧ 拖动：只沿一个方向走 ───────── */

type AxisS = { bx: number; by: number; drag: boolean; ease: boolean; keys: string[] | null; down: boolean; lock: boolean; hide: boolean }
const AXIS0: AxisS = { bx: B0.x, by: B0.y, drag: false, ease: false, keys: null, down: false, lock: false, hide: false }
const AXIS_GO = { x: 30, y: 9 }

const axisScript = async (a: Act<AxisS>) => {
  await a.wait(500)
  // 不按键：手一歪，图就跟着歪下去了
  await a.move('b')
  let from = a.center('b')
  await a.set({ drag: true, ease: false })
  await a.drag({ x: from.x + AXIS_GO.x, y: from.y + AXIS_GO.y }, (p, o) =>
    a.put({ bx: B0.x + Math.round(p.x - o.x), by: B0.y + Math.round(p.y - o.y) }),
  )
  await a.set({ drag: false })
  await a.wait(900)
  await a.set({ keys: [MOD, 'Z'], down: true })
  await a.wait(160)
  await a.set({ bx: B0.x, by: B0.y, ease: true, down: false })
  await a.wait(500)
  await a.set({ keys: null })
  await a.wait(300)
  // 按住 ⇧：手照样歪，图只横着走
  await a.move('b')
  from = a.center('b')
  await a.set({ keys: ['⇧'], down: true, ease: false })
  await a.wait(350)
  await a.set({ drag: true, lock: true })
  await a.drag({ x: from.x + AXIS_GO.x, y: from.y + AXIS_GO.y }, (p, o) => a.put({ bx: B0.x + Math.round(p.x - o.x) }))
  await a.set({ drag: false })
  await a.wait(1900)
  await a.set({ keys: null, lock: false })
  await a.move(REST)
  await fadeReset(a, AXIS0)
}

function AxisScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<AxisS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={AXIS0}
      poster={{ ...AXIS0, bx: B0.x + AXIS_GO.x, keys: ['⇧'], down: true, lock: true }}
      script={looped(axisScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <TwoPanels bx={s.bx} by={s.by} selected={s.drag || s.lock} ease={s.ease}>
              <i className="hint-axis" data-show={s.lock || undefined} style={{ top: B0.y + 38 }} />
            </TwoPanels>
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 5. 按住 ⌘ / Ctrl 拖动：暂时不吸附 ───────── */

type SnapS = { by: number; drag: boolean; snapped: boolean; keys: string[] | null; down: boolean; hide: boolean }
const SNAP_START = 44
const SNAP_AT = 26
const SNAP_WANT = 30
const SNAP0: SnapS = { by: SNAP_START, drag: false, snapped: false, keys: null, down: false, hide: false }
const SNAP_TOL = 6

const snapScript = async (a: Act<SnapS>) => {
  await a.wait(500)
  // 往上拖：靠近 a 的上沿就被吸过去
  await a.move('b')
  let from = a.center('b')
  await a.set({ drag: true })
  await a.drag({ x: from.x, y: from.y - (SNAP_START - SNAP_WANT) }, (p, o) => {
    const raw = SNAP_START + (p.y - o.y)
    const snap = Math.abs(raw - SNAP_AT) <= SNAP_TOL
    a.put({ by: snap ? SNAP_AT : Math.round(raw), snapped: snap })
  })
  await a.set({ by: SNAP_AT, snapped: false, drag: false })
  await a.wait(700)
  // 想往下挪一点点：又被吸回去
  await a.move('b')
  from = a.center('b')
  await a.set({ drag: true })
  await a.drag({ x: from.x, y: from.y + (SNAP_WANT - SNAP_AT) }, () => a.put({ by: SNAP_AT, snapped: true }), { duration: 500 })
  await a.set({ snapped: false, drag: false })
  await a.wait(700)
  // 按住 ⌘ / Ctrl：想放哪就放哪
  await a.set({ keys: [MOD], down: true })
  await a.wait(350)
  await a.move('b')
  from = a.center('b')
  await a.set({ drag: true })
  await a.drag({ x: from.x, y: from.y + (SNAP_WANT - SNAP_AT) }, (p, o) => a.put({ by: SNAP_AT + Math.round(p.y - o.y) }), {
    duration: 600,
  })
  await a.set({ by: SNAP_WANT, drag: false })
  await a.wait(1900)
  await a.set({ keys: null })
  await a.move(REST)
  await fadeReset(a, SNAP0)
}

function SnapScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<SnapS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={SNAP0}
      poster={{ ...SNAP0, by: SNAP_WANT, keys: [MOD], down: true }}
      script={looped(snapScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <TwoPanels bx={B0.x} by={s.by} selected={s.drag} />
            {/* 吸附线：只在拖动中、吸上的那一帧闪现（满色，与真画布同一约定） */}
            <div className="hint-snapline" data-show={s.snapped || undefined} style={{ top: 26 + SNAP_AT }} />
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 6. 方向键微移 ───────── */

type NudgeS = { bx: number; by: number; keys: string[] | null; down: boolean; hide: boolean }
const NUDGE0: NudgeS = { bx: B0.x, by: B0.y + 6, keys: null, down: false, hide: false }
const STEP = 2 // 0.5 mm 一步（示意）
const BIG = 20 // ⇧ 一步 5 mm

const nudgeScript = async (a: Act<NudgeS>) => {
  await a.wait(700)
  const tap = async (caps: string[], patch: (s: NudgeS) => Partial<NudgeS>) => {
    await a.set({ keys: caps, down: true })
    await a.set(patch)
    await a.wait(170)
    await a.set({ down: false })
    await a.wait(230)
  }
  // 差一点对不齐：按三下 ↑
  for (let i = 0; i < 3; i++) await tap(['↑'], (s) => ({ by: s.by - STEP }))
  await a.wait(900)
  // ⇧ = 大步
  await tap(['⇧', '→'], (s) => ({ bx: s.bx + BIG }))
  await a.wait(1700)
  await a.set({ keys: null })
  await fadeReset(a, NUDGE0)
}

function NudgeScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<NudgeS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={NUDGE0}
      poster={{ ...NUDGE0, by: B0.y, keys: ['↑'], down: false }}
      script={looped(nudgeScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <TwoPanels bx={s.bx} by={s.by} selected ease />
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 7. 按住 ⇧ 旋转：15° 一档 ───────── */

type RotS = { deg: number; ease: boolean; keys: string[] | null; down: boolean; hide: boolean }
const ROT0: RotS = { deg: 0, ease: false, keys: null, down: false, hide: false }
const ROT_C = { x: 160, y: 98 }
const ROT_BOX = { w: 104, h: 36 }
const KNOB_GAP = 16
const ROT_R = ROT_BOX.h / 2 + KNOB_GAP
const rad = (d: number) => (d * Math.PI) / 180
const knobAt = (deg: number) => ({ x: ROT_C.x + ROT_R * Math.sin(rad(deg)), y: ROT_C.y - ROT_R * Math.cos(rad(deg)) })
const degAt = (p: { x: number; y: number }) => (Math.atan2(p.x - ROT_C.x, -(p.y - ROT_C.y)) * 180) / Math.PI

const rotScript = async (a: Act<RotS>) => {
  await a.wait(500)
  const turn = async (to: number, snap: boolean) => {
    await a.move(knobAt(0))
    await a.drag(knobAt(to), (p) => a.put({ deg: snap ? Math.round(degAt(p) / 15) * 15 : Math.round(degAt(p)) }), {
      duration: 1100,
      path: (t) => knobAt(to * t),
    })
  }
  // 不按键：想转 45°，停在了 41°
  await a.set({ ease: false })
  await turn(41, false)
  await a.wait(900)
  await a.set({ keys: [MOD, 'Z'], down: true })
  await a.wait(160)
  await a.set({ deg: 0, ease: true, down: false })
  await a.wait(500)
  await a.set({ keys: null })
  await a.wait(300)
  // 按住 ⇧：一档一档跳，正好 45°
  await a.set({ keys: ['⇧'], down: true, ease: false })
  await a.wait(350)
  await turn(43, true)
  await a.set({ deg: 45 })
  await a.wait(1900)
  await a.set({ keys: null })
  await a.move(REST)
  await fadeReset(a, ROT0)
}

function RotateScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<RotS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={ROT0}
      poster={{ ...ROT0, deg: 45, keys: ['⇧'], down: true }}
      script={looped(rotScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" style={{ left: 40, top: 26, width: 240, height: 128 }} />
            <div
              className="hint-note"
              data-ease={s.ease || undefined}
              style={{
                left: ROT_C.x - ROT_BOX.w / 2,
                top: ROT_C.y - ROT_BOX.h / 2,
                width: ROT_BOX.w,
                height: ROT_BOX.h,
                transform: `rotate(${s.deg}deg)`,
              }}
            >
              <span className="hint-note__text" />
              <i className="hint-handle" data-side="nw" />
              <i className="hint-handle" data-side="ne" />
              <i className="hint-handle" data-side="sw" />
              <i className="hint-handle" data-side="se" />
              <i className="hint-knob" style={{ top: -KNOB_GAP - 4 }} />
            </div>
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 8. 双击图里的文字直接改 ───────── */

type TextS = { open: boolean; typed: string; hide: boolean }
const TEXT0: TextS = { open: false, typed: '', hide: false }
/** 示意图的标题：样本图里的内容（像用户脚本里写的那样是英文），不是界面文案 */
const SAMPLE_TITLE = 'Kinetics'

const textScript = async (a: Act<TextS>) => {
  await a.wait(600)
  await a.click('title', { double: true })
  await a.set({ open: true })
  await a.wait(700)
  await a.type('typed', ' (a)', { interval: 110 })
  await a.wait(2100)
  await a.move(REST)
  await fadeReset(a, TEXT0)
}

function TextScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<TextS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={TEXT0}
      poster={{ ...TEXT0, open: true, typed: ' (a)' }}
      script={looped(textScript, onLoop)}
      cursor={{ at: REST, idle: false, ripple: true }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" style={{ left: 70, top: 14, width: 180, height: 152 }}>
              <span className="hint-title">
                <span data-target="title" data-picked={s.open || undefined}>
                  {SAMPLE_TITLE}
                </span>
              </span>
              <div style={{ position: 'absolute', left: 22, top: 34 }}>
                <MiniPlot w={136} h={108} tag="" t={1} />
              </div>
            </div>
            {/* 快速改字：贴着光标出来的小弹层，内容输入框已聚焦 */}
            <div className="hint-quick" data-show={s.open || undefined} style={{ left: 148, top: 46 }}>
              <span className="hint-quick__field">
                {SAMPLE_TITLE}
                {s.typed}
                <i className="hint-caret" />
              </span>
            </div>
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ───────── 9. 按住 ⇧ 画正方形 ───────── */

type DrawS = { w: number; h: number; drawing: boolean; ease: boolean; keys: string[] | null; down: boolean; hide: boolean }
const DRAW0: DrawS = { w: 0, h: 0, drawing: false, ease: false, keys: null, down: false, hide: false }
const DRAW_AT = { x: 116, y: 50 }
const DRAW_GO = { x: 76, y: 62 }

const drawScript = async (a: Act<DrawS>) => {
  await a.wait(500)
  const pull = async (square: boolean) => {
    await a.move(DRAW_AT)
    await a.set({ drawing: true, ease: false })
    await a.drag(
      { x: DRAW_AT.x + DRAW_GO.x, y: DRAW_AT.y + DRAW_GO.y },
      (p, o) => {
        const w = Math.max(0, Math.round(p.x - o.x))
        const h = Math.max(0, Math.round(p.y - o.y))
        const side = Math.max(w, h)
        a.put(square ? { w: side, h: side } : { w, h })
      },
      { duration: 900, cursor: 'preserve' },
    )
    await a.set({ drawing: false })
  }
  // 不按键：想画正方形，画成了长方形
  await pull(false)
  await a.wait(900)
  await a.set({ keys: [MOD, 'Z'], down: true })
  await a.wait(160)
  await a.set({ w: 0, h: 0, down: false })
  await a.wait(450)
  await a.set({ keys: null })
  await a.wait(250)
  // 按住 ⇧：锁成正方形
  await a.set({ keys: ['⇧'], down: true })
  await a.wait(350)
  await pull(true)
  await a.wait(1900)
  await a.set({ keys: null })
  await a.move(REST)
  await fadeReset(a, DRAW0)
}

function DrawScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<DrawS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={DRAW0}
      poster={{ ...DRAW0, w: DRAW_GO.x, h: DRAW_GO.x, keys: ['⇧'], down: true }}
      script={looped(drawScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" data-cursor="crosshair" style={{ left: 40, top: 26, width: 240, height: 128 }} />
            {s.w > 0 && (
              <div
                className="hint-shape"
                data-drawing={s.drawing || undefined}
                style={{ left: DRAW_AT.x, top: DRAW_AT.y, width: s.w, height: s.h }}
              />
            )}
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 10. 按住空格拖动平移 ───────── */

type PanS = { dx: number; dy: number; keys: string[] | null; down: boolean; hand: boolean; hide: boolean }
const PAN0: PanS = { dx: 0, dy: 0, keys: null, down: false, hand: false, hide: false }
const PAN_GO = { x: -96, y: -54 }

const panScript = async (a: Act<PanS>) => {
  await a.wait(600)
  await a.move({ x: 190, y: 110 })
  await a.set({ keys: ['Space'], down: true, hand: true })
  await a.wait(450)
  await a.drag({ x: 190 + PAN_GO.x, y: 110 + PAN_GO.y }, (p, o) => a.put({ dx: Math.round(p.x - o.x), dy: Math.round(p.y - o.y) }), {
    duration: 1300,
  })
  await a.wait(500)
  await a.set({ keys: null, hand: false })
  await a.wait(1700)
  await a.move(REST)
  await fadeReset(a, PAN0)
}

function PanScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<PanS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={PAN0}
      poster={{ ...PAN0, dx: PAN_GO.x, dy: PAN_GO.y, keys: ['Space'], down: true, hand: true }}
      script={looped(panScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work" data-cursor={s.hand ? 'open' : undefined}>
          <div className="hint-fade" data-hide={s.hide || undefined}>
            {/* 放大着：纸面比窗口大，只看得见左上那一块 */}
            <div className="hint-page" style={{ left: 24, top: 20, width: 420, height: 250, transform: `translate(${s.dx}px, ${s.dy}px)` }}>
              <div style={{ position: 'absolute', left: 20, top: 18 }}>
                <MiniPlot w={180} h={150} tag="a" />
              </div>
              <div style={{ position: 'absolute', left: 220, top: 18 }}>
                <MiniPlot w={180} h={150} tag="b" tone={1} />
              </div>
            </div>
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 11. ⇧ 点击多选，一次改完 ───────── */

type MultiS = { sel: number[]; tone: number; keys: string[] | null; down: boolean; hide: boolean }
const MULTI0: MultiS = { sel: [], tone: 0, keys: null, down: false, hide: false }
const CURVES = ['M24 118 C70 60 120 40 196 34', 'M24 126 C80 104 130 78 196 66']

const multiScript = async (a: Act<MultiS>) => {
  await a.wait(600)
  await a.click('curve-0')
  await a.set({ sel: [0] })
  await a.wait(500)
  await a.set({ keys: ['⇧'], down: true })
  await a.wait(350)
  await a.click('curve-1')
  await a.set({ sel: [0, 1] })
  await a.wait(500)
  await a.set({ keys: null })
  // 去右栏点一个颜色：两条线一起变
  await a.click('swatch')
  await a.set({ tone: 1 })
  await a.wait(2100)
  await a.move(REST)
  await fadeReset(a, MULTI0)
}

function MultiScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<MultiS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={MULTI0}
      poster={{ ...MULTI0, sel: [0, 1], tone: 1 }}
      script={looped(multiScript, onLoop)}
      cursor={{ at: REST, idle: false, ripple: true }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" style={{ left: 16, top: 14, width: 220, height: 152 }}>
              <svg width={220} height={152} className="hint-plot">
                <rect x={20.5} y={12.5} width={184} height={122} className="hint-plot__axes" />
                {CURVES.map((d, i) => (
                  <g key={i}>
                    {s.sel.includes(i) && <path d={d} className="hint-curve-halo" />}
                    <path d={d} className="hint-plot__curve hint-curve" data-tone={s.tone ? 2 : i} />
                  </g>
                ))}
              </svg>
              <i data-target="curve-0" style={{ position: 'absolute', left: 104, top: 44, width: 8, height: 8 }} />
              <i data-target="curve-1" style={{ position: 'absolute', left: 124, top: 82, width: 8, height: 8 }} />
            </div>
            {/* 右栏的一角：颜色那一行 */}
            <div className="hint-side">
              <span className="hint-side__row" />
              <span className="hint-side__swatches">
                <i data-c="0" />
                <i data-c="1" />
                <i data-c="2" data-target="swatch" data-on={s.tone ? true : undefined} />
              </span>
              <span className="hint-side__row" />
              <span className="hint-side__row" data-short />
            </div>
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

/* ───────── 12. 按 ? 看全部快捷键 ───────── */

type HelpS = { open: boolean; keys: string[] | null; down: boolean; hide: boolean }
const HELP0: HelpS = { open: false, keys: null, down: false, hide: false }
const HELP_ROWS: [number, string[]][] = [
  [58, [MOD, 'Z']],
  [74, ['⇧', '2']],
  [46, [ALT, '↑']],
  [66, ['Space']],
  [52, [MOD, 'K']],
  [70, ['?']],
]

const helpScript = async (a: Act<HelpS>) => {
  await a.wait(700)
  await a.set({ keys: ['?'], down: true })
  await a.wait(200)
  await a.set({ open: true, down: false })
  await a.wait(600)
  await a.set({ keys: null })
  await a.wait(2600)
  await fadeReset(a, HELP0)
}

function HelpScene({ theme, play, onLoop }: SceneProps) {
  return (
    <Stage<HelpS>
      specimen
      unframed
      width={W}
      height={H}
      theme={theme}
      play={play}
      className="hint-stage"
      initial={HELP0}
      poster={{ ...HELP0, open: true }}
      script={looped(helpScript, onLoop)}
      cursor={{ at: REST, idle: false }}
    >
      {(s) => (
        <div className="hint-work">
          <div className="hint-fade" data-hide={s.hide || undefined}>
            <div className="hint-page" style={{ left: 40, top: 26, width: 240, height: 128 }}>
              <div style={{ position: 'absolute', left: 16, top: 26 }}>
                <MiniPlot w={84} h={76} tag="a" t={1} />
              </div>
              <div style={{ position: 'absolute', left: 116, top: 26 }}>
                <MiniPlot w={84} h={76} tag="b" tone={1} t={1} />
              </div>
            </div>
            <div className="hint-sheet" data-show={s.open || undefined}>
              {HELP_ROWS.map(([w, caps], i) => (
                <span key={i} className="hint-sheet__row">
                  <i style={{ width: w }} />
                  <span>
                    {caps.map((c) => (
                      <kbd key={c}>{c}</kbd>
                    ))}
                  </span>
                </span>
              ))}
            </div>
          </div>
          <Keys caps={s.keys} down={s.down} />
        </div>
      )}
    </Stage>
  )
}

const SCENES: Record<CardHintKind, (p: SceneProps) => React.JSX.Element> = {
  fit_canvas: FitScene,
  edge_crop: EdgeScene,
  ruler_guides: GuideScene,
  shift_axis: AxisScene,
  no_snap: SnapScene,
  arrow_nudge: NudgeScene,
  shift_rotate: RotateScene,
  dblclick_text: TextScene,
  shift_draw: DrawScene,
  space_pan: PanScene,
  shift_multi: MultiScene,
  shortcut_help: HelpScene,
}

/** 一张卡的动画。`key` 由调用方给（换提示 / 重播时重挂） */
export default function HintScene({ kind, ...rest }: SceneProps & { kind: CardHintKind }) {
  const Scene = SCENES[kind]
  return <Scene {...rest} />
}
