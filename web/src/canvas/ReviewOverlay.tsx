import { useMemo } from 'react'
import { t as translate } from '@/i18n'
import { innerGid, owningPanel } from '@/lib/reviewGids'
import { useReviewPlacements, type PlacedComment } from '@/components/review/useReviewPlacements'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToPx, mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { CanvasObject, PanelObject } from '@/types/document'
import { useElementRect } from './IssueOverlay'

/**
 * 审阅意见在画布上的圆点（ADR 0187 §五、§八）：**只在右栏「审阅」页签打开时画**。
 * 元素意见 = 圆点跟着元素现在的位置走（元素 `gid` 还在）；区域意见（最新版留的）= 按画面比例坐标落在图上；
 * 元素没了 / 旧版本的区域意见不在这里画（列表里用该版本快照标原位置）。点圆点 = 选中那条（列表滚到它），
 * 选中的元素描一道边。圆点不吃拖动：只拦 pointerdown，不开手势。
 */
export function ReviewOverlay({ objects, t }: { objects: readonly CanvasObject[]; t: ViewTransform }) {
  const tab = useUiStore((s) => s.rightTab)
  const open = useUiStore((s) => s.rightOpen)
  const placed = useReviewPlacements()
  const hasShare = useReviewStore((s) => s.share !== null)
  if (!open || tab !== 'review' || !hasShare) return null
  return (
    <g data-review-pins>
      {placed
        .filter((p) => p.placement === 'element' || p.placement === 'region')
        .map((p) => (
          <ReviewPin key={p.comment.id} p={p} objects={objects} t={t} />
        ))}
    </g>
  )
}

const R = 8

function ReviewPin({ p, objects, t }: { p: PlacedComment; objects: readonly CanvasObject[]; t: ViewTransform }) {
  const a = p.comment.anchor
  const render = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const selected = useReviewStore((s) => s.selectedId === p.comment.id)
  const mode = useWorkspaceStore((s) => s.mode)
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const page = useDocumentStore((s) => s.doc.page)
  const detail = useReviewStore((s) => s.detail)
  const owner = useMemo(
    () => (a.kind === 'element' ? owningPanel(objects, { byKey: render, latest }, a.gid) : null),
    [a, objects, render, latest],
  )
  // 元素意见：图内元素的框（同 `IssueOverlay` 的换算）；元素就是面板本身时用面板框
  const elRect = useElementRect((owner ?? FALLBACK) as PanelObject, a.kind === 'element' ? innerGid(a.gid) : '', t, 2)
  let rect: { x: number; y: number; width: number; height: number } | null = null
  if (a.kind === 'element' && owner) {
    const panelRect = { x: mmToViewX(owner.x, t), y: mmToViewY(owner.y, t), width: mmToPx(owner.w, t), height: mmToPx(owner.h, t) }
    // 上传的元素框已夹进画面，而 `useElementRect` 用的是当前 manifest 的未裁剪框（可能远大于图幅）：
    // 先与面板的可见框求交，圆点的相对位置才和上传时的框对得上；交集为空就退回面板框
    const visible = elRect && owner.id !== a.gid ? intersectRect(elRect, panelRect) : null
    rect = visible ?? panelRect
  } else if (a.kind === 'region') {
    // 区域的画面比例坐标：排版相对**页面**（快照就是导出 scope=canvas 的页面图），单图相对整张图；版本记录带 `frame` 时以它为准
    const frame = detail?.versions.find((v) => v.v === p.comment.v)?.frame ?? (mode === 'fast_edit' ? 'figure' : 'page')
    const base =
      frame === 'figure'
        ? (objects.find((o) => o.id === activePanelId) ?? null)
        : { x: 0, y: 0, w: page.w, h: page.h }
    if (base) {
      const [bx, by, bw, bh] = a.box
      rect = {
        x: mmToViewX(base.x + bx * base.w, t),
        y: mmToViewY(base.y + by * base.h, t),
        width: mmToPx(bw * base.w, t),
        height: mmToPx(bh * base.h, t),
      }
    }
  }
  if (!rect) return null
  // 元素意见带点击位置时，圆点落在元素上被点的那一处：按它在当时元素框里的相对位置换到元素现在的框（元素挪了也跟着走）
  const at = a.kind === 'element' && a.pt && owner && owner.id !== a.gid ? pinOnElement(a.box, a.pt) : null
  const cx = at ? rect.x + at[0] * rect.width : rect.x + rect.width
  const cy = at ? rect.y + at[1] * rect.height : rect.y
  const label = translate('review.comment.pin', { ns: 'dialogs', n: p.n })
  const pick = () => useReviewStore.getState().select(p.comment.id)
  const done = p.comment.resolved
  return (
    <g data-review-pin={p.comment.id} data-review-pin-n={p.n}>
      {selected && (
        <rect
          data-review-outline={p.comment.id}
          x={rect.x}
          y={rect.y}
          width={rect.width}
          height={rect.height}
          rx={2}
          fill="none"
          stroke="var(--color-sel)"
          strokeWidth={1.5}
          pointerEvents="none"
        />
      )}
      <g
        role="button"
        tabIndex={0}
        aria-label={label}
        style={{ pointerEvents: 'all', cursor: 'default' }}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          pick()
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return
          e.preventDefault()
          e.stopPropagation()
          pick()
        }}
      >
        <title>{label}</title>
        <circle
          cx={cx}
          cy={cy}
          r={R}
          fill={done ? 'var(--color-ink-3)' : 'var(--color-sel)'}
          stroke="var(--color-surface)"
          strokeWidth={selected ? 2.5 : 1.5}
        />
        <text x={cx} y={cy} dy="0.35em" textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--color-surface)" aria-hidden>
          {p.n > 9 ? '9+' : p.n}
        </text>
      </g>
    </g>
  )
}

type ViewRect = { x: number; y: number; width: number; height: number }

/** 两个屏幕框的交集（只取 x / y / width / height）；没有面积时 null */
export function intersectRect(r: ViewRect, frame: ViewRect): ViewRect | null {
  const x0 = Math.max(r.x, frame.x)
  const y0 = Math.max(r.y, frame.y)
  const x1 = Math.min(r.x + r.width, frame.x + frame.width)
  const y1 = Math.min(r.y + r.height, frame.y + frame.height)
  if (!(x1 > x0 && y1 > y0)) return null
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}

/** 点击位置在元素框里的相对位置（0..1）；框退化时 null（退回角上） */
export function pinOnElement(box: readonly number[], pt: readonly number[]): [number, number] | null {
  const [bx, by, bw, bh] = box
  if (!(bw > 0 && bh > 0)) return null
  const u = (pt[0] - bx) / bw
  const v = (pt[1] - by) / bh
  return [Math.min(1, Math.max(0, u)), Math.min(1, Math.max(0, v))]
}

/** `useElementRect` 是 hook、不能有条件地调：没有所属面板时喂一个占位（gid 为空，它返回 null） */
const FALLBACK = { id: '', type: 'panel', x: 0, y: 0, w: 0, h: 0, fileId: '', overrides: [] } as unknown as PanelObject
