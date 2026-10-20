/**
 * 教程与示例项目的**入口动作**——项目选择器、Help、命令面板、设置、画布空态都调这里，
 * 状态逻辑只有这一份（Prompt 21 §十）。两件事，互不牵连：
 *
 * ```text
 * runTutorialEntry()   三步原生教程（ADR 0136）：只改 onboardingStore、打开教程层——不开项目、不跑脚本、不发请求
 * markDemoStep()       教程里一步的任务真的做到了：记完成 + 遥测（只记一次）
 * finishTutorial()     完成页「开始使用」：记完成、收起教程层
 * openSampleProject()  示例项目（ADR 0039）：POST /api/tutorial/open → 认领项目 → 装示例画布；onboarding 一个字段都不碰
 * resetTutorial()      「重置示例项目」：确认 → POST /api/tutorial/reset → 忘掉本机那格 autosave → 重新装
 * tutorialEntry()      入口该显示「开始 / 继续 / 重新开始」哪一个（纯读）
 * ```
 *
 * 示例项目的三条硬约束（ADR 0039）：
 *   * 一切只从 `POST /api/tutorial/open` 的响应与 `tutorial_meta.json` 拿，
 *     不读仓库根 `examples/`（T-105）；
 *   * 示例画布的 documentId **必须**是 `metadata.document_id`（T-106）；
 *   * 打开不执行脚本——这里只调既有的项目打开链路（T-107）。
 *
 * 三步教程的硬约束（ADR 0136）：完成教程**不调用任何后端接口**——没有 Python、没有依赖准备、
 * 没有联网；那张图是 `lib/onboarding/demoFigure` 的受控演示，不是图内编辑。
 */
import { create } from 'zustand'
import { captureTelemetry } from '@/lib/telemetry'
import { msg, type UiMessage } from '@/i18n'
import {
  ApiError,
  backendErrorMsg,
  fetchLayout,
  fetchTutorialStatus,
  openTutorialApi,
  resetTutorialApi,
  type TutorialMetadata,
  type TutorialOpenResult,
  type TutorialStatus,
} from '@/lib/api'
import {
  forgetLocalDocument,
  loadAutosavedDocument,
  resumeAutosave,
  suspendAutosaveFor,
  useDocumentStore,
} from '@/store/documentStore'
import { ONBOARDING_FLOW_VERSION, type OnboardingStatus, useOnboardingStore } from '@/store/onboardingStore'
import type { StepId } from './stepIds'
import { useProjectStore, type ProjectState } from '@/store/projectStore'
import { useUiStore, askConfirm } from '@/store/uiStore'
import { migrateToProject } from '@/types/document'

export type TutorialFailure =
  /** 资源缺失 / 损坏：说「重新安装」 */
  | 'unavailable'
  /** 复制 / 打开失败（后端 5xx / 400） */
  | 'open_failed'
  /** 文件被占用（409 tutorial_locked） */
  | 'locked'
  /** 确认之后示例项目里的另存排版变了（409 tutorial_layouts_changed）：重置未执行 */
  | 'layouts_changed'
  /** 画布文档读不出来（schema 不兼容 / 缺文件） */
  | 'document_failed'
  /** 用户在确认框里取消 */
  | 'cancelled'
  /** 宿主没提供 Tutorial API（embedded） */
  | 'no_api'

export type TutorialOutcome =
  /** `opened` = 打开了示例项目；`restarted` = 重置了示例项目并重新打开 */
  | { ok: true; kind: 'restarted' | 'opened' }
  | { ok: false; reason: TutorialFailure; message: UiMessage }

interface TutorialState {
  /** `GET /api/tutorial` 的最近一次结论；null = 还没问过 */
  status: TutorialStatus | null
  /** 当前拿到的元数据（open / reset 的响应里那份；启动时也可能来自 GET） */
  meta: TutorialMetadata | null
  busy: 'open' | 'reset' | 'status' | null
  /** 最近一次失败；入口按它显示下一步 */
  failure: { reason: TutorialFailure; message: UiMessage } | null
}

export const useTutorialStore = create<TutorialState>(() => ({
  status: null,
  meta: null,
  busy: null,
  failure: null,
}))

