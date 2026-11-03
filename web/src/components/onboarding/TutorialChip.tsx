import { useTranslation } from 'react-i18next'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { runTutorialEntry } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'

/**
 * 顶栏右侧的「新手教程」状态胶囊（2026-10-30 用户裁决，ADR 0136 修订）。
 *
 * 新手态（`not_started`、`welcomeSeenAt` 空）的用户启动时直接进了项目（Codex 插件开图、拖脚本启动），
 * `useAutoOpen` 不弹整页欢迎，这里给一颗安静的胶囊：点它进教程（与帮助菜单同一个入口 `runTutorialEntry`），
 * 点右边的 × 关掉。两者都记下 `welcomeSeenAt`，以后不再出现。**不进文档流**，只淡入（与 `BuildMismatchChip` 同款）；
 * 教程层开着时不画（底下 inert）。窄于 900 只留圆点（短语给读屏）。
 * 更多菜单里的教程入口与它**不同时出现**（2026-10 审计 A2）：胶囊在时菜单不放，胶囊消失后菜单里才有——判据就是 `useTutorialChipVisible`。
 */
/** 胶囊此刻是否在显示：顶栏「⋯」菜单据此不重复列「开始教程」（判据只此一处） */
export function useTutorialChipVisible(): boolean {
  const pending = useOnboardingStore((s) => s.status === 'not_started' && s.welcomeSeenAt === null)
  const surface = useOnboardingStore((s) => s.surface)
  return pending && surface === 'none'
}

export function TutorialChip() {
  const { t } = useTranslation(['workspace', 'common'])
  const visible = useTutorialChipVisible()
  if (!visible) return null
  const label = t('workspace:tutorialChip.label')
  return (
    <span
      data-tutorial-chip=""
      className="inline-flex h-6 min-w-0 shrink-0 animate-fade-in items-center rounded-full bg-surface text-xs text-ink-2 inset-ring inset-ring-border"
    >
      <button
        type="button"
        data-tutorial-chip-open=""
        aria-label={label}
        onClick={() => void runTutorialEntry('canvas')}
        className={cn(
          'inline-flex h-6 min-w-0 items-center gap-1.5 rounded-full pl-2.5 pr-1 outline-none',
          'hover:bg-surface-hover focus-visible:focus-ring',
          'max-[899px]:w-6 max-[899px]:justify-center max-[899px]:px-0',
        )}
      >
        <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-accent" />
        <span className="truncate max-[899px]:sr-only">{label}</span>
      </button>
      <button
        type="button"
        data-tutorial-chip-dismiss=""
        aria-label={t('common:actions.close')}
        onClick={() => useOnboardingStore.getState().markWelcomeSeen()}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-full text-ink-3 outline-none hover:bg-surface-hover hover:text-ink focus-visible:focus-ring max-[899px]:hidden"
      >
        <X size={ICON_SIZE.xs} />
      </button>
    </span>
  )
}
