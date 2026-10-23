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
  ConvertSourceLanguage,
  EnvCandidate,
  GuiDialogPayload,
  MissingInputOffer,
  PreparationActionKind,
  PreparationAttempt,
  PreparationReport,
  PreparationRequirement,
  WorkdirConfirmation,
} from '@/lib/api'
import { listJoin } from '@/i18n/format'
import { isRemedy, type Remedy } from '@/lib/fixRemedy'
import { originFactsOf } from '@/lib/importOrigin'
import type { PrepEntry } from '@/store/projectPreparationStore'

export type PrepPrimary =
  | { kind: 'action'; action: PreparationActionKind; label: PrepButton }
  | { kind: 'adopt'; candidate: EnvCandidate }
  | { kind: 'workdir'; payload: WorkdirConfirmation }
  | { kind: 'missing_input'; payload: MissingInputOffer }
  | { kind: 'enter_edit'; count: number }
  | { kind: 'reopen'; label: 'retry' | 'continue' }
  | { kind: 'open_environment' }
  /** 有 import 后端给了预填、要用户确认包名（Import Origin PR6）：打开「确认包名」框（先取一份计划载荷，要到名字） */
  | { kind: 'confirm_origins' }
  /** 装不了 / 标准库缺了：直接打开「渲染环境」弹窗并展开「使用其他 Python 环境」（不是只打开设置） */
  | { kind: 'pick_environment' }
  | { kind: 'open_registry' }
  /** 跑完没出 Matplotlib 图、但脚本自己写了图片：去素材库找（项目里的图片文件素材库本来就列） */
  | { kind: 'open_assets' }
  /** 运行 / 安装期间：缩成角标，后台照跑（不是取消） */
  | { kind: 'background' }
  /** 跑完没图：知道了，收起卡片 */
  | { kind: 'dismiss' }
  /** 位图库画的图：让改图助手另写一份 Matplotlib 版新脚本（ADR 0143）；原脚本不动，用户对照后才采用 */
  | { kind: 'convert'; script: string; library: string | null; path: string | null; language?: ConvertSourceLanguage }
  /** 智能修复（ADR 0148）：「✦ 使用你的 AI 来修复」。remedy 是 `ai_fix` / `connect_ai` 时它就是主按钮；没连 AI 时点它先去连 */
  | { kind: 'ai_fix' }
  /** 反馈卡（remedy `feedback`）：「发送」——发送问题反馈（ADR 0118，逐次确认、源码默认不勾） */
  | { kind: 'feedback' }

/** 主按钮的文案键（`workspace:prep.btn.*`）：按钮说的就是它真正做的那件事 */
export type PrepButton = 'run' | 'openRun' | 'runAnyway' | 'runAgain' | 'retry' | 'stop' | 'install' | 'switchScope' | 'recheck'

/** 卡片的视觉语气：标题左边那个小图标（`busy` 转圈、`ok` 勾、`bad` 叹号、`mute` 灰） */
export type PrepTone = 'busy' | 'ok' | 'bad' | 'mute' | null

/** 卡片主区里除标题外要摆出来的那一块（它就是这一步要做的事，不是收起的详情） */
export type PrepSlot =
  | 'workdir'
  | 'args'
  | 'install'
  | 'progress'
  | 'python_change'
  | 'input'
  | 'figures'
  | 'ready'
  | null

export interface PrepView {
  /** 稳定的状态名（`data-prep-state`）：e2e / 单测按它断言，不按文案 */
  state: string
  /** 卡片标题：`workspace:prep.line.<key>`（一句话，不带句号） */
  sentence: { key: string; values: Record<string, unknown> }
  primary: PrepPrimary | null
  /** 主按钮旁边保持原样的次按钮（位图提示卡：只当图片用 / 打开素材库）；多数卡没有 */
  secondary: PrepPrimary | null
  /** 主按钮在但还不能按（必填参数没填齐 / 运行目录还没选） */
  primaryDisabled: boolean
  /** 主按钮旁边那颗：`later` = 稍后（缩成角标）、`stop` = 停止（会话的 cancel，文字按钮） */
  ghost: 'later' | 'stop' | null
  /** 准备 · 运行 · 编辑 走到哪一步 */
  step: 0 | 1 | 2
  tone: PrepTone
  slot: PrepSlot
  /**
   * 这张失败卡要不要摆一颗显眼的次按钮「✦ 使用你的 AI 来修复」（ADR 0148，维护者裁决：每次失败都出，不管 remedy 是什么；
   * 例外：`switch_native` 是静默回退、正在运行、教程层激活）。主按钮已经是它自己时为 false（不重复摆）
   */
  aiFix: boolean
  /** 本卡对应的 remedy（报告里认得的形状）；没有 = null。只给测试与详情读，按钮已经体现在 `primary` 里 */
  remedy: Remedy | null
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
  /**
   * 壳已确认本机引擎退出了（`exitNoticeStore.engineExit`）：此时「连接断了，正在重连…」是假话——没有谁在重连，
   * 要用户点通知轨上的「重新启动」。其它原因的断线（网络暂断）这一位是 false，提示保持原样。
   */
  engineExited?: boolean
  /** 教程层 / 教程项目激活：任何「连接 AI」相关 UI 都不出现（ADR 0136） */
  tutorial?: boolean
}