/** 只读探测：资源可不可用。失败（比如宿主没有这个端点）记成 `no_api`。 */
export async function loadTutorialStatus(): Promise<TutorialStatus | null> {
  useTutorialStore.setState({ busy: 'status' })
  try {
    const status = await fetchTutorialStatus()
    useTutorialStore.setState({
      status,
      meta: status.metadata ?? useTutorialStore.getState().meta,
      busy: null,
    })
    return status
  } catch (e) {
    useTutorialStore.setState({
      status: null,
      busy: null,
      failure: { reason: 'no_api', message: failureMessage('no_api', e) },
    })
    return null
  }
}

/**
 * 按需探测：只在需要判断「示例项目副本在不在」的界面（设置页 › 学习、命令面板）打开时调，
 * 已知副本在、或正在探测就不再问。**不放在选择器 / 主页挂载时**——教程层打开期间不许发任何
 * 教程资源请求（P0，e2e `tutorial.spec` 的 FORBIDDEN 含 `/api/tutorial`）。
 */
export function ensureTutorialStatus(): void {
  const { status, busy } = useTutorialStore.getState()
  // 只信「副本在」的缓存：「不在」会过时（之后打开示例项目就建出了副本，那次打开不回写这份状态），
  // 照它判会把「重置示例项目」藏起来——所以没有正面结论就再问一次（只读 GET，很便宜）
  if (busy || status?.copy?.exists === true) return
  void loadTutorialStatus()
}

function failureMessage(reason: TutorialFailure, e?: unknown): UiMessage {
  if (
    reason === 'open_failed' ||
    reason === 'locked' ||
    reason === 'unavailable' ||
    reason === 'layouts_changed'
  ) {
    // 后端带 code 的错误按 code 翻（`errors:backend.tutorial_*`），没有就原文透出
    if (e instanceof ApiError && typeof e.body.code === 'string') return backendErrorMsg(e)
  }
  return msg(`onboarding.failure.${reason}`, undefined, 'dialogs')
}

function classify(e: unknown): TutorialFailure {
  if (e instanceof ApiError) {
    const code = typeof e.body.code === 'string' ? e.body.code : ''
    if (code === 'tutorial_locked') return 'locked'
    if (code === 'tutorial_layouts_changed') return 'layouts_changed'
    if (code === 'tutorial_resources_missing' || code === 'tutorial_resources_invalid') {
      return 'unavailable'
    }
    if (e.status === 404) return 'no_api'
    return 'open_failed'
  }
  return 'open_failed'
}

function fail(reason: TutorialFailure, e?: unknown): TutorialOutcome {
  const message = failureMessage(reason, e)
  useTutorialStore.setState({ busy: null, failure: { reason, message } })
  return { ok: false, reason, message }
}

/**
 * 把教程画布装进 documentStore。
 *
 * 普通打开**保留进度**：本机 / 磁盘的自动保存槽位里有就用它（`readAutosaveDoc`
 * 自己裁决哪份新）；没有才读项目里的 `tavottofile/<document_name>.json`。
 * 重置后槽位已经被后端清掉、本机那格也被 `forgetLocalDocument` 忘掉，于是
 * 自然走第二条路拿到干净的画布。
 *
 * documentId 一律是 `meta.document_id`（T-106）。
 */
async function loadTutorialDocument(meta: TutorialMetadata): Promise<boolean> {
  const id = meta.document_id
  // 槽位里有就切过去，读盘带回来的恢复副本 / schema 太新的提示由它挂上（切换之后）
  const saved = await loadAutosavedDocument(id)
  if (saved.loaded) return true
  let raw: unknown
  try {
    raw = (await fetchLayout(meta.document_name)).doc
  } catch {
    return false
  }
  const pd = migrateToProject(raw)
  if (!pd) return false
  const ok = await useDocumentStore.getState().switchDocument(pd, id)
  // 槽位读不了（schema 太新）才退到项目里的画布文件：那句提示同样要说，在这次切换之后挂上
  if (ok && saved.notice) useDocumentStore.setState({ docNotice: saved.notice })
  return ok
}

const inTutorialProject = (projectId: string | undefined | null) =>
  !!projectId && useProjectStore.getState().project?.id === projectId

/**
 * 教程是从哪个入口开始的（遥测 `tutorial_started.source` 的闭集）。
 * `picker` 项目选择页；`help` 顶栏「更多」；`settings` 设置页；`palette` 命令面板。
 */
