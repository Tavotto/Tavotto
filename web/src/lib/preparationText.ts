/**
 * 准备面板的**句子与主按钮只有这一份**（T09，ADR 0116）：报告 → 一句话 + 至多一个主按钮（用户硬性要求「卡片一句话读懂」）。
 *
 * 这里是**翻译**，不是判据：
 *
 * * 按钮只来自报告里后端生成的东西——`actions` 里有 `run` 才有「确认并运行」，有 `prepare_dependencies` 才有
 *   「准备依赖」；要先答的事来自 `requirements`（环境选择 / 运行目录 / 指认数据），答题走既有端点；
 * * 句子按 phase / outcome / requirement kind 查，不从 checks 的布尔值里拼「可以了」；
 * * 执行结束、捕获到图、首次编辑渲染可用是三件事：`completed` 才说「已捕获」，跑完没图说「运行完成，未发现可编辑图」，
 *   进入编辑之后看渲染态才说「已进入编辑」。
 *
 * 前端自己的事实只有三样，都不是业务判据：网络连得上没有（`connection`）、参数草稿与会话冻结的那份是否不同（`argsChanged`）、
 * 加进画布的图首次编辑渲染好了没有（`editReady`）。
 */
import type {
  EnvCandidate,
  MissingInputOffer,
  PreparationActionKind,
  PreparationReport,
  PreparationRequirement,
  WorkdirConfirmation,
} from '@/lib/api'
import { listJoin } from '@/i18n/format'
import type { PrepEntry } from '@/store/projectPreparationStore'

export type PrepPrimary =
  | { kind: 'action'; action: PreparationActionKind; label: PrepButton }
  | { kind: 'adopt'; candidate: EnvCandidate }
  | { kind: 'workdir'; payload: WorkdirConfirmation }
  | { kind: 'missing_input'; payload: MissingInputOffer }
  | { kind: 'enter_edit'; count: number }
  | { kind: 'reopen'; label: 'retry' | 'checkArgs' }
  | { kind: 'open_environment' }
  | { kind: 'open_registry' }

/** 主按钮的文案键（`workspace:prep.btn.*`）：按钮说的就是它真正做的那件事 */
export type PrepButton = 'run' | 'runAgain' | 'stop' | 'prepareDeps' | 'switchScope' | 'recheck'

export interface PrepView {
  /** 稳定的状态名（`data-prep-state`）：e2e / 单测按它断言，不按文案 */
  state: string
  /** `workspace:prep.line.<key>` */
  sentence: { key: string; values: Record<string, unknown> }
  primary: PrepPrimary | null
  /** 嵌入正在等的那一问（同一请求只有一个展示面，面板认领期间原对话框让开） */
  input: boolean
  /** 参数编辑器要直接摆出来（脚本说缺参数）——它就是这一步要填的内容，不是收起的详情 */
  argsOpen: boolean
}

export interface PrepContext {
  /** 草稿里的参数与会话冻结的不同（用户在面板里改了参数） */
  argsChanged: boolean
  /** 进入编辑的那张图首次编辑渲染已经可用（渲染态观察） */
  editReady: boolean
}

const has = (report: PreparationReport, kind: PreparationActionKind) =>
  report.actions.some((a) => a.kind === kind)

const act = (action: PreparationActionKind, label: PrepButton): PrepPrimary => ({ kind: 'action', action, label })

const find = <K extends PreparationRequirement['kind']>(
  report: PreparationReport,
  kind: K,
): Extract<PreparationRequirement, { kind: K }> | undefined =>
  report.requirements.find((r) => r.kind === kind) as Extract<PreparationRequirement, { kind: K }> | undefined

const view = (
  state: string,
  key: string,
  values: Record<string, unknown>,
  primary: PrepPrimary | null,
  extra?: Partial<Pick<PrepView, 'input' | 'argsOpen'>>,
): PrepView => ({
  state,
  sentence: { key, values },
  primary,
  input: extra?.input ?? false,
  argsOpen: extra?.argsOpen ?? false,
})

/** 报告里的目标名（脚本相对路径；已知素材用图名） */
export const targetName = (entry: PrepEntry): string => {
  const t = entry.report?.target
  if (t?.script) return t.script
  if (t?.stem) return t.stem
  return 'id' in entry.target ? entry.target.id : entry.target.script
}

