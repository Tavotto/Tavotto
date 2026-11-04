import { useTranslation } from 'react-i18next'
import { isDesktop } from '@/lib/desktop'
import { useUpdateStore } from '@/store/updateStore'
import { Button } from './ui/Button'
import { Popover } from './ui/Popover'
import { cn } from '@/lib/utils'

/**
 * 桌面版「新版已就绪」：后台自动下载（默认开，设置里可关）下好新版之后，**顶栏右侧的状态芯片**——
 * 与 `BuildMismatchChip` 同一形态：24 高胶囊（surface 底 + 1px 描边环，一枚 6px accent 圆点 + 一句短语），
 * 不进文档流、出现时只淡入，窄于 900 只留圆点（短语给读屏）。点开是一句话 + 一个主按钮。
 *
 * 不点也没关系：退出应用时壳会自动装上，下次打开就是新版（运行中不替换——sidecar 与渲染 worker 会按需从包里
 * 载入代码，换包会让同一会话混用新旧版本）。「现在重启」用已下好的包安装并重启，不重新下载。
 * 后台下载失败不出任何东西（静默）；用户已经在设置里手动装好、等重启时（`installed`）也不再重复提示。
 */
export function DesktopUpdateChip() {
  const { t } = useTranslation('workspace')
  const ready = useUpdateStore((s) => s.desktopReady)
  const phase = useUpdateStore((s) => s.desktopPhase)
  const relaunching = useUpdateStore((s) => s.relaunching)
  const restartNow = useUpdateStore((s) => s.restartNow)
  if (!isDesktop() || !ready || phase === 'installed') return null
  const label = t('updateReady.chip')
  const busy = phase !== 'idle' || relaunching
  return (
    <Popover
      width={264}
      align="end"
      ariaLabel={label}
      trigger={
        <button
          type="button"
          data-update-ready=""
          aria-label={label}
          className={cn(
            'inline-flex h-6 min-w-0 shrink-0 animate-fade-in items-center gap-1.5 rounded-full bg-surface px-2.5 text-xs text-ink-2 outline-none inset-ring inset-ring-border',
            'hover:bg-surface-hover focus-visible:focus-ring data-[state=open]:bg-surface-hover',
            'max-[899px]:w-6 max-[899px]:justify-center max-[899px]:px-0',
          )}
        >
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-accent" />
          <span className="truncate max-[899px]:sr-only">{label}</span>
        </button>
      }
    >
      <div data-update-ready-popover="" className="flex flex-col items-start gap-2 p-1">
        <p className="type-reading text-ink">{t('updateReady.body', { version: ready.version })}</p>
        <Button
          variant="primary"
          size="sm"
          data-update-ready-restart
          loading={busy}
          onClick={() => void restartNow()}
        >
          {t('updateReady.restart')}
        </Button>
      </div>
    </Popover>
  )
}
