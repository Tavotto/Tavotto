import { useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import type { Rect4 } from '@/lib/axesLayout'
import { elementBoxOf, geomTarget } from '@/lib/elementGeom'
import { boundsOf, visualBounds, type Rect } from '@/lib/geometry'
import { formatMm } from '@/lib/units'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useDisplayedExactManifest } from '@/store/mountedSvgStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { fastEditPanelOf, useWorkspaceStore } from '@/store/workspace'
import { renderedSelection } from '@/store/zoomToSelection'
import type { PanelObject } from '@/types/document'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore } from '@/store/viewportStore'
import { elementDeltaOnPage, elementRectOnPage } from './elementGeometry'

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
 * 元素**下面（Codex #833）。位置与 Δ 都取 `InFigureMove.preview` 发布的那份预览——与 `ElementBoxes` 画选中框
 * 同一个来源（`elementPreview` 的框 / `gidDrag` 的位移，叠在显示中的几何权威上）：子图贴边时 `axesMove` 把框
 * 钳住，芯片跟着停、Δ 报的是真正挪了的量，不是按了几下的总和。权威缺席（上一段刚提交、渲染没回来）时不报：
 * 拿面板的框顶替会贴错地方。
 */
export function MeasureChip() {
  perfCount('render.MeasureChip')
  useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const nudge = useInteractionStore((s) => s.nudge)
  const draft = useInteractionStore((s) => s.draft)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  // 快速编辑里舞台上只画那一张图：选区里别的对象（从图层抽屉 ⇧ 选进来的）看不见，不进读数也不进锚点（Codex #833）
  const fastEditPanelId = useWorkspaceStore(fastEditPanelOf)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  // 底部浮动工具条显示时舞台底边让出的高度（`TOOLBAR_FIT_CLEARANCE`，工具条自己按显示判据写进来，隐藏 /
  // 快速编辑时为 0）：与「适应」取景同一个值。工具条的层级高于芯片，贴进那一条就被盖住（Codex #833）
  const bottomClear = useViewportStore((s) => s.fitBottomClear)
  // 图内编辑态：方向键推的是图内选中的元素（与 `nudge.ts` 的 `currentTargetKey` 同一判据）
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedGids = useUiStore((s) => s.selectedGids)
  const editedPanel = nudge && elementPanelId ? panelById(objects, elementPanelId) : null
  const manifest = useDisplayedExactManifest(editedPanel)
  const gidDrag = useInteractionStore((s) => s.gidDrag)
  const elementPreview = useInteractionStore((s) => s.elementPreview)

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
  const sel = kind === 'draw' && draft ? [] : renderedSelection(objects, ids, fastEditPanelId)
  const box: Rect | null = kind === 'draw' && draft ? { x: draft.x, y: draft.y, w: draft.w, h: draft.h } : boundsOf(sel)
  const fig =
    figureNudge && editedPanel && manifest
      ? figureNudgeGeometry(editedPanel, manifest, selectedGids, gidDrag, elementPreview)
      : null
  const anchor: Rect | null = figureNudge
    ? (fig?.anchor ?? null)
    : sel.length
      ? boundsOf(sel.map(visualBounds))
      : box
  // 图内微调的读数只要 Δ、不读 `box`（那是画布选区的盒，面板没选中时为 null）
  if (!anchor || (!box && !figureNudge)) return null

  // 图内微调报预览里真正挪了的量（贴边钳住时小于按键总和）；预览还没发布时退回按键的累计
  const delta = fig?.delta ?? nudge
  const text =
    mode === 'offset'
      ? `Δ ${translate('measure.mmPair', { a: signedMm(delta!.dx), b: signedMm(delta!.dy) })}`
      : mode === 'size'
        ? translate('measure.mmSize', { w: formatMm(Math.abs(box!.w)), h: formatMm(Math.abs(box!.h)) })
        : translate('measure.mmPair', { a: formatMm(box!.x), b: formatMm(box!.y) })

  const t = { zoom, panX, panY, originX: 0, originY: 0 }
  const left = mmToViewX(anchor.x, t) + mmToPx(anchor.w, t) / 2
  const top = mmToViewY(anchor.y, t)
  const bottom = top + mmToPx(anchor.h, t)
  const below = bottom + GAP + CHIP_H <= viewH - bottomClear || !viewH
  return (
    <ChipAt
      mode={mode}
      text={text}
      left={left}
      top={below ? bottom + GAP : top - GAP - CHIP_H}
      viewW={viewW}
      viewH={viewH}
      bottomClear={bottomClear}
    />
  )
}

