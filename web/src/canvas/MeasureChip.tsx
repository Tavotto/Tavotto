import { useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { t as translate } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { geomTarget, panelFullRect } from '@/lib/elementGeom'
import { boundsOf, visualBounds, type Rect } from '@/lib/geometry'
import { formatMm } from '@/lib/units'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useDisplayedExactManifest } from '@/store/mountedSvgStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { panelRotation, rotateVec, type PanelObject } from '@/types/document'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore } from '@/store/viewportStore'

/** 改的是大小的那几种拖动：芯片说 W × H；移动说对象的 X, Y；方向键微调说这一段挪了多少（Δ） */
const SIZE_KINDS = new Set(['resize', 'draw', 'crop', 'endpoint'])
const MOVE_KINDS = new Set(['move'])

/** 芯片与它所贴的框之间的缝；放不下（贴到视口底）就翻到框的上面 */
const GAP = 8
const CHIP_H = 22
/** 芯片与舞台边缘至少留的距离：舞台 `overflow-hidden`，贴出去的那一截会被裁掉 */
const EDGE = 4

export type MeasureMode = 'size' | 'position' | 'offset'

/**
 * 贴着选区的尺寸芯片（2026-10-07 设计审计 §10.1）：拖动 / 缩放 / 画框 / 方向键微调进行中，在被改的那个
 * 框正下方居中显示一个读数——W × H、对象的 X, Y、或这一段微调的 Δ。
 *
 * 此前读数在画布左下角的 HUD 里，而且移动时报的是**指针**坐标，不是对象的位置：用户要的是「这张图现在
 * 在哪」，不是「我的鼠标在哪」。读数贴着它说的那个东西，视线不用在画布与左下角之间来回跳。
 *
 * 只是读数：不吃指针事件、不进 Tab 顺序。几何来自文档里的对象（画布对象的拖动在事务里实时改文档）或
 * 正在画的草稿框；图内元素的指针拖动（预览平面，不改文档）不在这里报。
 *
 * 图内元素的方向键微调（图内编辑态，`canvas/nudge.ts` 推的是 `selectedGids` 而不是画布选区）贴在**被推的
 * 元素**下面（Codex #833）：起手框取显示中的几何权威（与 `ElementBoxes` 画选中框同一份），加上这一段的 Δ
 * ——微调只动预览平面，权威在收尾前不变，所以「权威框 + Δ」就是元素此刻在页面上的位置。权威缺席（上一段
 * 刚提交、渲染没回来）时不报：拿面板的框顶替会贴错地方。
 */
export function MeasureChip() {
  perfCount('render.MeasureChip')
  useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const nudge = useInteractionStore((s) => s.nudge)
  const draft = useInteractionStore((s) => s.draft)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  // 图内编辑态：方向键推的是图内选中的元素（与 `nudge.ts` 的 `currentTargetKey` 同一判据）
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedGids = useUiStore((s) => s.selectedGids)
  const editedPanel = nudge && elementPanelId ? panelById(objects, elementPanelId) : null
  const manifest = useDisplayedExactManifest(editedPanel)

  const mode: MeasureMode | null = nudge
    ? 'offset'
    : SIZE_KINDS.has(kind)
      ? 'size'
      : MOVE_KINDS.has(kind)
        ? 'position'
        : null
  if (!mode) return null
  const figureNudge = !!nudge && !!elementPanelId

  // 被改的那个框：画新对象时是草稿框，其余是选区里看得见的对象。读数（W × H / X, Y）说的是逻辑盒（未旋转的
  // x/y/w/h，与属性页同一套数）；芯片**贴在**看得见的外接框下面（`visualBounds` 的并，Codex #833）——
  // 旋转的文字 / 形状转出来比逻辑盒高时，按逻辑盒摆会压在对象上。
  const sel = kind === 'draw' && draft ? [] : objects.filter((o) => ids.includes(o.id) && !o.hidden)
  const box: Rect | null = kind === 'draw' && draft ? { x: draft.x, y: draft.y, w: draft.w, h: draft.h } : boundsOf(sel)
  const anchor: Rect | null = figureNudge
    ? editedPanel && manifest
      ? shift(elementsOnPage(editedPanel, manifest, selectedGids), nudge!.dx, nudge!.dy)
      : null
    : sel.length
      ? boundsOf(sel.map(visualBounds))
      : box
  // 图内微调的读数只要 Δ、不读 `box`（那是画布选区的盒，面板没选中时为 null）
  if (!anchor || (!box && !figureNudge)) return null

  const text =
    mode === 'offset'
      ? `Δ ${translate('measure.mmPair', { a: signedMm(nudge!.dx), b: signedMm(nudge!.dy) })}`
      : mode === 'size'
        ? translate('measure.mmSize', { w: formatMm(Math.abs(box!.w)), h: formatMm(Math.abs(box!.h)) })
        : translate('measure.mmPair', { a: formatMm(box!.x), b: formatMm(box!.y) })

  const t = { zoom, panX, panY, originX: 0, originY: 0 }
  const left = mmToViewX(anchor.x, t) + mmToPx(anchor.w, t) / 2
  const top = mmToViewY(anchor.y, t)
  const bottom = top + mmToPx(anchor.h, t)
  const below = bottom + GAP + CHIP_H <= viewH || !viewH
  return (
    <ChipAt
      mode={mode}
      text={text}
      left={left}
      top={below ? bottom + GAP : top - GAP - CHIP_H}
      viewW={viewW}
      viewH={viewH}
    />
  )
}