const has = (report: PreparationReport, kind: PreparationActionKind) =>
  report.actions.some((a) => a.kind === kind)

const act = (action: PreparationActionKind, label: PrepButton): PrepPrimary => ({ kind: 'action', action, label })

/**
 * 「运行」这件事的主按钮：报告里有 `run_when_ready`（「打开并运行」，ADR 0124）就认领它——准备、运行、进入编辑一次点完；
 * 老后端 / 已经跑过一次之后只有 `run`，就认领 `run`。按钮来自后端报告里的动作，这里不判「能不能」。
 * `label` 是这张卡想说的字（「仍然运行」「再运行一次」…）；缺省时 `run_when_ready` 说「打开并运行」、`run` 说「运行」
 */
const runPrimary = (report: PreparationReport, label?: PrepButton): PrepPrimary | null => {
  if (has(report, 'run_when_ready')) return act('run_when_ready', label ?? 'openRun')
  if (has(report, 'run')) return act('run', label ?? 'run')
  return null
}

const find = <K extends PreparationRequirement['kind']>(
  report: PreparationReport,
  kind: K,
): Extract<PreparationRequirement, { kind: K }> | undefined =>
  report.requirements.find((r) => r.kind === kind) as Extract<PreparationRequirement, { kind: K }> | undefined

/** 账本里的执行尝试（去掉「自动换 Python」那种非执行条目） */
export const runAttempts = (report: PreparationReport | null | undefined): PreparationAttempt[] =>
  (report?.attempt_history ?? []).filter((a): a is PreparationAttempt => a.kind !== 'python_changed')

/** 当前这一次尝试在账本里的那一条（没有就取最后一条）；老后端没有账本 = undefined */
export const currentAttempt = (report: PreparationReport): PreparationAttempt | undefined => {
  const all = runAttempts(report)
  return all.find((a) => a.attempt_id === report.provider.attempt_id) ?? all[all.length - 1]
}

/** 这个会话的账本里有没有 safe → native 的回退（native 尝试的触发原因 = safe 自己的限制） */
export const fellBack = (report: PreparationReport): boolean => {
  const current = currentAttempt(report)
  // 只看**本次运行**（账本按 run 分组，历史里更早一次运行的回退不算）
  return runAttempts(report).some(
    (a) => current !== undefined && a.run === current.run && a.profile === 'native' && a.changed.includes('profile'),
  )
}

type Extra = Partial<Pick<PrepView, 'secondary' | 'input' | 'argsOpen' | 'primaryDisabled' | 'ghost' | 'step' | 'tone' | 'slot'>>

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
  secondary: extra?.secondary ?? null,
  primaryDisabled: extra?.primaryDisabled ?? false,
  ghost: extra && 'ghost' in extra ? (extra.ghost ?? null) : primary ? 'later' : null,
  step: extra?.step ?? 0,
  tone: extra?.tone ?? null,
  slot: extra?.slot ?? null,
  aiFix: false,
  remedy: null,
  input: extra?.input ?? false,
  argsOpen: extra?.argsOpen ?? false,
})

/** 报告里的目标名（脚本相对路径；已知素材用图名） */
/** 弹窗有哪几种：优先读后端按全部调用算的 `kinds`；旧载荷没有就从（可能被截断的）calls 推 */
export function dialogKinds(payload: GuiDialogPayload): { file: boolean; ask: boolean } {
  const kinds = payload.kinds ?? payload.calls.map((c) => c.kind)
  return { file: kinds.includes('file'), ask: kinds.includes('prompt') }
}

