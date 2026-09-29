import { useTranslation } from 'react-i18next'
import { Check, Circle, LoaderCircle } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { DependencyProgress } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * 一键修复的阶段条：准备 Python → 创建环境 → 安装包 → 重新运行。脚本行 / 画布右栏的修复卡与跑前的依赖授权框
 * 共用这一份（同一条 SSE `engine.dependency`、同一张状态表）。
 *
 * 阶段**只按 state 换**，不解析日志（ADR 0019）。下载私有 Python 那一段读进度里的 `result.download`
 * （字节数，U05）：`result` 在后端是「沿用上一条」的，所以只在 state 仍是 `downloading_python` 时读它——
 * 到了创建环境那一步，那段字节数还挂在进度上，但说的已经是过去的事。
 *
 * 装进项目自己的 `.venv` 时没有前两段（不下载、不新建环境），只列后两段。
 */
type Stage = 'python' | 'env' | 'packages' | 'rerun'

const STAGE_OF: Partial<Record<DependencyProgress['state'], Stage>> = {
  preparing: 'python',
  downloading_python: 'python',
  creating_env: 'env',
  installing: 'packages',
  verifying: 'packages',
  done: 'rerun',
}

//: 文案键写成字面量：errors.json 的死键门禁按「源码里出现过这个串」判活
const STAGE_LABEL: Record<Stage, string> = {
  python: 'engine.repairStage_python',
  env: 'engine.repairStage_env',
  packages: 'engine.repairStage_packages',
  rerun: 'engine.repairStage_rerun',
}

const MB = 1048576
const mb = (bytes: number) => (bytes / MB).toFixed(1)

export function RepairStages({ progress }: { progress: DependencyProgress }) {
  const { t } = useTranslation('errors')
  const current = STAGE_OF[progress.state]
  // 失败 / 取消 / idle 不画阶段条：那时卡片说的是结局，不是走到了哪一步
  if (!current) return null
  const stages: Stage[] =
    progress.target_kind === 'project_venv' ? ['packages', 'rerun'] : ['python', 'env', 'packages', 'rerun']
  // 项目 `.venv` 的 preparing 落在不画的那一段上：当作第一段之前
  const at = Math.max(0, stages.indexOf(current))
  const download = progress.state === 'downloading_python' ? progress.result?.download : undefined
  return (
    <div className="flex flex-col gap-1.5" data-repair-stages>
      <ol className="flex flex-col gap-0.5">
        {stages.map((stage, i) => {
          const state = i < at ? 'done' : i === at ? 'active' : 'pending'
          return (
            <li
              key={stage}
              data-repair-stage={stage}
              data-stage-state={state}
              className={cn(
                'flex items-center gap-1.5 text-xs',
                state === 'active' ? 'font-medium text-ink' : state === 'done' ? 'text-ink-2' : 'text-ink-3',
              )}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
                {state === 'done' ? (
                  <Check size={ICON_SIZE.xs} />
                ) : state === 'active' ? (
                  <LoaderCircle size={ICON_SIZE.xs} className="motion-safe:animate-spin" />
                ) : (
                  <Circle size={ICON_SIZE.xs} />
                )}
              </span>
              {t(STAGE_LABEL[stage])}
              {/* 选中态不只靠颜色：读屏另读一个状态词 */}
              {state === 'done' && <span className="sr-only">{t('engine.repairStageDone')}</span>}
              {state === 'active' && <span className="sr-only">{t('engine.repairStageActive')}</span>}
            </li>
          )
        })}
      </ol>
      {download && <DownloadBar download={download} />}
    </div>
  )
}

function DownloadBar({ download }: { download: { stage: string; done_bytes: number; total_bytes: number } }) {
  const { t } = useTranslation('errors')
  const total = download.total_bytes
  // 下载完之后还有校验 / 解压 / 试启动几步：那时不再是百分比，说一句正在做什么
  const downloading = download.stage === 'downloading' && total > 0
  const pct = downloading ? Math.min(100, Math.round((download.done_bytes / total) * 100)) : null
  return (
    <div className="flex flex-col gap-1 pl-5" data-repair-download>
      <div
        role="progressbar"
        aria-label={t('engine.repairDownloadAria')}
        aria-valuenow={pct ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1 overflow-hidden rounded-full bg-surface-2"
      >
        {/* 与更新下载同一种颜色：蓝色不做大块背景 */}
        <div
          className={cn('h-full bg-ink', pct === null && 'w-full animate-pulse')}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-ink-3" data-repair-download-text>
        {pct === null
          ? t('engine.repairDownloadUnpacking')
          : t('engine.repairDownloadProgress', { done: mb(download.done_bytes), total: mb(total), pct })}
      </span>
    </div>
  )
}
