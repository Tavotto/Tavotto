import { X } from 'lucide-react'
import { formatMm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { boundsOf } from '@/lib/geometry'

/**
 * 底部不再有常驻状态栏。这里是两块按需出现的浮层：
 * - CanvasHud：坐标 / 选区尺寸，只在移动、缩放等交互进行中出现；
 *   工具提示只在非选择工具激活时出现。
 * - StatusToasts：普通状态短暂即逝，错误保留到用户关闭；带 aria-live。
 * 两者都挂在画布列内部，不占布局高度。
 */

/** 交互进行中才值得显示实时几何数字的拖动类型 */
const GEOMETRY_KINDS = new Set(['move', 'resize', 'draw', 'crop', 'endpoint', 'element', 'guide'])

export function CanvasHud() {
  const kind = useInteractionStore((s) => s.kind)
  const cursor = useInteractionStore((s) => s.cursor)
  const tool = useUiStore((s) => s.tool)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)

  const interacting = GEOMETRY_KINDS.has(kind)
  const hint = tool !== 'select' ? TOOL_HINT[tool] : null
  if (!interacting && !hint) return null

  const selected = objects.filter((o) => ids.includes(o.id))
  const bounds = selected.length > 0 ? boundsOf(selected) : null

  return (
    <div
      className="pointer-events-none absolute bottom-2 left-2 z-10 flex flex-col items-start gap-1"
      aria-hidden={interacting ? undefined : true}
    >
      {interacting && (
        <div className="flex items-center gap-2 rounded-sm border border-border bg-surface px-2 py-1 font-mono text-xs tabular-nums text-ink-2">
          {cursor && (
            <span>
              {formatMm(cursor.x)}, {formatMm(cursor.y)} mm
            </span>
          )}
          {bounds && (
            <span className="text-ink-3">
              {formatMm(bounds.w)}×{formatMm(bounds.h)} mm
            </span>
          )}
        </div>
      )}
      {!interacting && hint && (
        <p className="rounded-sm border border-border bg-surface px-2 py-1 text-xs text-ink-2">
          {hint}
        </p>
      )}
    </div>
  )
}

export function StatusToasts() {
  const status = useUiStore((s) => s.status)
  const tone = useUiStore((s) => s.statusTone)

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-20 flex justify-center px-4">
      {/* aria-live 常驻在 DOM 里，读屏器才能捕捉内容变化 */}
      <div aria-live="polite" role="status" className="sr-only">
        {tone === 'info' ? status : ''}
      </div>
      <div aria-live="assertive" role="alert" className="sr-only">
        {tone === 'error' ? status : ''}
      </div>
      {status && (
        <div
          className={cn(
            'pointer-events-auto flex max-w-[520px] items-center gap-2 rounded-md border px-3 py-1.5 text-xs shadow-pop',
            tone === 'error'
              ? 'border-danger/30 bg-danger-subtle text-danger'
              : 'border-border bg-surface text-ink-2',
          )}
        >
          <span className="min-w-0 flex-1">{status}</span>
          {tone === 'error' && (
            <button
              onClick={() => useUiStore.getState().setStatus('')}
              aria-label="关闭错误提示"
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm hover:bg-danger/10"
            >
              <X size={12} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

const TOOL_HINT: Record<string, string> = {
  text: '在画布上拖出文字框，或单击放置默认大小；Esc 取消',
  arrow: '拖动画出箭头，Shift 吸附 15° 角；Esc 取消',
  rect: '拖动画出矩形；Esc 取消',
  ellipse: '拖动画出椭圆；Esc 取消',
  line: '拖动画出直线；Esc 取消',
}
