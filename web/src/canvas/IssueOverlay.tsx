import { useMemo } from 'react'
import { t as translate } from '@/i18n'
import { geomTarget, isElementHidden, panelFullRect } from '@/lib/elementGeom'
import { openProblemAt, focusFailureMessage } from '@/lib/issueFocus'
import { visualBounds } from '@/lib/geometry'
import { currentFigureOf } from '@/lib/problemContext'
import { SEVERITIES, type Severity } from '@/lib/profile'
import type { ValidationIssue } from '@/lib/validation'
import { severityLabel } from '@/lib/validationText'
import { useDocumentStore } from '@/store/documentStore'
import { useDisplayedExactManifest } from '@/store/mountedSvgStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import { objectRotation, type CanvasObject, type PanelObject } from '@/types/document'
import { elementOverlayTransform } from './elementGeometry'
import { PIN_COLORS, tokenVar } from './issuePinColors'

/**
 * 问题面板在画布上的两样东西（2026-10-07 设计审计 §9.4），从 `OverlaySvg` 里挂进来、自己一个文件——
 * 覆盖层的其余语法（选择框、手柄、参考线）归外壳，这里只画「问题」：
 *
 * * **悬停轮廓**：问题面板里指着一行（`uiStore.issueHover`），画布上那个对象画一道与画布自己的
 *   hover 预示同一种画法的轮廓（sel 色、`--sel-hover-opacity`）。只是「我在看它」：不选中、不定位。
 *   问题带 gid（图内元素）时描那个元素的框（`ElementHover`），解不出来才退回整张图。
 * * **等级标记**（`uiStore.problemPins`，默认关，问题面板「⋯」里打开）：每张有问题的图右上角外侧一枚
 *   等级色小圆 + 项数；点它 = `openProblemAt`（定位 + 问题面板点开它所在的那一支、落游标）——
 *   与左栏样式面板直达走同一个入口，不另写第二套「跳到问题」。
 *
 * 定位之后那一下闪（`issueHighlight`）仍由 `OverlaySvg` 画（token 关键帧 `animate-attention`）。
 */
export function IssueOverlay({ objects, t }: { objects: readonly CanvasObject[]; t: ViewTransform }) {
  const hover = useUiStore((s) => s.issueHover)
  const pins = useUiStore((s) => s.problemPins)
  const target = hover ? objects.find((o) => o.id === hover.objectId) : undefined
  return (
    <>
      {target &&
        (target.type === 'panel' && hover?.gid ? (
          <ElementHover panel={target} gid={hover.gid} t={t} />
        ) : (
          <HoverRect id={target.id} {...boxOf(target, t, 2)} transform={spin(target, t)} />
        ))}
      {pins && <IssuePins objects={objects} t={t} />}
    </>
  )
}

function HoverRect({
  id,
  gid,
  ...rect
}: {
  id: string
  gid?: string
  x: number
  y: number
  width: number
  height: number
  transform?: string
}) {
  return (
    <rect
      data-issue-hover={id}
      data-issue-hover-gid={gid}
      {...rect}
      rx={2}
      fill="none"
      stroke="var(--color-sel)"
      strokeWidth={1.5}
      style={{ strokeOpacity: 'var(--sel-hover-opacity)' }}
      pointerEvents="none"
    />
  )
}

/**
 * 问题落在图内某个元素上（刻度、轴标题、图例……，`issueHover.gid`）：轮廓描**那个元素**，不是整张图。
 * 换算与图内编辑的 `OverlaySvg.ElementBoxes` 同一套：只认此刻显示着的精确 manifest
 * （`useDisplayedExactManifest`，权威不在就不按旧墨迹框猜）、`geomTarget`（位图落到宿主子图）、
 * `panelFullRect`（裁剪 / 旋转的内容坐标系），再整体套上面板内容的显示变换（`canvas/elementGeometry.elementOverlayTransform`，
 * 底下是 `lib/panelTransform`：先翻转、再旋转，绕包围盒中心——与 `PanelView` 同一份权威）。
 * 解不出来（manifest 没就位、gid 不在里面、是 `figure` 或已隐藏）退回整张图的轮廓（Codex #832）。
 */
function ElementHover({ panel, gid, t }: { panel: PanelObject; gid: string; t: ViewTransform }) {
  const manifest = useDisplayedExactManifest(panel)
  const el = manifest?.elements.find((e) => e.gid === gid)
  if (!manifest || !el || el.gid === 'figure' || isElementHidden(el)) {
    return <HoverRect id={panel.id} {...boxOf(panel, t, 2)} />
  }
  const target = geomTarget(manifest, el)
  const full = panelFullRect(panel)
  const [bx, by, bw, bh] = target.bbox
  const pad = 2
  const x = mmToViewX(full.x + bx * full.w, t)
  const y = mmToViewY(full.y + by * full.h, t)
  const pb = box(panel, t)
  return (
    <HoverRect
      id={panel.id}
      gid={target.gid}
      x={x - pad}
      y={y - pad}
      width={mmToPx(bw * full.w, t) + 2 * pad}
      height={mmToPx(bh * full.h, t) + 2 * pad}
      // 与面板内容同一个显示变换，取自图内编辑框同一个出处（`canvas/elementGeometry`）：先翻转、再旋转，
      // 都绕包围盒中心——只转不翻的话，水平 / 垂直翻转过的图上轮廓落在镜像位置（#832 / #833 评审）
      transform={elementOverlayTransform(panel, pb)}
    />
  )
}