/**
 * 先按「框下 / 放不下翻到框上」摆（`left` 是芯片中心、`top` 是上沿），再整体夹进舞台（Codex #833）：选区几乎占满
 * 或超出视口时上下都放不下，翻上去会落到负坐标、被舞台的 overflow-hidden 裁掉；横向同理（框中心在视口外时芯片
 * 半截出界）。芯片宽随读数变（w-max），读数一变就在绘制之前量一次。
 */
function ChipAt(props: { mode: MeasureMode; text: string; left: number; top: number; viewW: number; viewH: number }) {
  const { mode, text, viewW, viewH } = props
  const ref = useRef<HTMLDivElement>(null)
  const [chipW, setChipW] = useState(0)
  useLayoutEffect(() => {
    setChipW(ref.current?.offsetWidth ?? 0)
  }, [text])
  const y = clamp(props.top, EDGE, viewH - EDGE - CHIP_H, viewH)
  const x = clamp(props.left, EDGE + chipW / 2, viewW - EDGE - chipW / 2, viewW)
  return (
    <div
      ref={ref}
      data-measure-chip={mode}
      className="pointer-events-none absolute z-sticky w-max -translate-x-1/2 rounded-full bg-ink px-2 py-0.5 text-xs font-medium tabular-nums text-surface shadow-pop"
      style={{ left: x, top: y }}
    >
      {text}
    </div>
  )
}

/** 夹进 [lo, hi]；舞台还没量到尺寸（`size` 为 0）时不夹；舞台比芯片还窄时贴住起始边 */
function clamp(v: number, lo: number, hi: number, size: number): number {
  if (!size) return v
  return Math.max(lo, Math.min(v, hi))
}

function panelById(objects: readonly { id: string; type: string }[], id: string): PanelObject | null {
  const o = objects.find((x) => x.id === id)
  return o?.type === 'panel' ? (o as PanelObject) : null
}

/**
 * 选中的图内元素（或组）在页面上的外接框（mm）：与 `ElementBoxes` 同一套换算——元素取它的几何落点
 * （`geomTarget`：位图落在宿主子图上），分数框（top-origin）按 `panelFullRect` 落到内容坐标，再绕面板中心
 * 转到面板当前的朝向（只有直角，转完仍是轴对齐的框）。一个都解析不出来时回 null。
 */
function elementsOnPage(panel: PanelObject, manifest: Manifest, gids: readonly string[]): Rect | null {
  const full = panelFullRect(panel)
  const rot = panelRotation(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  const rects: Rect[] = []
  for (const gid of gids) {
    const el = manifest.elements.find((e) => e.gid === gid)
    const bbox =
      el && el.gid !== 'figure'
        ? geomTarget(manifest, el).bbox
        : manifest.groups?.find((g) => g.gid === gid)?.bbox
    if (!bbox) continue
    const w = bbox[2] * full.w
    const h = bbox[3] * full.h
    const [ox, oy] = rotateVec(full.x + bbox[0] * full.w + w / 2 - cx, full.y + bbox[1] * full.h + h / 2 - cy, rot)
    const [rw, rh] = rot === 90 || rot === 270 ? [h, w] : [w, h]
    rects.push({ x: cx + ox - rw / 2, y: cy + oy - rh / 2, w: rw, h: rh })
  }
  return boundsOf(rects)
}

const shift = (r: Rect | null, dx: number, dy: number): Rect | null =>
  r && { x: r.x + dx, y: r.y + dy, w: r.w, h: r.h }

/** 位移读数带正负号：「+1.5」「−0.5」「0.0」 */
function signedMm(v: number): string {
  const s = formatMm(Math.abs(v))
  if (s === formatMm(0)) return s
  return v > 0 ? `+${s}` : `−${s}`
}
