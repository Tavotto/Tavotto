import { useMemo } from 'react'
import { t as translate } from '@/i18n'
import { openProblemAt, focusFailureMessage } from '@/lib/issueFocus'
import { currentFigureOf } from '@/lib/problemContext'
import { SEVERITIES, type Severity } from '@/lib/profile'
import type { ValidationIssue } from '@/lib/validation'
import { severityLabel } from '@/lib/validationText'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { objectRotation, type CanvasObject } from '@/types/document'

/**
 * 问题面板在画布上的两样东西（2026-10-07 设计审计 §9.4），从 `OverlaySvg` 里挂进来、自己一个文件——
 * 覆盖层的其余语法（选择框、手柄、参考线）归外壳，这里只画「问题」：
 *
 * * **悬停轮廓**：问题面板里指着一行（`uiStore.issueHover`），画布上那个对象画一道与画布自己的
 *   hover 预示同一种画法的轮廓（sel 色、`--sel-hover-opacity`）。只是「我在看它」：不选中、不定位。
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
      {target && (
        <rect
          data-issue-hover={target.id}
          {...boxOf(target, t, 2)}
          transform={spin(target, t)}
          rx={2}
          fill="none"
          stroke="var(--color-sel)"
          strokeWidth={1.5}
          style={{ strokeOpacity: 'var(--sel-hover-opacity)' }}
          pointerEvents="none"
        />
      )}
      {pins && <IssuePins objects={objects} t={t} />}
    </>
  )
}

const box = (o: CanvasObject, t: ViewTransform) => ({
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

/** 标记的底色：等级锚点（非文字 ≥3:1）；查不了 / 建议是 ink-3 */
const PIN_FILL: Record<Severity, string> = {
  error: 'var(--color-danger)',
  warn: 'var(--color-warn)',
  not_verifiable: 'var(--color-ink-3)',
  suggestion: 'var(--color-ink-3)',
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
        const b = box(o, t)
        const cx = b.x + b.w + PIN_OFFSET
        const cy = b.y - PIN_OFFSET
        const label = translate('problems.pinLabel', {
          ns: 'errors',
          count: list.length,
          severity: severityLabel(worst.severity),
        })
        const open = () => {
          const ui = useUiStore.getState()
          const figure = currentFigureOf(
            [useWorkspaceStore.getState().activePanelId, ui.elementPanelId, useSelectionStore.getState().ids.at(-1) ?? null],
            useDocumentStore.getState().doc.objects,
          )
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
            transform={spin(o, t)}
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
            <circle cx={cx} cy={cy} r={8} fill={PIN_FILL[worst.severity]} stroke="var(--color-surface)" strokeWidth={1.5} />
            <text
              x={cx}
              y={cy}
              dy="0.35em"
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              fill="var(--color-surface)"
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