const box = (o: { x: number; y: number; w: number; h: number }, t: ViewTransform) => ({
  x: mmToViewX(o.x, t),
  y: mmToViewY(o.y, t),
  w: mmToPx(o.w, t),
  h: mmToPx(o.h, t),
})

function boxOf(o: CanvasObject, t: ViewTransform, pad: number) {
  const b = box(o, t)
  return { x: b.x - pad, y: b.y - pad, width: b.w + 2 * pad, height: b.h + 2 * pad }
}

function spin(o: CanvasObject, t: ViewTransform): string | undefined {
  const rot = objectRotation(o)
  if (!rot) return undefined
  const b = box(o, t)
  return `rotate(${rot} ${b.x + b.w / 2} ${b.y + b.h / 2})`
}

const rank = (s: Severity) => SEVERITIES.indexOf(s)

/**
 * 标记圆心离对象右上角往外挪多少（px，两个方向各一份）。圆心压在角上时，选中对象的 ne 缩放手柄
 * （`OverlaySvg` 后画、7px）正好盖住标记中心——点标记变成拖缩放，标记也挡住了手柄。挪到
 * 角外 10px：圆（r=8）离角最近 ≈6px，手柄半对角 ≈5px，两者不相交，各自点得到。
 */
const PIN_OFFSET = 10

function IssuePins({ objects, t }: { objects: readonly CanvasObject[]; t: ViewTransform }) {
  const issues = useValidationStore((s) => s.issues)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  const byObject = useMemo(() => {
    const out = new Map<string, ValidationIssue[]>()
    for (const i of issues) {
      const id = i.objectRef.objectId
      if (!id || i.objectRef.canvasId !== canvasId) continue
      const list = out.get(id)
      if (list) list.push(i)
      else out.set(id, [i])
    }
    return out
  }, [issues, canvasId])

  return (
    <g data-issue-pins>
      {objects.map((o) => {
        const list = byObject.get(o.id)
        if (!list?.length) return null
        // 最要紧的那条：等级最高、清单里先出现的
        const worst = list.reduce((a, b) => (rank(b.severity) < rank(a.severity) ? b : a))
        // 底色与项数字色成对取（`issuePinColors`，对比度门禁逐对量）
        const pin = PIN_COLORS[worst.severity]
        // 屏幕上的右上角：按**转出来之后**的外接框（`visualBounds`）放，整组不跟着对象转——转 90° / 180° 的
        // 文字 / 形状上项数会侧过来 / 倒过来，标记也会跟着对象自己的局部角跑（Codex #832）。面板的 x/y/w/h
        // 本来就是旋转后的盒、翻转不改外框，同一个判据
        const b = box(visualBounds(o), t)
        const cx = b.x + b.w + PIN_OFFSET
        const cy = b.y - PIN_OFFSET
        const label = translate('problems.pinLabel', {
          ns: 'errors',
          count: list.length,
          severity: severityLabel(worst.severity),
        })
        const open = () => {
          const ui = useUiStore.getState()
          // 「当前图」= 点的这枚标记所在的那张图（`focusIssue` 随后就选中它），不是点之前选中 / 正在编辑的
          // 那张：拿旧的那张去比，`openProblemAt` 会判成「不是同一张」而开整份文档的清单（Codex #832）
          const figure = currentFigureOf([o.id], useDocumentStore.getState().doc.objects)
          const outcome = openProblemAt(worst, useValidationStore.getState().issues, figure.id)
          if (!outcome.ok) ui.setStatus(focusFailureMessage(outcome.reason), 'error')
        }
        return (
          <g
            key={o.id}
            data-issue-pin={o.id}
            data-issue-pin-severity={worst.severity}
            role="button"
            tabIndex={0}
            aria-label={label}
            style={{ pointerEvents: 'all', cursor: 'default' }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation()
              open()
            }}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' && e.key !== ' ') return
              e.preventDefault()
              e.stopPropagation()
              open()
            }}
          >
            <title>{label}</title>
            <circle cx={cx} cy={cy} r={8} fill={tokenVar(pin.fill)} stroke="var(--color-surface)" strokeWidth={1.5} />
            <text
              x={cx}
              y={cy}
              dy="0.35em"
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              fill={tokenVar(pin.text)}
              aria-hidden
              style={{ fontVariantNumeric: 'tabular-nums' }}
            >
              {list.length > 9 ? '9+' : list.length}
            </text>
          </g>
        )
      })}
    </g>
  )
}