export type TutorialEntrySource = 'picker' | 'help' | 'settings' | 'palette' | 'canvas'

/**
 * 正在改状态的动作（open / reset）互斥；**只读的状态探测（`status`）不算**。项目选择器一挂载就
 * 发 `GET /api/tutorial` 探资源，用户紧接着点「用示例了解 Tavotto」——探测还在飞就把点击判成
 * 「打不开」，用户看到的是一条无中生有的红字（U08 把后端第一次 probe 变慢了 100 ms，e2e 就撞上）。
 */
/**
 * 教程自己在开 / 重置，或者**项目正在切换**：这时不再发起第二次。教程也是一次切项目
 * （`adoptOpenedProject`），切换中再点只会排上一次用户没想要的完整换代（Codex #550）。
 * 所有入口（画布空态、命令面板、帮助菜单、设置、Picker）都经这里，拦一处就够。
 */
function mutating(): boolean {
  const b = useTutorialStore.getState().busy
  return b === 'open' || b === 'reset' || useProjectStore.getState().switching
}

/**
 * 打开示例项目（同一条认领链路、同一份示例画布），**不动 onboarding 状态**——主页老手版的
 * 「使用示例脚本试试看」用它。走完过教程的人点一下示例不该被重新拉进引导，更不该因此把
 * 「已完成」改回「进行中」：主页的新手 / 老手两版正是按这个状态分的（`homeVariant`），
 * 派生出来的一次点击不许覆盖它。
 */
async function openSample(): Promise<TutorialOutcome> {
  if (mutating()) return fail('open_failed')
  // 请求 + 认领是一次切换：请求在路上时别的打开就得排在它后面（Codex #550）
  return useProjectStore.getState().switchTransaction((adopt) => openSampleNow(adopt))
}

async function openSampleNow(adopt: AdoptProject): Promise<TutorialOutcome> {
  // 示例项目开着、却不知道哪份是示例画布（升级后重启：元数据还没取到，三步教程也不再在
  // onboarding 里记它的 id）：先取齐元数据再开——挂起自动保存只能挂在**真正的**示例画布上，
  // 挂错了（用户正在示例项目里改另一份排版）切文档时它的未存改动会被丢掉；不挂又会让认领把
  // 旧排版写回固定槽位。取不到就不开，按失败返回。**在切换事务里取**：取的那段时间里用户
  // 另开的项目会排在这次后面，不会先落地、再被这次过时的点击切回示例项目
  if (useProjectStore.getState().project?.tutorial === true && knownTutorialDocumentId() === null) {
    await loadTutorialStatus()
    if (knownTutorialDocumentId() === null) return fail('unavailable')
  }
  useTutorialStore.setState({ busy: 'open', failure: null })
  // 手里这份就是教程画布时先把它从自动保存链路上摘下来（与重置同一条路）。open 可能换
  // 副本（资源升级换了目录 = 新项目 id），换副本要走认领，而认领的第一步就是把当前文档
  // 冲刷落盘——那一刻项目 id 已经是新副本的，旧布局会在同一个教程 documentId 下落回
  // 本机 / 磁盘槽位，下面 forgetLocalDocument 清掉的那格当场被建回来，装回来的还是它。
  // 换到教程画布时 switchDocument 自动恢复；没换文档（同一副本再开）/ 没做成就手动接回。
  const suspended = currentTutorialDocumentId()
  if (suspended) suspendAutosaveFor(suspended)
  let res: TutorialOpenResult
  try {
    res = await openTutorialApi({ default: true })
  } catch (e) {
    if (suspended) resumeAutosave()
    return fail(classify(e), e)
  }
  // 后端刚建了一份全新的副本（首次 / 资源升级换了目录）并清了磁盘上的槽位；
  // 本机这格不忘掉的话 readAutosaveDoc 会把上一份副本的排版推回来——与重置同一条路
  if (res.created) forgetLocalDocument(res.tutorial.document_id)
  const out = await landTutorial(res, 'open', adopt)
  if (suspended) resumeAutosave()
  return out
}

/**
 * 当前文档是教程画布时给它的 id，否则 null。教程画布的 documentId 固定为
 * `metadata.document_id`（T-106）：元数据重启后可能还没取到（onboarding 已完成就没人去取），
 * onboarding 记着的那份是第二个出处。
 */
