import { forwardRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import type { CoachmarkSide } from '@/lib/onboarding/position'
import { Button } from '../ui/Button'

/**
 * coachmark 的**外壳**：标题、一两句话、进度、返回 / 跳过 / 关闭。纯展示，
 * 不知道自己在教哪一步——挂哪、说什么、按钮做什么全由 `OnboardingLayer` 传进来。
 *
 * 可访问性：
 *   * `role="dialog"` + `aria-modal="false"`：它是一块非模态的说明，不困住焦点；
 *   * 标题 / 正文经 `aria-labelledby` / `aria-describedby` 关联，`aria-live`
 *     区在层里（换步骤时读一次「第几步、目标、进度」）；
 *   * Tab 顺序：返回 → 跳过 → 主动作 → 关闭；Esc 由层处理（暂停）。
 *
 * 视觉：浮层用唯一的轻投影 `shadow-pop`、10px 圆角；进场 `animate-pop-in`
 * （reduced motion 下 index.css 的全局覆盖把它压到 0.01ms）。
 */
export interface CoachmarkProps {
  id: string
  title: string
  body: string
  /** 「第 n 步，共 N 步」；欢迎 / 完成页不显示 */
  progress?: string | null
  side?: CoachmarkSide | 'center'
  /** 主动作（欢迎页的「开始」、完成页的两颗、Step 4 的「已解决，继续」） */
  primary?: { label: string; onClick: () => void; autoFocus?: boolean } | null
  secondary?: { label: string; onClick: () => void } | null
  onBack?: (() => void) | null
  onSkip?: (() => void) | null
  onClose: () => void
  /** 目标暂时找不到时的提示行 */
  note?: ReactNode
  style?: React.CSSProperties
  className?: string
  onKeyDown?: (e: React.KeyboardEvent) => void
}

const ob = (key: string, values?: Record<string, unknown>) =>
  translate(`onboarding.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export const Coachmark = forwardRef<HTMLDivElement, CoachmarkProps>(function Coachmark(
  {
    id,
    title,
    body,
    progress,
    side = 'bottom',
    primary,
    secondary,
    onBack,
    onSkip,
    onClose,
    note,
    style,
    className,
    onKeyDown,
  },
  ref,
) {
  const titleId = `${id}-title`
  const bodyId = `${id}-body`
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      data-onboarding-coachmark
      data-side={side}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      // 指针事件不许漏到画布上：coachmark 上的一次点击不该顺手选中它身后的对象
      onPointerDown={(e) => e.stopPropagation()}
      style={style}
      className={cn(
        'pointer-events-auto w-[300px] max-w-[calc(100vw-1rem)] rounded-[10px] border border-border bg-surface p-3 text-ink shadow-pop outline-none',
        'animate-pop-in',
        className,
      )}
    >
      {/* 指向锚点的小箭头：用形状而不只靠位置说明「我在说它」 */}
      {side !== 'center' && (
        <span
          aria-hidden
          className={cn(
            'absolute h-2.5 w-2.5 rotate-45 border-border bg-surface',
            side === 'bottom' && '-top-[6px] left-4 border-l border-t',
            side === 'top' && '-bottom-[6px] left-4 border-b border-r',
            side === 'right' && '-left-[6px] top-4 border-b border-l',
            side === 'left' && '-right-[6px] top-4 border-r border-t',
          )}
        />
      )}
      <div className="min-w-0 pr-6">
        <h2 id={titleId} className="text-[13px] font-medium leading-5 text-ink">
          {title}
        </h2>
        <p id={bodyId} className="mt-1 text-xs leading-relaxed text-ink-2">
          {body}
        </p>
        {note && <div className="mt-1.5 text-xs leading-relaxed text-ink-3">{note}</div>}
      </div>
      <div className="mt-2.5 flex items-center gap-1">
        {progress && (
          <span className="font-mono text-[11px] text-ink-3" data-onboarding-progress>
            {progress}
          </span>
        )}
        <span className="flex-1" />
        {onBack && (
          <Button size="sm" variant="ghost" onClick={onBack} data-onboarding-back>
            {ob('back')}
          </Button>
        )}
        {onSkip && (
          <Button size="sm" variant="ghost" onClick={onSkip} data-onboarding-skip>
            {ob('skipStep')}
          </Button>
        )}
        {secondary && (
          <Button size="sm" variant="outline" onClick={secondary.onClick} data-onboarding-secondary>
            {secondary.label}
          </Button>
        )}
        {primary && (
          <Button
            size="sm"
            variant="primary"
            onClick={primary.onClick}
            autoFocus={primary.autoFocus}
            data-onboarding-primary
          >
            {primary.label}
          </Button>
        )}
      </div>
      {/* 关闭（暂停）画在右上角，但放在 DOM 末尾：Tab 顺序是返回 → 跳过 → 主动作 → 关闭 */}
      <button
        type="button"
        onClick={onClose}
        aria-label={ob('pause')}
        title={ob('pause')}
        className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-sm text-ink-3 outline-none hover:bg-ink/[.055] hover:text-ink focus-visible:focus-ring"
      >
        <X size={13} />
      </button>
    </div>
  )
})
