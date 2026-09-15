import { forwardRef, type ReactNode } from 'react'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
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
 * 骨架固定两行脚注，**什么语言、多长的图名都不折行**（审计 A06 / A07：以前
 * 进度、返回、跳过与「打开 Fig2_correlation」挤在同一行，「第 2 步，共 8 步」被
 * 折成三行）：
 *
 * ```text
 * 第 n 步，共 N 步                     返回  跳过此步     ← 进度 + 两个辅助动作
 *                                   [次动作] [主动作]     ← 只在有动作时出现
 * ```
 *
 * 视觉：浮层用唯一的轻投影 `shadow-pop`、10px 圆角；进场 `animate-pop-in`
 * （reduced motion 下 index.css 的全局覆盖把它压到 0.01ms）。
 */
export interface CoachmarkProps {
  id: string
  title: string
  /** 正文：这一步做什么。前置条件没满足时层会把它换成「先做什么」（见 OnboardingLayer） */
  body: ReactNode
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
        'pointer-events-auto w-[300px] max-w-[calc(100vw-1rem)] rounded-md bg-surface p-3 text-ink shadow-pop outline-none',
        'animate-pop-in',
        className,
      )}
    >
      {/* 指向锚点的小箭头：用形状而不只靠位置说明「我在说它」 */}
      {side !== 'center' && (
        <span
          aria-hidden
          // 箭头与卡片同一副皮：只有 bg-surface（宪法第一节，浮层不画实边）。此前卡片是
          // shadow-pop 的 8% 环、箭头却画 12% 实边，箭头比它指的那张卡还重（打磨 G2）
          className={cn(
            'absolute h-2.5 w-2.5 rotate-45 bg-surface',
            side === 'bottom' && '-top-[5px] left-4',
            side === 'top' && '-bottom-[5px] left-4',
            side === 'right' && '-left-[5px] top-4',
            side === 'left' && '-right-[5px] top-4',
          )}
        />
      )}
      <div className="min-w-0 pr-6">
        {/* 13px 不在六个角色里（宪法第六节）：标题是 type-title 15（打磨 G1） */}
        <h2 id={titleId} className="type-title">
          {title}
        </h2>
        <p id={bodyId} className="mt-1 text-xs leading-relaxed text-ink-2">
          {body}
        </p>
        {note && <div className="mt-1.5 text-xs leading-relaxed text-ink-3">{note}</div>}
      </div>
      {(progress || onBack || onSkip) && (
        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="min-w-0 truncate text-xs tabular-nums text-ink-3" data-onboarding-progress>
            {progress}
          </span>
          <span className="flex shrink-0 items-center gap-0.5">
            {onBack && (
              <Button size="md" variant="ghost" onClick={onBack} data-onboarding-back>
                {ob('back')}
              </Button>
            )}
            {onSkip && (
              <Button size="md" variant="ghost" onClick={onSkip} data-onboarding-skip>
                {ob('skipStep')}
              </Button>
            )}
          </span>
        </div>
      )}
      {(primary || secondary) && (
        <div className={cn('flex items-center justify-end gap-2', progress || onBack || onSkip ? 'mt-2' : 'mt-3')}>
          {secondary && (
            <Button size="md" variant="secondary" onClick={secondary.onClick} data-onboarding-secondary>
              {secondary.label}
            </Button>
          )}
          {primary && (
            <Button
              size="md"
              variant="primary"
              onClick={primary.onClick}
              autoFocus={primary.autoFocus}
              data-onboarding-primary
            >
              {primary.label}
            </Button>
          )}
        </div>
      )}
      {/* 关闭（暂停）画在右上角，但放在 DOM 末尾：Tab 顺序是返回 → 跳过 → 主动作 → 关闭 */}
      {/* 20px 行内小钮是原语的一档（`size="icon-xs"`，宪法第十三节）：此前这里手写 24×24 */}
      <Button
        size="icon-xs"
        onClick={onClose}
        aria-label={ob('pause')}
        title={ob('pause')}
        className="absolute right-2 top-2 text-ink-3 hover:text-ink"
      >
        <X size={ICON_SIZE.sm} />
      </Button>
    </div>
  )
})
