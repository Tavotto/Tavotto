/**
 * 准备引导卡的**标题与主按钮只有这一份**（T09 / T13b，ADR 0116）：报告 → 一句话 + 至多一个主按钮（用户硬性要求「卡片一句话读懂」）。
 *
 * 这里是**翻译**，不是判据：
 *
 * * 按钮只来自报告里后端生成的东西——`actions` 里有 `run` 才有「运行」，有 `prepare_dependencies` 才有
 *   「安装」；要先答的事来自 `requirements`（运行目录 / 指认数据；确认模式下还有选环境），答题走既有端点；
 * * 句子按 phase / outcome / requirement kind 查，不从 checks 的布尔值里拼「可以了」；
 * * 执行结束、捕获到图、首次编辑渲染可用是三件事：`completed` 才说「画好了」，跑完没图说「跑完了，没有出图」，
 *   进入编辑之后看渲染态才说「已进入编辑」；
 * * **必填参数没填齐时任何卡都不说「可以运行」**：参数 schema 是后端报告里的（`script_arguments` 待办），草稿缺几个必填项由
 *   `lib/scriptArgsForm.missingRequired` 读出来（`ctx.missingArgs`），这时换成参数卡、主按钮置灰。
 *
 * 前端自己的事实只有四样，都不是业务判据：网络连得上没有（`connection`）、参数草稿与会话冻结的那份是否不同（`argsChanged`）、
 * 草稿按后端 schema 还差几个必填参数（`missingArgs`）、加进画布的图首次编辑渲染好了没有（`editReady`）。
 */
import type {
  EnvCandidate,
  MissingInputOffer,
  PreparationActionKind,
  PreparationReport,
  PreparationRequirement,
  WorkdirConfirmation,
} from '@/lib/api'
import type { PrepEntry } from '@/store/projectPreparationStore'

export type PrepPrimary =
  | { kind: 'action'; action: PreparationActionKind; label: PrepButton }
  | { kind: 'adopt'; candidate: EnvCandidate }
  | { kind: 'workdir'; payload: WorkdirConfirmation }
  | { kind: 'missing_input'; payload: MissingInputOffer }
  | { kind: 'enter_edit'; count: number }
  | { kind: 'reopen'; label: 'retry' | 'continue' }
  | { kind: 'open_environment' }
  | { kind: 'open_registry' }
  /** 运行 / 安装期间：缩成角标，后台照跑（不是取消） */
  | { kind: 'background' }
  /** 跑完没图：知道了，收起卡片 */
  | { kind: 'dismiss' }

/** 主按钮的文案键（`workspace:prep.btn.*`）：按钮说的就是它真正做的那件事 */
export type PrepButton = 'run' | 'runAnyway' | 'runAgain' | 'retry' | 'stop' | 'install' | 'switchScope' | 'recheck'

/** 卡片的视觉语气：标题左边那个小图标（`busy` 转圈、`ok` 勾、`bad` 叹号、`mute` 灰） */
export type PrepTone = 'busy' | 'ok' | 'bad' | 'mute' | null

/** 卡片主区里除标题外要摆出来的那一块（它就是这一步要做的事，不是收起的详情） */
export type PrepSlot = 'workdir' | 'args' | 'install' | 'progress' | 'input' | 'figures' | 'ready' | null

export interface PrepView {
  /** 稳定的状态名（`data-prep-state`）：e2e / 单测按它断言，不按文案 */
  state: string
  /** 卡片标题：`workspace:prep.line.<key>`（一句话，不带句号） */
  sentence: { key: string; values: Record<string, unknown> }
  primary: PrepPrimary | null
  /** 主按钮在但还不能按（必填参数没填齐 / 运行目录还没选） */
  primaryDisabled: boolean
  /** 主按钮旁边那颗：`later` = 稍后（缩成角标）、`stop` = 停止（会话的 cancel，文字按钮） */
  ghost: 'later' | 'stop' | null
  /** 准备 · 运行 · 编辑 走到哪一步 */
  step: 0 | 1 | 2
  tone: PrepTone
  slot: PrepSlot
  /** 嵌入正在等的那一问（同一请求只有一个展示面，卡片认领期间原对话框让开） */
  input: boolean
  /** 参数表单要直接摆出来（这一步要填的内容，不是收起的详情） */
  argsOpen: boolean
}

