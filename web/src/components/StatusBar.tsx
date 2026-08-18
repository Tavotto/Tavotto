import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import type { UiMessage } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import { DURATION, usePresence } from '@/lib/motion'
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
  const { t } = useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const cursor = useInteractionStore((s) => s.cursor)
  const tool = useUiStore((s) => s.tool)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)

  const interacting = GEOMETRY_KINDS.has(kind)
  const hint = tool !== 'select' ? t(`toolHint.${tool}`) : null
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
  const { t } = useTranslation('workspace')
  const fmt = useFormatMessage()
  const status = useUiStore((s) => s.status)
  const tone = useUiStore((s) => s.statusTone)
  // 退场那 90ms 里 status 已经是 null 了，得把最后一条文案留着播完，
  // 否则会看到一个空壳滑下去。留的是**描述符**，不是翻好的字符串：
  // 退场期间切语言也跟着换。
  const last = useRef<{ status: UiMessage | null; tone: typeof tone }>({
    status: null,
    tone: 'info',
  })
  useEffect(() => {
    if (status) last.current = { status, tone }
  }, [status, tone])
  const shown = status ? { status, tone } : last.current
  const shownText = fmt(shown.status)
  const liveText = fmt(status)
  const { mounted, state } = usePresence(!!status, DURATION.exit)

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-20 flex justify-center px-4">
      {/* aria-live 常驻在 DOM 里，读屏器才能捕捉内容变化 */}
      <div aria-live="polite" role="status" className="sr-only">
        {tone === 'info' ? liveText : ''}
      </div>
      <div aria-live="assertive" role="alert" className="sr-only">
        {tone === 'error' ? liveText : ''}
      </div>
      {mounted && (
        <div
          data-state={state}
          className={cn(
            'pointer-events-auto flex max-w-[520px] items-center gap-2 rounded-md border px-3 py-1.5 text-xs shadow-pop',
            shown.tone === 'error'
              ? 'border-danger/30 bg-danger-subtle text-danger'
              : 'border-border bg-surface text-ink-2',
            'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
          )}
        >
          <span className="min-w-0 flex-1">{shownText}</span>
          {shown.tone === 'error' && (
            <button
              onClick={() => useUiStore.getState().setStatus(null)}
              aria-label={t('status.dismissError')}
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