export const targetName = (entry: PrepEntry): string => {
  const t = entry.report?.target
  if (t?.script) return t.script
  if (t?.stem) return t.stem
  return 'id' in entry.target ? entry.target.id : entry.target.script
}

/** 位图库的名字（专有名词，两种语言相同；`rasterhint.LIBRARIES` 的显示名）。未知库（新后端、旧前端）原样显示后端给的名字 */
const RASTER_LIBRARY_NAME: Record<string, string> = {
  pillow: 'Pillow',
  opencv: 'OpenCV',
  imageio: 'imageio',
  skimage: 'scikit-image',
  plotly: 'Plotly',
  root: 'ROOT',
}

const BUSY = new Set(['running', 'awaiting_runtime_input', 'preparing_environment'])
/** 这几种状态下「运行」的前提是参数：必填参数没填齐就换成参数卡（任何卡都不说「可以运行」） */
const ARGS_GATED = new Set(['ready', 'gui_dialog', 'restarted', 'needs_args', 'args_changed', 'origin_unknown'])

/**
 * 失败卡：下面这些状态都是「跑不了 / 没出图 / 走不通」的结局（`prepView` 的 `state`）。智能修复的次按钮只摆在它们上面；
 * 还没跑（ready）、在跑（running…）、要先答的事（workdir / args / missing_data）、成功的卡都不在内。
 */
const FAILURE_STATES = new Set([
  'failed',
  'deps_failed',
  'deps_runtime',
  'deps_platform',
  'deps_stdlib',
  'deps_unknown',
  'env_blocked',
  'blocked',
  'file_ops_blocked',
  'no_figure',
  'no_figure_raster',
  'not_registered',
])
/** remedy 是 `ai_fix` / `connect_ai` 时，这几张卡的主按钮换成「使用你的 AI 来修复」（其余失败卡的主按钮是它自己的修法，照旧） */
const AI_PRIMARY_STATES = new Set(['failed', 'deps_failed', 'blocked', 'no_figure', 'not_registered'])

/**
 * 智能修复（ADR 0148）：在卡片之上读后端选好的 `report.remedy`，只翻译、不判定。
 * 装包 / 转写 / 选环境三种确定修法的主按钮就是卡片原来的那一颗（后端 remedy 与这些卡的来源是同一份事实）；
 * `ai_fix` / `connect_ai` 把主按钮换成 AI 修复；`feedback` 换成一句话 +「发送」；`switch_native` 不动（静默）。
 */
function withRemedy(view: PrepView, report: PreparationReport | null | undefined, ctx: PrepContext): PrepView {
  const remedy = report && isRemedy(report.remedy) ? report.remedy : null
  if (ctx.tutorial || !FAILURE_STATES.has(view.state)) return { ...view, remedy }
  let out: PrepView = { ...view, remedy }
  if (remedy && (remedy.kind === 'ai_fix' || remedy.kind === 'connect_ai') && AI_PRIMARY_STATES.has(view.state)) {
    out = { ...out, primary: { kind: 'ai_fix' }, secondary: null, primaryDisabled: false, ghost: 'later' }
  } else if (remedy?.kind === 'feedback') {
    out = {
      ...out,
      state: 'feedback',
      sentence: { key: 'feedback', values: view.sentence.values },
      primary: { kind: 'feedback' },
      secondary: null,
      primaryDisabled: false,
      ghost: 'later',
      slot: null,
    }
  }
  return { ...out, aiFix: remedy?.kind !== 'switch_native' && out.primary?.kind !== 'ai_fix' }
}

/**
 * AI 环境修复暂时做不了（`ai_fix_unavailable`，点过「使用你的 AI 来修复」之后）：同一张卡落成一句话
 * 「这个问题 AI 暂时还不会修」+ 主按钮「发送」。次按钮不再摆（刚说完不会修）；下一次运行出新尝试后卡片自然换回。
 */
export function withAiUnavailable(view: PrepView): PrepView {
  return {
    ...view,
    state: 'ai_unavailable',
    sentence: { key: 'aiUnavailable', values: view.sentence.values },
    primary: { kind: 'feedback' },
    secondary: null,
    primaryDisabled: false,
    ghost: 'later',
    slot: null,
    aiFix: false,
    tone: 'bad',
  }
}