function currentTutorialDocumentId(): string | null {
  const current = useDocumentStore.getState().documentId
  const known = knownTutorialDocumentId()
  return known !== null && current === known ? current : null
}

/**
 * 示例画布固定的 documentId（元数据，其次 onboarding 记着的旧值）；都没有回 null。三步教程
 * （ADR 0136）不再写 onboarding 那份，所以升级后重启、元数据还没取到时是 null——调用方
 * （`openSample`）先取齐元数据，**不拿「当前项目是示例项目」去猜**哪份文档是示例画布。
 */
function knownTutorialDocumentId(): string | null {
  return useTutorialStore.getState().meta?.document_id ?? useOnboardingStore.getState().tutorialDocumentId
}

/**
 * 「重置示例项目」（换回干净副本）。先确认——示例项目里如果有用户另存的画布文件，确认框会
 * 点名列出来（重置换的是整个副本目录，它们会一起没）。三步教程的进度不归它管（ADR 0136）。
 */
export async function resetTutorial(): Promise<TutorialOutcome> {
  if (mutating()) return fail('open_failed')
  // 元数据可能还没取到（重启后示例项目开着、设置页 / 命令面板的探测还在路上）：先取齐再确认。
  // 拿着 null 往下走，确认框会漏掉要被删的另存画布，重置窗口里也不会挂起示例画布的自动保存
  const meta = useTutorialStore.getState().meta ?? (await loadTutorialStatus())?.metadata ?? null
  // 现取失败就不弹确认：确认框不能假装「没有另存排版」去让用户放心点删除
  let extras: string[]
  let inventory: string[]
  try {
    ;({ extras, inventory } = await savedLayoutsInTutorial(meta))
  } catch (e) {
    return fail(classify(e), e)
  }
  const ok = await askConfirm({
    title: msg('onboarding.reset.title', undefined, 'dialogs'),
    body: extras.length
      ? msg('onboarding.reset.bodyWithLayouts', { names: extras.join(', ') }, 'dialogs')
      : msg('onboarding.reset.body', undefined, 'dialogs'),
    confirmLabel: msg('onboarding.reset.confirm', undefined, 'dialogs'),
    danger: true,
  })
  if (!ok) return { ok: false, reason: 'cancelled', message: failureMessage('cancelled') }
  // 确认框等用户的那段不算切换；点了确认之后「重置请求 + 认领」是一次切换
  if (mutating()) return fail('open_failed')
  return useProjectStore.getState().switchTransaction((adopt) => resetTutorialNow(adopt, meta, inventory))
}

async function resetTutorialNow(
  adopt: AdoptProject,
  meta: TutorialMetadata | null,
  inventory: string[],
): Promise<TutorialOutcome> {
  useTutorialStore.setState({ busy: 'reset', failure: null })
  // 先把手里这份教程画布从自动保存链路上摘下来，再让后端清槽位、换目录：重置窗口里
  // 的防抖写盘 / 派生同步（项目重开会推 registry.changed / assets.changed）否则会把
  // 拖过 / 改过的旧文档写回刚清掉的那一格，随后装回来的就是旧的（Windows 桌面腿抓到）。
  // 换到干净画布时 switchDocument 自动恢复；重置没做成就手动接回。
  const current = useDocumentStore.getState().documentId
  const suspended = meta !== null && current === meta.document_id
  if (suspended) suspendAutosaveFor(current)
  let res: TutorialOpenResult
  try {
    res = await resetTutorialApi({ default: true, expected_layouts: inventory })
  } catch (e) {
    if (suspended) resumeAutosave()
    return fail(classify(e), e)
  }
  // 后端只清了磁盘那格；本机这格不忘掉的话 readAutosaveDoc 会把旧进度推回去
  forgetLocalDocument(res.tutorial.document_id)
  const out = await landTutorial(res, 'reset', adopt)
  if (!out.ok) resumeAutosave()
  return out
}

/**
 * 重置会一并删掉的、用户在示例项目里另存的画布名（不含教程画布本身）。**一律重置前现取**后端对
 * 示例副本的清点 `copy.layouts`：不读 `/api/layouts`——它是当前项目的列表，示例项目开着时还会
 * 混进数据目录 `layouts/` 里与示例无关、重置也不删的画布；示例项目不开着时则是别的项目。
 * 不用缓存的旧状态（用户可能刚另存过）。「不知道」一律抛，由调用方按失败路径返回。
 */
