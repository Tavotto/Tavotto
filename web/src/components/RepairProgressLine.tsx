import { useTranslation } from 'react-i18next'
import { Check, Circle, LoaderCircle } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { DependencyProgress } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * 修复进度的**一行**（2026-09-29 用户：「一定要让用户一句话就能读懂」）：「正在安装 openpyxl…（3/4）」，下载那一段是
 * 「正在下载 Python… 12 / 25 MB」。脚本行 / 画布右栏的修复卡与跑前的依赖授权框共用这一份；阶段句子由调用方按 state
 * 给（两处的文案表不同），这里补第几步 / 字节数与一根细进度条。完整的阶段列表（`RepairStageList`）只进折叠的「详情」。
 *
 * 字节数读进度里的 `result.download`（U05）：`result` 在后端是「沿用上一条」的，所以只在 state 仍是
 * `downloading_python` 时读它——到了创建环境那一步，那段字节数还挂在进度上，但说的已经是过去的事。
 */
const MB = 1048576

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

/** 这次修复走哪几步：装进项目自己的 `.venv` 不下载、不新建环境，只有后两步 */
const stagesOf = (p: DependencyProgress): Stage[] =>
  p.target_kind === 'project_venv' ? ['packages', 'rerun'] : ['python', 'env', 'packages', 'rerun']

/** 走到第几步（从 1 数）；失败 / 取消 / idle 回 null */
function stepOf(p: DependencyProgress): { n: number; total: number } | null {
  const stage = STAGE_OF[p.state]
  if (!stage) return null
  const stages = stagesOf(p)
  return { n: Math.max(0, stages.indexOf(stage)) + 1, total: stages.length }
}

export function RepairProgressLine({ progress, text }: { progress: DependencyProgress; text: string }) {
  const { t } = useTranslation('errors')
  const download = progress.state === 'downloading_python' ? progress.result?.download : undefined
  // 下载完之后还有校验 / 解压 / 试启动几步：那时不再是字节数，换一句正在做什么
  const downloading = !!download && download.stage === 'downloading' && download.total_bytes > 0
  const pct = downloading ? Math.min(100, Math.round((download.done_bytes / download.total_bytes) * 100)) : null
  const step = stepOf(progress)
  const line = !download
    ? `${text}${step ? t('engine.repairStep', step) : ''}`
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

/** 完整的阶段列表（准备 Python → 创建运行环境 → 安装所需的包 → 重新运行）：只放进折叠的「详情」 */
export function RepairStageList({ progress }: { progress: DependencyProgress }) {
  const { t } = useTranslation('errors')
  const step = stepOf(progress)
  if (!step) return null
  return (
    <ol className="flex flex-col gap-0.5" data-repair-stages>
      {stagesOf(progress).map((stage, i) => {
        const state = i + 1 < step.n ? 'done' : i + 1 === step.n ? 'active' : 'pending'
        return (
          <li
            key={stage}
            data-repair-stage={stage}
            data-stage-state={state}
            className={cn('flex items-center gap-1.5', state === 'active' ? 'text-ink' : 'text-ink-3')}
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
          </li>
        )
      })}
    </ol>
  )
}