export function prepView(entry: PrepEntry, ctx: PrepContext): PrepView {
  return withRemedy(prepViewBase(entry, ctx), entry.report, ctx)
}

function prepViewBase(entry: PrepEntry, ctx: PrepContext): PrepView {
  const script = targetName(entry)
  const v = { script }
  if (entry.failure) return view('check_failed', 'checkFailed', v, { kind: 'reopen', label: 'retry' }, { tone: 'bad' })
  const report = entry.report
  if (!report) {
    if (entry.connection === 'lost') return offline(v, ctx.engineExited)
    return view('checking', 'checking', v, null, { tone: 'busy', slot: 'progress' })
  }
  if (entry.connection === 'lost') return offline(v, ctx.engineExited)
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

/** 项目自带的环境还没体检（后端 `dependencies` 检查项的 `deferred`）：运行时才检查，跑不了会回到安装。 */
const projectEnvDeferred = (report: PreparationReport): boolean =>
  report.checks.some((c) => c.id === 'dependencies' && c.detail?.deferred === 'project_environment')

const offline = (v: Record<string, unknown>, engineExited?: boolean) =>
  // 不画活动条：断线 / 引擎已停时没有任何东西在推进，一条在扫的条是假的「还在动」
  view('offline', engineExited ? 'engineStopped' : 'offline', v, null, { tone: 'mute', step: 1 })

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
    case 'preparing_environment': {
      // 版本不合、正在自动另建合适版本的环境（ADR 0114 修订 2026-10-23）：一句话 + 进度 + 停止，不问用户
      const pc = report.python_change
      if (pc && pc.state === 'running') {
        const download = pc.base === 'private' && pc.download_bytes > 0
        return view(
          'python_change',
          download ? 'pythonChangeDownload' : 'pythonChange',
          { ...v, version: pc.to, size: Math.max(1, Math.round(pc.download_bytes / 1048576)) },
          { kind: 'background' },
          { ghost: has(report, 'cancel') ? 'stop' : null, tone: 'busy', slot: 'python_change' },
        )
      }
      return view('preparing', 'preparing', v, { kind: 'background' }, {
        ghost: has(report, 'cancel') ? 'stop' : null,
        tone: 'busy',
        slot: 'progress',
      })
    }
    case 'ready_to_run': {
      // 来源事实（Import Origin PR4/PR6，只有计数与闭集原因码）：有 import 没有可信的安装名时不说「可以运行了」——
      // 带预填的（后端数在 `confirmable` 里）让用户确认包名；其余只说来源还没法确定，主按钮仍是报告里的运行（改口成「仍然运行」）
      const origins = originFactsOf(report)
      // 可确认的来源优先于弹窗提示：弹窗提示每次检查都在，排在前面这类脚本就永远进不了确认包名；提示仍在详情里（SessionDetails 读 requirements）
      if (origins && origins.confirmable) {
        return view('origin_confirm', 'originConfirm', { ...v, count: origins.confirmable }, { kind: 'confirm_origins' }, {
          tone: 'mute',
        })
      }
      // 脚本会弹窗选文件 / 询问（后端静态识别，不阻塞）：先说这件事，主按钮仍是报告里的 run，只是改口成「仍然运行」。
      // 不另造「让助手改脚本」的入口——叠栈里没有现成的
      const dialog = find(report, 'gui_dialog')
      if (dialog) {
        const { file, ask } = dialogKinds(dialog.payload)
        // 选文件与询问都有：一句话涵盖两者，不塌成只讲文件
        const key = file && ask ? 'dialogBoth' : file ? 'dialogFile' : 'dialogAsk'
        return view('gui_dialog', key, v, runPrimary(report, 'runAnyway'), {
          slot: 'ready',
          tone: 'bad',
        })
      }
      if (origins) {
        return view('origin_unknown', 'originUnknown', { ...v, count: origins.count }, runPrimary(report, 'runAnyway'), {
          tone: 'mute',
        })
      }
      return view('ready', projectEnvDeferred(report) ? 'readyProject' : 'ready', v, runPrimary(report), {
        slot: 'ready',
      })
    }
    case 'completed':
      return completed(report, entry, ctx, script)
    case 'partial':
      // 部分捕获（#1134）：成功那几张照常可进入编辑，失败的图在标题里点名；不是 completed，也不是没出图
      if (report.outcome.kind === 'succeeded') return completed(report, entry, ctx, script)
      if (report.outcome.kind === 'execution_finished_no_figure') {
        const raster = report.no_figure_hint?.kind === 'raster_script' ? report.no_figure_hint : null
        if (raster) {
          // 脚本自己用位图库画成了图片：说出原因；后端确认那张图落在素材库盘点范围内（in_project）才给「打开素材库」，
          // 否则（绝对路径 / 沙盒 / 盘点不认的格式 / 判不出）素材库里找不到它，只给「知道了」。其余折叠进详情
          return view('no_figure_raster', 'noFigureRaster', { ...v, library: RASTER_LIBRARY_NAME[raster.library] ?? raster.library }, { kind: 'convert', script: report.target.script ?? '', library: raster.library, path: raster.path ?? null }, {
            // 次按钮保持现有：素材库里找得到就「打开素材库」，否则「只当图片用」（收起卡片）
            secondary: raster.in_project === true ? { kind: 'open_assets' } : { kind: 'dismiss' },
            ghost: null,
            tone: 'mute',
            step: 1,
          })
        }
        return view('no_figure', 'noFigure', v, { kind: 'dismiss' }, { ghost: null, tone: 'mute', step: 1 })
      }
      return view('not_registered', 'notRegistered', v, { kind: 'open_registry' }, { tone: 'bad', step: 1 })
    case 'cancelled':
      return view('cancelled', 'cancelled', v, runPrimary(report, 'runAgain'), { tone: 'mute', step: 1 })
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
  const failed = (report.failed_figures ?? []).map((f) => f.stem)
  if (failed.length > 0) {
    return view(
      'completed_partial',
      'completedPartial',
      { script, done: count, total: count + failed.length, names: listJoin(failed) },
      { kind: 'enter_edit', count },
      { tone: 'bad', slot: 'figures', step: 2 },
    )
  }
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
    // 报告里有「打开并运行」、而这次安装只进 Tavotto 自己的环境（授权合同的范围）：主按钮就是它，装完自动接着运行；
    // 要改用户自己环境的安装不在合同里，仍是逐次确认的「安装」
    const installAction = report.actions.find((a) => a.kind === 'prepare_dependencies')
    const prepare =
      installAction === undefined
        ? null
        : has(report, 'run_when_ready') && deps.origin !== 'runtime_missing' && !installAction.impact.modifies_user_environment
          ? act('run_when_ready', 'openRun')
          : act('prepare_dependencies', 'install')
    if (deps.origin === 'runtime_missing') {
      const p = (deps.payload ?? {}) as { module?: string; installable?: boolean; route?: string }
      if (!p.installable) {
        // 按后端去向翻译，不根据模块名再判平台；平台限制无法通过换 Python 补齐。
        const values = { script, module: p.module ?? '' }
        if (p.route === 'unsupported_platform') {
          return view('deps_platform', 'depsPlatform', values, null, { tone: 'bad' })
        }
        const pick: PrepPrimary = { kind: 'pick_environment' }
        if (p.route === 'stdlib_missing') {
          const builtin = report.environment?.kind === 'builtin'
          return view('deps_stdlib', builtin ? 'depsStdlib' : 'depsStdlibOther', values, pick)
        }
        return view('deps_unknown', 'depsUnknown', values, pick)
      }
      return view('deps_runtime', 'depsRuntime', { script, module: p.module ?? '' }, prepare, { slot: 'install' })
    }
    return view('deps', 'install', v, prepare, { slot: 'install' })
  }
  const missing = find(report, 'input_location')
  const code = report.outcome.code ?? ''
  if (report.outcome.kind === 'failed' && code === 'script_needs_arguments') {
    // 用户已经给了参数、脚本自己的解析器仍然拒绝（P05a：`params.argv_count` 来自后端本地错误详情）：别再说"要参数"
    const given = Number(report.result?.error?.params?.argv_count)
    return given > 0
      ? view('needs_args', 'argsRejected', { count: given }, { kind: 'reopen', label: 'continue' }, { slot: 'args', argsOpen: true })
      : view('needs_args', 'needsArgs', v, { kind: 'reopen', label: 'continue' }, { slot: 'args', argsOpen: true })
  }
  // safe 守卫拦下了脚本的文件操作、脚本随后又读了它（P01a）：重试得到同样的结果，没有「再试一次」。
  // 桌面用户发起的运行里后端会在同一会话里自动换成保留文件语义的执行再跑一次（ADR 0127），所以走到这里只有一种情况：
  // 后端没有回退（项目设成只用安全运行 / 运维关了回退 / 老后端）——如实说为什么，不替用户换环境重跑，没有主按钮。
  // 原因来自账本里的闭集理由码（`declined`），前端不另判
  if (report.outcome.kind === 'failed' && code === 'safe_file_operation_blocked') {
    const declined = currentAttempt(report)?.declined
    const key =
      declined === 'policy_safe_only'
        ? 'fileOpsBlockedSafeOnly'
        : declined === 'fallback_disabled'
          ? 'fileOpsBlockedOff'
          : 'fileOpsBlocked'
    return view('file_ops_blocked', key, v, null, { tone: 'bad', step: 1 })
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
      if (code === 'script_needs_input' || code === 'script_input_timeout') {
        // 脚本中途要输入而这一次没有人能答：再运行一次，提问会出现在卡片里（口令类说清不会被记住）
        // 口令：理由是 secret_required，或读取方式本身是 getpass（首问无人应答 / 超时时理由是别的）
        const params = report.result?.error?.params
        const secret = params?.reason === 'secret_required' || params?.input_kind === 'getpass'
        return view('input_unanswered', secret ? 'inputUnansweredSecret' : 'inputUnanswered', v, runPrimary(report, 'retry') ?? fallback, {
          tone: 'bad',
          step: 1,
        })
      }
      // 换成原样运行之后又出错了：一句话说明已经换过（脚本自己的错误原文与运行记录在详情里），主按钮仍是再试一次
      return view('failed', fellBack(report) ? 'failedAfterFallback' : 'failed', v, runPrimary(report, 'retry') ?? fallback, {
        tone: 'bad',
        step: 1,
      })
    case 'cancelled':
      return view('cancelled', 'depsCancelled', v, fallback, { tone: 'mute' })
    default:
      return view('attention', 'attention', v, runPrimary(report) ?? fallback)
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
    case 'ready_to_run': {
      const origins = originFactsOf(report)
      if (origins) return { key: origins.confirmable ? 'originConfirm' : 'originUnknown', values: {} }
      return { key: projectEnvDeferred(report) ? 'readyProject' : 'ready', values: {} }
    }
    case 'completed':
      return report.outcome.kind === 'succeeded'
        ? { key: 'captured', values: { count: (report.captured ?? []).length } }
        : { key: 'static', values: {} }
    case 'partial':
      if (report.outcome.kind === 'succeeded') {
        const done = (report.captured ?? []).length
        return { key: 'capturedPartial', values: { done, total: done + (report.failed_figures ?? []).length } }
      }
      return { key: report.outcome.kind === 'execution_finished_no_figure' ? 'noFigure' : 'attention', values: {} }
    case 'cancelled':
      return { key: 'cancelled', values: {} }
    default:
      return { key: 'attention', values: {} }
  }
}