export interface PrepContext {
  /** 草稿里的参数与会话冻结的不同（用户在卡片里改了参数） */
  argsChanged: boolean
  /** 进入编辑的那张图首次编辑渲染已经可用（渲染态观察） */
  editReady: boolean
  /**
   * 按后端给的参数 schema（报告里 `script_arguments` 待办的载荷）读草稿，还差几个必填参数（`lib/scriptArgsForm.missingRequired`）；
   * `null` = 没有可用的表单（没有 argparse 证据 / 表单关闭），不判
   */
  missingArgs: number | null
}

const has = (report: PreparationReport, kind: PreparationActionKind) =>
  report.actions.some((a) => a.kind === kind)

const act = (action: PreparationActionKind, label: PrepButton): PrepPrimary => ({ kind: 'action', action, label })

const find = <K extends PreparationRequirement['kind']>(
  report: PreparationReport,
  kind: K,
): Extract<PreparationRequirement, { kind: K }> | undefined =>
  report.requirements.find((r) => r.kind === kind) as Extract<PreparationRequirement, { kind: K }> | undefined

type Extra = Partial<Pick<PrepView, 'input' | 'argsOpen' | 'primaryDisabled' | 'ghost' | 'step' | 'tone' | 'slot'>>

/** 有主按钮、又不是正在跑的卡，旁边都给一颗「稍后」（缩成角标） */
const view = (
  state: string,
  key: string,
  values: Record<string, unknown>,
  primary: PrepPrimary | null,
  extra?: Extra,
): PrepView => ({
  state,
  sentence: { key, values },
  primary,
  primaryDisabled: extra?.primaryDisabled ?? false,
  ghost: extra && 'ghost' in extra ? (extra.ghost ?? null) : primary ? 'later' : null,
  step: extra?.step ?? 0,
  tone: extra?.tone ?? null,
  slot: extra?.slot ?? null,
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
/** 这几种状态下「运行」的前提是参数：必填参数没填齐就换成参数卡（任何卡都不说「可以运行」） */
const ARGS_GATED = new Set(['ready', 'gui_dialog', 'restarted', 'needs_args', 'args_changed'])

export function prepView(entry: PrepEntry, ctx: PrepContext): PrepView {
  const script = targetName(entry)
  const v = { script }
  if (entry.failure) return view('check_failed', 'checkFailed', v, { kind: 'reopen', label: 'retry' }, { tone: 'bad' })
  const report = entry.report
  if (!report) {
    if (entry.connection === 'lost') return offline(v)
    return view('checking', 'checking', v, null, { tone: 'busy', slot: 'progress' })
  }
  if (entry.connection === 'lost') return offline(v)
  let base = fromReport(report, entry, ctx, script)
  if (entry.rejection) base = { ...base, state: 'rejected', sentence: { key: 'rejected', values: v } }
  else if (entry.restarted && report.phase === 'ready_to_run') {
    base = { ...base, state: 'restarted', sentence: { key: 'restarted', values: v }, tone: 'mute' }
  }
  if (report.target.kind === 'script' && !BUSY.has(report.phase)) {
    const missing = ctx.missingArgs ?? 0
    if (missing > 0 && (ARGS_GATED.has(base.state) || ctx.argsChanged)) {
      return view('args', 'argsMissing', { script, count: missing }, { kind: 'reopen', label: 'continue' }, {
        primaryDisabled: true,
        slot: 'args',
        argsOpen: true,
      })
    }
    if (ctx.argsChanged) {
      return view('args_changed', 'argsReady', v, { kind: 'reopen', label: 'continue' }, { slot: 'args', argsOpen: true })
    }
  }
  return base
}

const offline = (v: Record<string, unknown>) =>
  view('offline', 'offline', v, null, { tone: 'mute', slot: 'progress', step: 1 })

function fromReport(report: PreparationReport, entry: PrepEntry, ctx: PrepContext, script: string): PrepView {
  const v = { script }
  switch (report.phase) {
    case 'running':
      if (report.outcome.reason === 'cancel_requested') {
        return view('stopping', 'stopping', v, null, { tone: 'busy', slot: 'progress', step: 1 })
      }
      return view('running', 'running', v, { kind: 'background' }, {
        ghost: has(report, 'cancel') ? 'stop' : null,
        tone: 'busy',
        slot: 'progress',
        step: 1,
      })
    case 'awaiting_runtime_input':
      return view('input', report.runtime_input?.secret ? 'inputSecret' : 'input', v, null, {
        input: true,
        slot: 'input',
        step: 1,
        tone: 'mute',
      })
    case 'preparing_environment':
      return view('preparing', 'preparing', v, { kind: 'background' }, {
        ghost: has(report, 'cancel') ? 'stop' : null,
        tone: 'busy',
        slot: 'progress',
      })
    case 'ready_to_run': {
      // 脚本会弹窗选文件 / 询问（后端静态识别，不阻塞）：先说这件事，主按钮仍是报告里的 run，只是改口成「仍然运行」。
      // 不另造「让助手改脚本」的入口——叠栈里没有现成的
      const dialog = find(report, 'gui_dialog')
      if (dialog) {
        const file = dialog.payload.calls.some((c) => c.kind === 'file')
        const ask = dialog.payload.calls.some((c) => c.kind === 'prompt')
        // 选文件与询问都有：一句话涵盖两者，不塌成只讲文件
        const key = file && ask ? 'dialogBoth' : file ? 'dialogFile' : 'dialogAsk'
        return view('gui_dialog', key, v, has(report, 'run') ? act('run', 'runAnyway') : null, {
          slot: 'ready',
          tone: 'bad',
        })
      }
      return view('ready', 'ready', v, has(report, 'run') ? act('run', 'run') : null, { slot: 'ready' })
    }
    case 'completed':
      return completed(report, entry, ctx, script)
    case 'partial':
      if (report.outcome.kind === 'execution_finished_no_figure') {
        return view('no_figure', 'noFigure', v, { kind: 'dismiss' }, { ghost: null, tone: 'mute', step: 1 })
      }
      return view('not_registered', 'notRegistered', v, { kind: 'open_registry' }, { tone: 'bad', step: 1 })
    case 'cancelled':
      return view('cancelled', 'cancelled', v, has(report, 'run') ? act('run', 'runAgain') : null, { tone: 'mute', step: 1 })
    default:
      return needsSomething(report, script)
  }
}

function completed(report: PreparationReport, entry: PrepEntry, ctx: PrepContext, script: string): PrepView {
  const v = { script }
  if (report.outcome.kind === 'static_source') return view('static', 'static', v, null, { tone: 'ok', step: 2 })
  if (entry.editing.length > 0) {
    return ctx.editReady
      ? view('edit_ready', 'editReady', v, null, { tone: 'ok', step: 2 })
      : view('edit_opening', 'editOpening', v, null, { tone: 'busy', step: 2 })
  }
  const count = (report.captured ?? []).length
  // 老后端没有 `captured`：不猜图名
  if (count === 0) return view('completed', 'completedNoList', v, null, { tone: 'ok', step: 2 })
  // 无参数运行整条替换了这个脚本的图名（T03 已知缺口，T09b）：标题照旧，详情里说清哪些旧图不再关联、怎么恢复。
  // 名字来自后端报告（`unlinked_stems`），这里不比对任何清单
  const unlinked = (report.unlinked_stems ?? []).length > 0
  return view(unlinked ? 'completed_unlinked' : 'completed', 'completed', { script, count }, { kind: 'enter_edit', count }, {
    tone: 'ok',
    slot: 'figures',
    step: 2,
  })
}

/** 等用户 / 走不通：先看要先答的事，再看结局 */
function needsSomething(report: PreparationReport, script: string): PrepView {
  const v = { script }
  const env = find(report, 'environment_choice')
  if (env) {
    // 只在确认模式（`TAVOTTO_ENV_ADOPTION=confirm`）出现：默认检测模式下用哪一套由程序决定，不问（ADR 0114 §六）
    const rec = env.payload
    if (rec?.decision?.locked_by) return view('env_locked', 'envLocked', v, { kind: 'open_environment' })
    const pick = rec?.candidates?.find((c) => c.id === rec.recommended_id)
    if (pick) return view('env_choice', 'envChoice', v, { kind: 'adopt', candidate: pick })
    return view('env_choice', 'envChoiceOpen', v, { kind: 'open_environment' })
  }
  const workdir = find(report, 'workdir_choice')
  if (workdir?.payload) {
    return view('workdir', 'workdir', v, { kind: 'workdir', payload: workdir.payload }, {
      // 推荐项来自后端；歧义时不预选，选中之前按钮置灰（卡片按选择定）
      primaryDisabled: !workdir.payload.recommended,
      slot: 'workdir',
    })
  }
  const pinned = find(report, 'dependency_pinned')
  if (pinned) return view('deps_pinned', 'envLocked', v, { kind: 'open_environment' })
  const scope = find(report, 'dependency_scope_choice')
  if (scope) {
    return view('deps_scope', 'depsScope', v, has(report, 'prepare_dependencies') ? act('prepare_dependencies', 'switchScope') : null)
  }
  const deps = find(report, 'dependency_authorization')
  if (deps) {
    const prepare = has(report, 'prepare_dependencies') ? act('prepare_dependencies', 'install') : null
    if (deps.origin === 'runtime_missing') {
      const p = (deps.payload ?? {}) as { module?: string; installable?: boolean }
      if (!p.installable) return view('deps_unknown', 'depsUnknown', { script, module: p.module ?? '' }, { kind: 'open_environment' })
      return view('deps_runtime', 'depsRuntime', { script, module: p.module ?? '' }, prepare, { slot: 'install' })
    }
    return view('deps', 'install', v, prepare, { slot: 'install' })
  }
  const missing = find(report, 'input_location')
  const code = report.outcome.code ?? ''
  if (report.outcome.kind === 'failed' && code === 'script_needs_arguments') {
    return view('needs_args', 'needsArgs', v, { kind: 'reopen', label: 'continue' }, { slot: 'args', argsOpen: true })
  }
  if (missing) return view('missing_data', 'missingData', v, { kind: 'missing_input', payload: missing.payload }, { step: 1 })
  const fallback = has(report, 'recheck') ? act('recheck', 'recheck') : null
  switch (report.outcome.kind) {
    case 'stale':
      return view('stale', 'stale', v, fallback)
    case 'unknown':
      return view(code === 'attempt_expired' ? 'expired' : 'unknown', code === 'attempt_expired' ? 'expired' : 'unknown', v, fallback)
    case 'blocked':
      if (report.checks.some((c) => c.id === 'environment' && c.status === 'blocked')) {
        // 找不到能跑它的那一套：与缺组件同一张卡（检测模式下后端通常直接给安装待办；没有安装动作时到设置里看）
        return view('env_blocked', 'install', v, has(report, 'prepare_dependencies') ? act('prepare_dependencies', 'install') : { kind: 'open_environment' }, { slot: 'install' })
      }
      return view('blocked', 'blocked', v, fallback)
    case 'failed':
      if (report.outcome.reason === 'dependency_preparation') return view('deps_failed', 'depsFailed', v, fallback, { tone: 'bad' })
      return view('failed', 'failed', v, has(report, 'run') ? act('run', 'retry') : fallback, { tone: 'bad', step: 1 })
    case 'cancelled':
      return view('cancelled', 'depsCancelled', v, fallback, { tone: 'mute' })
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
