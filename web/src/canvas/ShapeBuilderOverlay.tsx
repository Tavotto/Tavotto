import { useEffect } from 'react'
import { subpathD } from '@/lib/pathGeom'
import { useDocumentStore } from '@/store/documentStore'
import {
  beginBuilderGesture,
  cancelShapeBuilder,
  endBuilderGesture,
  extendBuilderGesture,
  finishShapeBuilder,
  setBuilderHover,
  useShapeBuilderStore,
} from '@/store/shapeBuilder'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToViewX, mmToViewY, type ViewTransform } from '@/store/viewportStore'

/**
 * 形状生成器（⇧M）的覆盖层：把会话里的区域画成一块块可点的形状（页面 mm → 屏幕）。悬停的那块淡淡铺一层选区色；
 * 拖的这一下经过的区域：合并模式铺选区色、删除模式（⌥）铺危险色。区域互不重叠，按下 / 经过只认最上面那块。
 * 选区或工具变了 → 收尾退出（`finishShapeBuilder`：有改动才落一条历史）；原对象在别处被改了（撤销等）→ 不落地退出。
 */

const SEL = 'var(--color-sel)'

export function ShapeBuilderOverlay({ t }: { t: ViewTransform }) {
  const st = useShapeBuilderStore()
  const ids = useSelectionStore((s) => s.ids)
  const tool = useUiStore((s) => s.tool)
  const objects = useDocumentStore((s) => s.doc.objects)

  // 进入时的那组对象一旦不再是选区 / 换了工具 / 原对象在别处被改了，就收尾退出
  useEffect(() => {
    if (!st.active) return
    const want = new Set(st.sources.map((s) => s.id))
    const sameSel = ids.length === want.size && ids.every((id) => want.has(id))
    const intact = st.originals.every((s) => objects.find((o) => o.id === s.id) === s)
    if (!intact) cancelShapeBuilder()
    else if (!sameSel || tool !== 'select') finishShapeBuilder()
  }, [st.active, st.sources, st.originals, ids, tool, objects])

  // 松手在哪儿都算这一下结束（指针可能已经离开区域）
  useEffect(() => {
    if (!st.gesture) return
    const up = () => void endBuilderGesture()
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    return () => {
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
  }, [st.gesture])

  if (!st.active) return null
  const v = (q: readonly [number, number]): [number, number] => [mmToViewX(q[0], t), mmToViewY(q[1], t)]
  const picked = new Set(st.gesture?.picked ?? [])
  return (
    <g data-shape-builder="" data-faces={st.faces.length}>
      {st.faces.map((f, i) => {
        const d = f.subs.map((sp) => subpathD(sp, v)).join(' ')
        const on = picked.has(i)
        const fill = on ? (st.gesture?.mode === 'delete' ? 'var(--color-danger)' : SEL) : i === st.hover ? SEL : 'transparent'
        return (
          <path
            key={i}
            data-builder-face={i}
            data-picked={on || undefined}
            d={d}
            fillRule="evenodd"
            fill={fill}
            fillOpacity={on ? 0.35 : 0.18}
            stroke={SEL}
            strokeWidth={1}
            strokeDasharray={i === st.hover || on ? undefined : '3 3'}
            style={{ pointerEvents: 'all', cursor: st.gesture?.mode === 'delete' ? 'not-allowed' : 'crosshair' }}
            onPointerDown={(e) => {
              if (e.button !== 0) return
              e.stopPropagation()
              e.preventDefault()
              beginBuilderGesture(i, e.altKey)
            }}
            onPointerEnter={() => {
              setBuilderHover(i)
              extendBuilderGesture(i)
            }}
            onPointerLeave={() => setBuilderHover(null)}
          />
        )
      })}
    </g>
  )
}