const BUSY = new Set(['running', 'awaiting_runtime_input', 'preparing_environment'])

export function prepView(entry: PrepEntry, ctx: PrepContext): PrepView {
  const script = targetName(entry)
  const v = { script }
  if (entry.failure) return view('check_failed', 'checkFailed', v, { kind: 'reopen', label: 'retry' })
  const report = entry.report
  if (!report) {
    if (entry.connection === 'lost') return view('offline', 'offline', v, null)
    return view('checking', 'checking', v, null)
  }
  if (entry.connection === 'lost') return view('offline', 'offline', v, null)
  const base = fromReport(report, entry, ctx, script)
  if (ctx.argsChanged && !BUSY.has(report.phase) && report.target.kind === 'script') {
    return view('args_changed', 'argsChanged', v, { kind: 'reopen', label: 'checkArgs' })
  }
  if (entry.rejection) return { ...base, state: 'rejected', sentence: { key: 'rejected', values: v } }
  if (entry.restarted && report.phase === 'ready_to_run') {
    return { ...base, state: 'restarted', sentence: { key: 'restarted', values: v } }
  }
  return base
}

function fromReport(report: PreparationReport, entry: PrepEntry, ctx: PrepContext, script: string): PrepView {
  const v = { script }
  switch (report.phase) {
    case 'running':
      if (report.outcome.reason === 'cancel_requested') return view('stopping', 'stopping', v, null)
      return view('running', 'running', v, has(report, 'cancel') ? act('cancel', 'stop') : null)
    case 'awaiting_runtime_input':
      return view('input', 'input', v, null, { input: true })
    case 'preparing_environment':
      return view(
        'preparing',
        report.outcome.reason === 'joined_existing' ? 'preparingJoined' : 'preparing',
        v,
        has(report, 'cancel') ? act('cancel', 'stop') : null,
      )
    case 'ready_to_run': {
      const run = report.actions.find((a) => a.kind === 'run')
      const count = run?.impact.script_arguments ?? 0
      return view('ready', count > 0 ? 'readyArgs' : 'ready', { script, count }, run ? act('run', 'run') : null)
    }
    case 'completed':
      return completed(report, entry, ctx, script)
    case 'partial':
      if (report.outcome.kind === 'execution_finished_no_figure') return view('no_figure', 'noFigure', v, null)
      return view('not_registered', 'notRegistered', v, { kind: 'open_registry' })
    case 'cancelled':
      return view('cancelled', 'cancelled', v, has(report, 'run') ? act('run', 'runAgain') : null)
    default:
      return needsSomething(report, script)
  }
}

function completed(report: PreparationReport, entry: PrepEntry, ctx: PrepContext, script: string): PrepView {
  const v = { script }
  if (report.outcome.kind === 'static_source') return view('static', 'static', v, null)
  if (entry.editing.length > 0) {
    return ctx.editReady ? view('edit_ready', 'editReady', v, null) : view('edit_opening', 'editOpening', v, null)
  }
  const count = (report.captured ?? []).length
  if (count === 0) return view('completed', 'completedNoList', v, null) // 老后端没有 `captured`：不猜图名
  // 无参数运行整条替换了这个脚本的图名（T03 已知缺口，T09b）：同一句里说清哪些旧图不再关联、怎么恢复（用原参数再运行）。
  // 名字来自后端报告（`unlinked_stems`），这里不比对任何清单
  const unlinked = report.unlinked_stems ?? []
  if (unlinked.length > 0) {
    return view('completed_unlinked', 'completedUnlinked', { script, count, names: listJoin(unlinked) }, {
      kind: 'enter_edit',
      count,
    })
  }
  return view('completed', 'completed', { script, count }, { kind: 'enter_edit', count })
}

