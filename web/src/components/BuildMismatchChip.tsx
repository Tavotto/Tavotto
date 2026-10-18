import { useTranslation } from 'react-i18next'
import { useBuildVersion } from '@/hooks/useBuildVersion'
import { Button } from './ui/Button'
import { Popover } from './ui/Popover'
import { cn } from '@/lib/utils'

/**
 * 页面里跑的前端与后端不是同一次构建（`useBuildVersion`）时的那一句话——**顶栏右侧的状态芯片**
 * （2026-10-16 UI 2.0「不推挤」）。
 *
 * 此前它是工作面板顶上一条整宽的提示条（`BannerStack`），出现时把画布标签行与画布整体下推 44px——
 * 全产品唯一一条横贯工作面板、会让用户正在看的东西位移的提示。现在它是 24 高的胶囊（surface 底 + 1px 描边环，
 * 一枚 6px 圆点 + 一句短语），坐在顶栏里，**不进文档流**，出现时只淡入；点开是一句话 + 一个动作的小浮层。
 *
 * 只提示、不自动刷新——用户可能正在图内编辑或等 AI 跑完。没有「稍后」：芯片本来就安静，常驻到处理为止。
 * 与「有新版本」（`UpdateNoticeDialog`，升级通道）是两件事：这里说的是**这一页**落后于本机的后端。
 * 窄于 900 只留圆点（短语给读屏）。
 */
export function BuildMismatchChip() {
  const { t } = useTranslation(['workspace', 'common'])
  const outdated = useBuildVersion()
  if (!outdated) return null
  const label = t('update.chip')
  return (
    <Popover
      width={264}
      align="end"
      ariaLabel={label}
      trigger={
        <button
          type="button"
          data-build-mismatch=""
          aria-label={label}
          className={cn(
            'inline-flex h-6 min-w-0 shrink-0 animate-fade-in items-center gap-1.5 rounded-full bg-surface px-2.5 text-xs text-ink-2 outline-none inset-ring inset-ring-border',
            'hover:bg-surface-hover focus-visible:focus-ring data-[state=open]:bg-surface-hover',
            'max-[899px]:w-6 max-[899px]:justify-center max-[899px]:px-0',
          )}
        >
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warn" />
          <span className="truncate max-[899px]:sr-only">{label}</span>
        </button>
      }
    >
      <div data-build-mismatch-popover="" className="flex flex-col items-start gap-2 p-1">
        <p className="type-reading text-ink">{t('update.banner')}</p>
        <Button variant="primary" size="sm" data-build-mismatch-refresh onClick={() => location.reload()}>
          {t('common:actions.refresh')}
        </Button>
      </div>
    </Popover>
  )
}
