import { useTranslation } from 'react-i18next'
import type { DependencyProgress } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * 修复进度的**一行**（2026-09-29 用户：「一定要让用户一句话就能读懂」）：「正在下载 Python… 12 / 25 MB」。
 * 脚本行 / 画布右栏的修复卡与跑前的依赖授权框共用这一份；阶段句子由调用方按 state 给（两处的文案表不同），
 * 这里只补下载那一段的字节数与一根细进度条。
 *
 * 字节数读进度里的 `result.download`（U05）：`result` 在后端是「沿用上一条」的，所以只在 state 仍是
 * `downloading_python` 时读它——到了创建环境那一步，那段字节数还挂在进度上，但说的已经是过去的事。
 */
const MB = 1048576

export function RepairProgressLine({ progress, text }: { progress: DependencyProgress; text: string }) {
  const { t } = useTranslation('errors')
  const download = progress.state === 'downloading_python' ? progress.result?.download : undefined
  // 下载完之后还有校验 / 解压 / 试启动几步：那时不再是字节数，换一句正在做什么
  const downloading = !!download && download.stage === 'downloading' && download.total_bytes > 0
  const pct = downloading ? Math.min(100, Math.round((download.done_bytes / download.total_bytes) * 100)) : null
  const line = !download
    ? text
    : downloading
      ? `${text} ${t('engine.repairDownloadBytes', {
          done: Math.round(download.done_bytes / MB),
          total: Math.round(download.total_bytes / MB),
        })}`
      : t('engine.repairDownloadUnpacking')
  return (
    <div className="flex flex-col gap-1.5">
      <p className="type-section tabular-nums" data-repair-state={progress.state} data-repair-line>
        {line}
      </p>
      {download && (
        <div
          role="progressbar"
          aria-label={t('engine.repairDownloadAria')}
          aria-valuenow={pct ?? undefined}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1 overflow-hidden rounded-full bg-surface-2"
          data-repair-download
        >
          {/* 与更新下载同一种颜色：蓝色不做大块背景 */}
          <div
            className={cn('h-full bg-ink', pct === null && 'w-full animate-pulse')}
            style={pct === null ? undefined : { width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  )
}