async function savedLayoutsInTutorial(
  meta: TutorialMetadata | null,
): Promise<{ extras: string[]; inventory: string[] }> {
  // 元数据（教程画布名）取不到：分不清哪份是教程画布、哪份是另存的——「不知道」不是「没有」
  if (!meta) throw new Error('tutorial metadata unavailable')
  const status = await fetchTutorialStatus()
  const copy = status.copy
  if (copy && !copy.exists) return { extras: [], inventory: [] } // 副本不在：没有东西会被删
  // 清点不全（null）或后端没给：不知道有没有另存排版——按失败处理，不弹「没有另存」的确认
  if (!Array.isArray(copy?.layouts)) throw new Error('tutorial layout inventory unavailable')
  // `inventory` 是后端原样的清单（后端已不含教程画布），随重置请求发回：后端持锁后重核，
  // 确认之后别处又另存了一份就拒绝重置，不会静默删掉用户没看到的排版
  return { extras: copy.layouts.filter((n) => n !== meta.document_name), inventory: copy.layouts }
}

/** 事务里用的认领：直接执行、不再排切换队列（见 `projectStore.switchTransaction`） */
type AdoptProject = ProjectState['adoptOpenedProject']

async function landTutorial(
  res: TutorialOpenResult,
  how: 'reset' | 'open',
  adopt: AdoptProject,
): Promise<TutorialOutcome> {
  const meta = res.tutorial
  const projectId = res.project.id ?? null
  useTutorialStore.setState({ meta })
  const proj = useProjectStore.getState()
  const alreadyHere = proj.phase === 'open' && inTutorialProject(projectId)
  let docOk = true
  if (alreadyHere) {
    // 同一个项目绝不再走一遍认领（那会把用户正在排的版换成空白文档——与
    // `lib/openRequest` 同一条纪律）；只在文档不是示例画布时换过去
    if (useDocumentStore.getState().documentId !== meta.document_id || how === 'reset') {
      docOk = await loadTutorialDocument(meta)
    }
  } else {
    try {
      await adopt(res.project, {
        // 返回值就是「准备成功了没有」：认领据此确认（失败 → 门停在「需要用户处理」，初始空白不标 ready）
        prepareDocument: async () => {
          docOk = await loadTutorialDocument(meta)
          return docOk
        },
      })
    } catch (e) {
      return fail('open_failed', e)
    }
  }
  if (!docOk) return fail('document_failed')
  useTutorialStore.setState({ busy: null, failure: null })
  const kind = how === 'reset' ? 'restarted' : 'opened'
  useUiStore.getState().setStatus(msg(`onboarding.landed.${kind}`, undefined, 'dialogs'), 'done')
  return { ok: true, kind }
}

/* ------------------------------ 四个入口共用 ------------------------------ */

export type TutorialEntryKind = 'start' | 'resume' | 'restart'

/**
 * 入口该说什么。**纯读**：
 *   * 进行中 / 暂停 → 「继续教程」；
 *   * 已完成 / 已跳过 → 「再看一遍教程」（三步从头；与「重置示例项目」是两件事，后者换的是
 *     磁盘上的示例项目副本）；
 *   * 没开始过 → 「开始教程」。
 */
export function tutorialEntry(
  status: OnboardingStatus = useOnboardingStore.getState().status,
): TutorialEntryKind {
  const s = status
  if (s === 'active' || s === 'paused') return 'resume'
  if (s === 'completed' || s === 'skipped') return 'restart'
  return 'start'
}

/**
 * 入口点下去：打开三步原生教程。进行中 / 暂停的接着上次那一步，已完成 / 已跳过 / 没开始的
 * 从头开始（「再看一遍」就是从头，演示改动清空）。**不打开项目、不发请求**。
 *
 * 遥测只记**真的开始了**的那一次；继续不是开始。
 */
export function runTutorialEntry(source: TutorialEntrySource): 'started' | 'resumed' {
  const kind = useOnboardingStore.getState().openTutorial()
  if (kind === 'started') {
    captureTelemetry('tutorial_started', { source, tutorial_version: ONBOARDING_FLOW_VERSION })
  }
  return kind
}