/**
 * 先按「框下 / 放不下翻到框上」摆（`left` 是芯片中心、`top` 是上沿），再整体夹进舞台（Codex #833）：选区几乎占满
 * 或超出视口时上下都放不下，翻上去会落到负坐标、被舞台的 overflow-hidden 裁掉；横向同理（框中心在视口外时芯片
 * 半截出界）。底部浮动工具条那一条（`bottomClear`）不算可用的舞台：上沿翻转与纵向夹取都让开它。
 * 芯片宽随读数变（w-max），读数一变就在绘制之前量一次。
 */
function ChipAt(props: {
  mode: MeasureMode
  text: string
  left: number
  top: number
  viewW: number
  viewH: number
  bottomClear: number
}) {
  const { mode, text, viewW, viewH, bottomClear } = props
  const ref = useRef<HTMLDivElement>(null)
  const [chipW, setChipW] = useState(0)
  useLayoutEffect(() => {
    setChipW(ref.current?.offsetWidth ?? 0)
  }, [text])
  const y = clamp(props.top, EDGE, viewH - Math.max(EDGE, bottomClear) - CHIP_H, viewH)
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

type ElementDrag = { gid: string; dfx: number; dfy: number } | null
type ElementPreview = { boxes: Record<string, Rect4>; group?: Rect4 } | null

/**
 * 图内微调此刻的样子：被推的元素在页面上的外接框（mm）与真正挪了的量（页面 mm）。
 *
 * 与 `ElementBoxes` 的 `resolve` 同一套取法：元素取它的几何落点（`geomTarget`：位图落在宿主子图上），选中的组
 * 展开成成员；框优先取 `elementPreview` 里的（子图 / 成组平移发布的，**已钳位**），否则权威 bbox 叠上
 * `gidDrag` 的分数位移。分数框落到页面走 `canvas/elementGeometry.elementRectOnPage`——与选中框、画布画这张图
 * 同一个变换（先翻转再旋转）；翻转过的面板上不跟着翻，芯片就贴到元素的镜像位置上去了（#832 评审）。
 *
 * 挪了的量取主选（最后一个有预览的目标）：`elementPreview` 的框减去它的起手框——起手框是 `elementBoxOf`，
 * 与 `alignEntries` 交给 `groupMove` 的 `box`、`axesMove` 的 `positionOf` 同一个出处（子图取 position，锚定
 * 元素取按当前锚点修正过的墨迹框）——或直接是 `gidDrag` 的位移；
 * 内容分数向量经同一个变换换回页面 mm（`elementDeltaOnPage`）——报的是元素在页面上看得见的挪动方向。一个目标都解析不出来时回 null。
 */
function figureNudgeGeometry(
  panel: PanelObject,
  manifest: Manifest,
  gids: readonly string[],
  gidDrag: ElementDrag,
  preview: ElementPreview,
): { anchor: Rect; delta: { dx: number; dy: number } | null } | null {
  const targets = new Map<string, ManifestElement>()
  const add = (gid: string) => {
    const el = manifest.elements.find((e) => e.gid === gid)
    if (!el || el.gid === 'figure') return
    const target = geomTarget(manifest, el)
    targets.set(target.gid, target)
  }
  for (const gid of gids) {
    const group = manifest.groups?.find((g) => g.gid === gid)
    if (group) group.members.forEach(add)
    else add(gid)
  }
  const rects: Rect[] = []
  let moved: [number, number] | null = null
  for (const target of targets.values()) {
    const pv = preview?.boxes[target.gid]
    const drag = gidDrag?.gid === target.gid ? gidDrag : null
    let box: Rect4 = target.bbox
    if (pv) {
      // 起手框与 `alignEntries` 的 `box`（`groupMove` / `axesMove` 的起点）同一个出处：子图取 position，
      // 锚定元素取按当前锚点修正过的墨迹框——直接减 manifest bbox 会把它此前的 override 算进 Δ（Codex #833）
      const start = elementBoxOf(panel, target) ?? target.bbox
      box = pv
      moved = [pv[0] - start[0], pv[1] - start[1]]
    } else if (drag) {
      box = [box[0] + drag.dfx, box[1] + drag.dfy, box[2], box[3]]
      moved = [drag.dfx, drag.dfy]
    }
    rects.push(elementRectOnPage(panel, box))
  }
  const anchor = boundsOf(rects)
  if (!anchor) return null
  if (!moved) return { anchor, delta: null }
  const [dx, dy] = elementDeltaOnPage(panel, moved[0], moved[1])
  return { anchor, delta: { dx, dy } }
}

/** 位移读数带正负号：「+1.5」「−0.5」「0.0」 */
function signedMm(v: number): string {
  const s = formatMm(Math.abs(v))
  if (s === formatMm(0)) return s
  return v > 0 ? `+${s}` : `−${s}`
}
