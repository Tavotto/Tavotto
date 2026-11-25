import { useTranslation } from 'react-i18next'
import { TaskChecklist, type TaskStep, type TaskStepState } from '@/components/ui/TaskChecklist'
import type { DependencyProgress } from '@/lib/api'

/**
 * 修复进度（2026-11-25 用户拍板，照「AI Coding Agent 不该只给一个总进度条」的视频做）：一块任务清单——标题行是这一刻在做的
 * 那句话（「正在安装 openpyxl…」），下面一开始就列全这次要走的几步（准备 Python → 创建运行环境 → 安装所需的包 → 重新运行），
 * 做完的勾掉、进行中的转圈、下载那一步带「12 / 25 MB」胶囊。替掉了原来的「一句话（3/4）+ 进度条」与折在详情里的阶段列表。
 * 脚本行 / 画布右栏的修复卡与跑前的依赖授权框共用这一份；阶段句子由调用方按 state 给（两处的文案表不同）。
 *
 * 字节数读进度里的 `result.download`（U05）：`result` 在后端是「沿用上一条」的，所以只在 state 仍是
 * `downloading_python` 时读它——到了创建环境那一步，那段字节数还挂在进度上，但说的已经是过去的事。
 */
const MB = 1048576

/**
 * 供应私有 Python 的子阶段（后端 #743 `privatepython.STAGE_*` 闭集：downloading / verifying / extracting / launching /
 * committed，经 `deprepair._provision_private_base` 放在进度的 `result.download.stage`）→ 一句话。`downloading` 在上面
 * 按字节数说；`committed` 一闪而过、下一条就是创建环境，与认不出的一起落到通用的「正在准备 Python…」
 */
//: 文案键写成字面量：errors.json 的死键门禁按「源码里出现过这个串」判活
const SUBSTAGE_TEXT: Record<string, string> = {
  verifying: 'engine.repairPythonVerifying',
  extracting: 'engine.repairDownloadUnpacking',
  launching: 'engine.repairPythonLaunching',
}

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
  // 字节数只在**真的在下载**时说：安装包自带 / 已缓存的归档（进度里 `result.private_python.origin`，#743
  // `_provision_private_base` 与计划载荷同一个字段）后端同样先发一条 `downloading`、0 字节——那时说「正在下载…
  // 0 / 25 MB」是假话。缺 origin 的老后端按下载处理
  const origin = progress.result?.private_python?.origin
  const downloading =
    !!download && download.stage === 'downloading' && download.total_bytes > 0 && (!origin || origin === 'download')
  const title =
    !download || downloading
      ? text
      : // 下载之后的几步各说各的（#743 `privatepython.STAGE_*` 闭集）；认不出的子阶段安静降级成通用的一句
        t(SUBSTAGE_TEXT[download.stage] ?? 'engine.repairPythonPreparing')
  const step = stepOf(progress)
  const stages = stagesOf(progress)
  // 胶囊只放真数字（下载的字节数）；其余几步只有转圈
  const meta = downloading
    ? t('engine.repairDownloadBytes', {
        done: Math.round(download.done_bytes / MB),
        total: Math.round(download.total_bytes / MB),
      })
    : undefined
  const steps: TaskStep[] = stages.map((stage, i) => {
    // `done` 那一刻后端已装好、正在重跑：前几步全勾，「重新运行」在转
    const state: TaskStepState = !step ? 'pending' : i + 1 < step.n ? 'done' : i + 1 === step.n ? 'active' : 'pending'
    return { id: stage, label: t(STAGE_LABEL[stage]), state, meta: stage === 'python' ? meta : undefined }
  })
  return (
    <TaskChecklist
      title={title}
      steps={steps}
      data-repair-line
      data-repair-state={progress.state}
      data-repair-download={downloading ? Math.min(100, Math.round((download.done_bytes / download.total_bytes) * 100)) : undefined}
    />
  )
}
