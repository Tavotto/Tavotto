import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, CircleAlert, Info, Lightbulb, X } from '@/components/ui/icons'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { useHintStore } from '@/lib/onboarding/hints'
import { t as translate, type UiMessage } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import { DURATION, usePresence } from '@/lib/motion'
import { formatMm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { boundsOf } from '@/lib/geometry'

/**
 * 底部不再有常驻状态栏。这里是两块按需出现的浮层：
 * - CanvasHud：坐标 / 选区尺寸，只在移动、缩放等交互进行中出现；
 *   工具提示只在非选择工具激活时出现。
 * - NotificationRail：状态 / 操作提示 / 「刚为编辑加入」三种通知同一条轨（二审 D1）；带 aria-live。
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

/** 一条通知的形态：图标 + 一句话 + 至多一个动作 / 关闭。三种来源共用（二审 D1） */
function Toast({
  tone,
  icon,
  text,
  action,
  onClose,
  closeLabel,
  state,
  ...rest
}: {
  tone: 'info' | 'error' | 'hint'
  icon: ReactNode
  text: ReactNode
  action?: { label: string; onClick: () => void }
  onClose?: () => void
  closeLabel?: string
  state: 'open' | 'closed'
} & Record<`data-${string}`, string | undefined>) {
  return (
    <div
      {...rest}
      data-state={state}
      className={cn(
        'pointer-events-auto flex max-w-[520px] items-center gap-2 rounded-md border px-3 py-1.5 text-xs shadow-pop',
        tone === 'error' ? 'border-danger/30 bg-danger-subtle text-danger' : 'border-border bg-surface text-ink-2',
        'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
      )}
    >
      {icon}
      <span className="min-w-0 flex-1">{text}</span>
      {action && (
        <Button variant="ghost" size="sm" className="-my-1 shrink-0 text-ink" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
      {onClose && (
        <button
          onClick={onClose}
          aria-label={closeLabel}
          className={cn(
            'flex h-5 w-5 shrink-0 items-center justify-center rounded-sm outline-none focus-visible:focus-ring',
            tone === 'error' ? 'hover:bg-danger/10' : 'text-ink-3 hover:bg-surface-hover hover:text-ink',
          )}
        >
          <X size={ICON_SIZE.sm} />
        </button>
      )}
    </div>
  )
}

/**
 * 通知轨：底部居中**一条**，最多两条叠着（新的在下、靠近底边；旧的顺延到上面），
 * 三种来源同一种盒子（2026-09-14 二审 D1）：
 *   - 状态（`uiStore.status`）：普通状态 4.5s 自己走，错误保留到用户关闭；
 *   - 操作提示（`useHintStore`）：可关、到时自己走；
 *   - 「刚为编辑加入本文档」：带「撤销」动作，加进来的那一次显示、离开快速编辑即消失。
 * 此前三者各占一条轨（状态居中、提示右下、加入说明常驻在上下文栏第二行）——那行常驻说明
 * 存在的理由正是「toast 只有一个槽位、会被『渲染完成』盖掉」。HUD 留在左下：它是读数不是消息。
 * aria-live 的契约不变：状态区 `data-status-live`（e2e 认它，不认 role=status）、错误 assertive；
 * 提示区只有 aria-live 没有 role（`role=status` 全产品只留状态区那一个）。
 */
export function NotificationRail() {
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
  const statusPresence = usePresence(!!status, DURATION.exit)

  const hint = useHintStore((s) => s.current)
  const hintToken = useHintStore((s) => s.token)
  const dismissHint = useHintStore((s) => s.dismiss)
  const hintPresence = usePresence(!!hint, DURATION.exit)
  const hintText = hint ? t(`hints.${hint}`) : ''

  // 「编辑原图」这一次把图加进了文档（此前不在）：说出口，并给撤销；回排版 / 撤销即消失
  const justAdded = useWorkspaceStore(
    (s) => s.addedForEdit !== null && s.addedForEdit === s.activePanelId,
  )
  const addedPresence = usePresence(justAdded, DURATION.exit)
  // 「移除」= 撤销加入那一步；只在撤销栈还停在加入那一刻时给（之后再改了别的，撤销撤的就是
  // 别的了）。名字用「移除」不用「撤销」：顶栏已有一颗「撤销」，同名两颗读屏与用例都分不清
  const addedDepth = useWorkspaceStore((s) => s.addedForEditDepth)
  const historyDepth = useDocumentStore((s) => s.past.length)
  const canRemoveAdded = justAdded && historyDepth === addedDepth

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-20 flex flex-col items-center gap-1.5 px-4">
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
      <div aria-live="polite" className="sr-only">
        {hintText}
      </div>
      {/* 顺序 = 出现的先后：加入说明最早、提示其次、刚说的状态最靠近底边 */}
      {addedPresence.mounted && (
        <Toast
          tone="info"
          state={addedPresence.state}
          data-fast-edit-added-note=""
          icon={<Info size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={t('fastEdit.addedForEdit')}
          action={canRemoveAdded ? { label: t('fastEdit.removeAdded'), onClick: () => runUndoRedo(false) } : undefined}
        />
      )}
      {hintPresence.mounted && hint && (
        <Toast
          key={hintToken}
          tone="hint"
          state={hintPresence.state}
          data-onboarding-hint={hint}
          icon={<Lightbulb size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={hintText}
          onClose={dismissHint}
          closeLabel={translate('actions.close')}
        />
      )}
      {statusPresence.mounted && (
        <Toast
          tone={shown.tone}
          state={statusPresence.state}
          icon={
            shown.tone === 'error' ? (
              <CircleAlert size={ICON_SIZE.sm} className="shrink-0" aria-hidden />
            ) : (
              <Check size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
            )
          }
          text={shownText}
          onClose={shown.tone === 'error' ? () => useUiStore.getState().setStatus(null) : undefined}
          closeLabel={t('status.dismissError')}
        />
      )}
    </div>
  )
}