/** 等用户 / 走不通：先看要先答的事，再看结局 */
function needsSomething(report: PreparationReport, script: string): PrepView {
  const v = { script }
  const env = find(report, 'environment_choice')
  if (env) {
    const rec = env.payload
    if (rec?.decision?.locked_by) return view('env_locked', 'envLocked', v, { kind: 'open_environment' })
    const pick = rec?.candidates?.find((c) => c.id === rec.recommended_id)
    if (pick) {
      return view('env_choice', 'envChoice', { script, name: pick.python_relative || pick.name || pick.id }, {
        kind: 'adopt',
        candidate: pick,
      })
    }
    return view('env_choice', 'envChoiceOpen', v, { kind: 'open_environment' })
  }
  const workdir = find(report, 'workdir_choice')
  if (workdir?.payload) return view('workdir', 'workdir', v, { kind: 'workdir', payload: workdir.payload })
  const pinned = find(report, 'dependency_pinned')
  if (pinned) return view('deps_pinned', 'depsPinned', v, { kind: 'open_environment' })
  const scope = find(report, 'dependency_scope_choice')
  if (scope) {
    return view('deps_scope', 'depsScope', v, has(report, 'prepare_dependencies') ? act('prepare_dependencies', 'switchScope') : null)
  }
  const deps = find(report, 'dependency_authorization')
  if (deps) {
    const prepare = report.actions.find((a) => a.kind === 'prepare_dependencies')
    if (deps.origin === 'runtime_missing') {
      const p = (deps.payload ?? {}) as { module?: string; installable?: boolean }
      if (!p.installable) return view('deps_unknown', 'depsUnknown', { script, module: p.module ?? '' }, { kind: 'open_environment' })
      return view('deps_runtime', 'depsRuntime', { script, module: p.module ?? '' }, prepare ? act('prepare_dependencies', 'prepareDeps') : null)
    }
    const count = prepare?.impact.installs?.length ?? 0
    return view('deps', count > 0 ? 'deps' : 'depsSome', { script, count }, prepare ? act('prepare_dependencies', 'prepareDeps') : null)
  }
  const missing = find(report, 'input_location')
  const code = report.outcome.code ?? ''
  if (report.outcome.kind === 'failed' && code === 'script_needs_arguments') {
    return view('needs_args', 'needsArgs', v, { kind: 'reopen', label: 'checkArgs' }, { argsOpen: true })
  }
  if (missing) return view('missing_data', 'missingData', v, { kind: 'missing_input', payload: missing.payload })
  const fallback = has(report, 'recheck') ? act('recheck', 'recheck') : null
  switch (report.outcome.kind) {
    case 'stale':
      return view('stale', 'stale', v, fallback)
    case 'unknown':
      return view(code === 'attempt_expired' ? 'expired' : 'unknown', code === 'attempt_expired' ? 'expired' : 'unknown', v, fallback)
    case 'blocked':
      if (report.checks.some((c) => c.id === 'environment' && c.status === 'blocked')) {
        return view('env_blocked', 'envBlocked', v, { kind: 'open_environment' })
      }
      return view('blocked', 'blocked', v, fallback)
    case 'failed':
      if (report.outcome.reason === 'dependency_preparation') return view('deps_failed', 'depsFailed', v, fallback)
      return view('failed', 'failed', v, has(report, 'run') ? act('run', 'runAgain') : fallback)
    case 'cancelled':
      return view('cancelled', 'depsCancelled', v, fallback)
    default:
      return view('attention', 'attention', v, has(report, 'run') ? act('run', 'run') : fallback)
  }
}

/**
 * 素材库脚本行上的那几个字（元数据档，不是句子；`workspace:prep.row.<key>`）：只翻译 phase / outcome，不另判。
 * 回 null = 这一行没有准备会话，行照旧显示自己的清单状态。
 */
export function prepRowKey(entry: PrepEntry | undefined): { key: string; values: Record<string, unknown> } | null {
  if (!entry) return null
  if (entry.failure) return { key: 'attention', values: {} }
  const report = entry.report
  if (!report) return { key: entry.connection === 'lost' ? 'offline' : 'checking', values: {} }
  if (entry.connection === 'lost') return { key: 'offline', values: {} }
  switch (report.phase) {
    case 'running':
      return { key: 'running', values: {} }
    case 'awaiting_runtime_input':
      return { key: 'input', values: {} }
    case 'preparing_environment':
      return { key: 'preparing', values: {} }
    case 'ready_to_run':
      return { key: 'ready', values: {} }
    case 'completed':
      return report.outcome.kind === 'succeeded'
        ? { key: 'captured', values: { count: (report.captured ?? []).length } }
        : { key: 'static', values: {} }
    case 'partial':
      return { key: report.outcome.kind === 'execution_finished_no_figure' ? 'noFigure' : 'attention', values: {} }
    case 'cancelled':
      return { key: 'cancelled', values: {} }
    default:
      return { key: 'attention', values: {} }
  }
}