/**
 * 教程里一步的任务**真的做到了**（拖放成功落进投放区 / 图例真的挪开了 / 字号真的变大了）——
 * 由教程界面在那次交互成功的那一刻调，不由「下一步」按钮调。同一步只记一次遥测：
 * 返回上一步再做一遍不是又完成了一步。
 */
export function markDemoStep(id: Exclude<StepId, 'done'>): void {
  const ob = useOnboardingStore.getState()
  if (ob.status !== 'active' || ob.completedSteps.includes(id)) return
  ob.markStep(id)
  captureTelemetry('tutorial_step_completed', { step_id: id, tutorial_version: ONBOARDING_FLOW_VERSION })
}

/** 完成页「开始使用 Tavotto」：记完成、收起教程层，落回此刻底下的主页 / 工作台 */
export function finishTutorial(): void {
  const ob = useOnboardingStore.getState()
  if (ob.status !== 'active' || ob.currentStep !== 'done') return
  ob.markStep('done')
  captureTelemetry('tutorial_step_completed', { step_id: 'done', tutorial_version: ONBOARDING_FLOW_VERSION })
  ob.complete()
  captureTelemetry('tutorial_completed', { tutorial_version: ONBOARDING_FLOW_VERSION })
}

/** 顶栏「跳过教程」：跳过不是完成（不记遥测），教程层收起 */
export function skipTutorial(): void {
  useOnboardingStore.getState().skip()
}

/** 完成页「重新体验」：从第一步重来，演示改动清空；是一次新的开始，记 `tutorial_started`（来源 `replay`） */
export function restartTutorial(): void {
  useOnboardingStore.getState().start()
  captureTelemetry('tutorial_started', { source: 'replay', tutorial_version: ONBOARDING_FLOW_VERSION })
}

/**
 * 欢迎页「直接进入工作台」：没开始过的只是收起欢迎页（状态不动，入口还在）；从第一步退回
 * 欢迎页、再选进工作台的，是明确不走这一轮了——记成跳过，下次启动不再自己弹出来。
 */
export function leaveFromWelcome(): void {
  const ob = useOnboardingStore.getState()
  if (ob.status === 'active' || ob.status === 'paused') ob.skip()
  else ob.closeSurface()
}

/** 主页老手版「使用示例脚本试试看」：打开示例项目，不带引导、不改 onboarding 状态 */
export async function openSampleProject(): Promise<TutorialOutcome> {
  const out = await openSample()
  if (!out.ok) useUiStore.getState().setStatus(out.message, 'error')
  return out
}

/**
 * 示例项目的副本在不在（「重置示例项目」只在有东西可重置时出现）：后端探测说有，或者它在
 * 最近列表里 / 此刻就开着。三步教程不碰它（ADR 0136）。
 */
export function hasSampleProject(
  tutorial: Pick<TutorialState, 'status'> = useTutorialStore.getState(),
  projects: Pick<ProjectState, 'recent' | 'project'> = useProjectStore.getState(),
): boolean {
  return (
    tutorial.status?.copy?.exists === true ||
    projects.project?.tutorial === true ||
    projects.recent.some((r) => r.tutorial === true)
  )
}

export type HomeVariant = 'newcomer' | 'returning'

/**
 * 主页显示哪一版。**纯读 onboarding 状态，不另设标志、不写任何东西**：
 *   * 已完成 / 已跳过（用户明确走完或明确说了「跳过教程」）→ 老手版；
 *   * 没开始 / 进行中 / 中途暂停 → 新手版（暂停的人主按钮是「继续」，见 `tutorialEntry`）。
 *
 * 状态只在 `store/onboardingStore`（`tavotto.onboarding` 那一格）：清掉本机数据 = 回到
 * 没开始 = 新手版。各窗口各读各的那份
 * store（启动时从 localStorage 读），另一个窗口里刚走完教程，这个窗口下次启动才换版。
 */
export function homeVariant(
  status: OnboardingStatus = useOnboardingStore.getState().status,
): HomeVariant {
  return status === 'completed' || status === 'skipped' ? 'returning' : 'newcomer'
}

/** 「重置提示」：所有一次性情境提示重新可见 */
export function resetHints(): void {
  useOnboardingStore.getState().resetHints()
  useUiStore.getState().setStatus(msg('onboarding.hintsReset', undefined, 'dialogs'), 'done')
}