/**
 * 这次运行跑完并捕获到了图：`completed`（全部拿到），或 `partial` + `succeeded`（部分捕获：成功那几张照常可用，失败的在
 * `failed_figures` 里——后端 #1134 起不再把它投影成 completed）。其余 partial（没出图 / 登记被拒）不算
 */
export const finishedWithFigures = (report: PreparationReport | null | undefined): report is PreparationReport =>
  !!report && (report.phase === 'completed' || report.phase === 'partial') && report.outcome.kind === 'succeeded'

/**
 * safe → native 静默回退**成功**之后，详情里补的那一行（默认句子与主按钮不变，ADR 0127 裁决静默）。
 * 事实全在后端：账本（`attempt_history`）里本次 run 最后一次尝试是 native 且相对上一次换了执行语义，且这次捕获到的图确实是
 * native 的；`noWriteback` 读描述符自己的 `can_writeback_source`，不在前端推。老后端没有账本 = 不说。
 */
export function nativeFallbackNote(report: PreparationReport | null): { noWriteback: boolean } | null {
  if (!finishedWithFigures(report)) return null
  const history = runAttempts(report)
  if (history.length === 0) return null
  const run = Math.max(...history.map((a) => a.run))
  const last = history.filter((a) => a.run === run).at(-1)
  if (!last || last.profile !== 'native' || !last.changed.includes('profile')) return null
  const native = (report.captured ?? []).filter((c) => c.execution_profile === 'native')
  if (native.length === 0) return null
  return { noWriteback: native.some((c) => !c.can_writeback_source) }
}
