import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, CircleAlert, X } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate, type UiMessage } from '@/i18n'
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

/** 以尺寸为主读数的交互：改的是大小，坐标退为次要；其余（移动等）反之 */
const SIZE_FIRST_KINDS = new Set(['resize', 'draw', 'crop'])

/**
 * 读数盒与工具提示共用的一只盒子：inline-flex 可换行、最小高 28px、
 * 内边距 5/10px，颜色全部走现有 token，不加阴影与强调色。
 */
const HUD_BOX =
  'inline-flex min-h-7 max-w-full flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-sm border border-border bg-surface px-2.5 py-1.25 text-sm'

export function CanvasHud() {
  const { t } = useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const cursor = useInteractionStore((s) => s.cursor)
  const tool = useUiStore((s) => s.tool)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)

  const interacting = GEOMETRY_KINDS.has(kind)
  const hint = !interacting && tool !== 'select' ? t(`toolHint.${tool}`) : null

  const selected = objects.filter((o) => ids.includes(o.id))
  const bounds = selected.length > 0 ? boundsOf(selected) : null
  // 交互中但既没有光标也没有选区时什么都不画——不留一只空边框盒子
  const showReadings = interacting && (!!cursor || !!bounds)
  if (!showReadings && !hint) return null

  const sizeFirst = SIZE_FIRST_KINDS.has(kind)

  return (
    <div
      className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-col items-start gap-1"
      aria-hidden={interacting ? undefined : true}
    >
      {showReadings && (
        <div className={cn(HUD_BOX, 'tabular-nums')} data-hud-mode={sizeFirst ? 'size' : 'cursor'}>
          {cursor && (
            <span className="inline-flex items-baseline gap-1.75 whitespace-nowrap">
              <span className="text-xs text-ink-3">{t('hud.cursor')}</span>
              {/* 固定最小宽度 + 等宽数字：拖动时数字变长变短，盒子不跟着跳 */}
              <span
                className={cn(
                  'inline-block min-w-[13ch]',
                  sizeFirst ? 'text-ink-3' : 'font-medium text-ink',
                )}
              >
                {translate('measure.mmPair', { a: formatMm(cursor.x), b: formatMm(cursor.y) })}
              </span>
            </span>
          )}
          {bounds && (
            <span
              className={cn(
                'inline-flex items-baseline gap-1.75 whitespace-nowrap',
                cursor && 'border-l border-border pl-2.5',
              )}
            >
              <span className="text-xs text-ink-3">{t('hud.size')}</span>
              <span
                className={cn(
                  'inline-block min-w-[12ch]',
                  sizeFirst ? 'font-medium text-ink' : 'text-ink-3',
                )}
              >
                {translate('measure.mmSize', { w: formatMm(bounds.w), h: formatMm(bounds.h) })}
              </span>
            </span>
          )}
        </div>
      )}
      {hint && <p className={cn(HUD_BOX, 'text-ink-2')}>{hint}</p>}
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
      {/* aria-live 常驻在 DOM 里，读屏器才能捕捉内容变化。
          `data-status-live` 是这块播报区的**稳定机器标识**：`role="status"` 全产品有十几个
          产出点（快速编辑那行常驻说明、素材库、导出面板、问题面板……），所以
          `[role="status"]` 取第一个拿到的是「文档里排在最前的那个 status」，不是
          「应用刚说了什么」。要问后者的（e2e）一律认这个属性，见 `web/AGENTS.md`。 */}
      <div aria-live="polite" role="status" data-status-live className="sr-only">
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
          {shown.tone === 'error' ? (
            <CircleAlert size={ICON_SIZE.sm} className="shrink-0" aria-hidden />
          ) : (
            <Check size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
          )}
          <span className="min-w-0 flex-1">{shownText}</span>
          {shown.tone === 'error' && (
            <button
              onClick={() => useUiStore.getState().setStatus(null)}
              aria-label={t('status.dismissError')}
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm hover:bg-danger/10"
            >
              <X size={ICON_SIZE.sm} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}
