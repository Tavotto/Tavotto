import { Button } from '../../ui/Button'
import { installProgressText } from './useAgentInstall'

/**
 * 行右的动作按钮（安装并登录 / 重新安装 / 打开）。安装中它本身就是进度条：
 * 同一份文字叠两层——底层 ink 字在灰底上，覆盖层 surface 字在 ink 底上，按进度从左往右露出，被填到的部分字变白；
 * 两套主题只换 token。安装中不用 disabled（会压成 opacity-40），改 aria-disabled 并拦点击；
 * 宽度取「原文案」与「正在安装… 100%」较宽者，切换不跳。进度未知时不填充（不画假进度），只有文字「正在安装…」。
 */
export function InstallActionButton({
  label,
  running,
  progress,
  onClick,
  action,
}: {
  label: string
  running: boolean
  progress: number | undefined
  onClick: () => void
  action: string
}) {
  const known = typeof progress === 'number' && progress >= 0 && progress <= 1
  const pct = known ? Math.round(progress * 100) : null
  const text = running ? installProgressText(progress) : label
  return (
    <Button
      variant="secondary"
      size="sm"
      data-connect-action={action}
      aria-disabled={running || undefined}
      onClick={running ? undefined : onClick}
      className="relative overflow-hidden"
    >
      <span className="grid place-items-center">
        {/* 定宽：两种文案里较宽的那个决定按钮宽度 */}
        <span className="invisible col-start-1 row-start-1" aria-hidden>
          {label}
        </span>
        <span className="invisible col-start-1 row-start-1" aria-hidden>
          {installProgressText(1)}
        </span>
        <span className="col-start-1 row-start-1">{text}</span>
      </span>
      {running && pct !== null && (
        <span
          role="progressbar"
          aria-label={text}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          data-connect-install-progress
          style={{ clipPath: `inset(0 calc(100% - ${pct}%) 0 0)` }}
          className="pointer-events-none absolute inset-0 flex items-center justify-center bg-ink text-surface transition-[clip-path] duration-slow ease-out motion-reduce:transition-none"
        >
          {text}
        </span>
      )}
    </Button>
  )
}
