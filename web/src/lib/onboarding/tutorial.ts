/**
 * 教程的**入口动作**——项目选择器、Help、命令面板、设置四个入口都调这里，
 * 状态逻辑只有这一份（Prompt 21 §十）。
 *
 * ```text
 * startTutorial()   GET/POST /api/tutorial/open → 认领项目 → 装教程画布 → 开始 / 继续 onboarding
 * resetTutorial()   「重置教程项目」：确认 → POST /api/tutorial/reset → 忘掉本机那格 autosave → 重新装 → 从头开始
 * tutorialEntry()   四个入口该显示「开始 / 继续 / 重新开始」哪一个（纯读）
 * ```
 *
 * 三条硬约束（ADR 0039 交给 21 的）：
 *   * 一切只从 `POST /api/tutorial/open` 的响应与 `tutorial_meta.json` 拿，
 *     不读仓库根 `examples/`（T-105）；
 *   * 教程画布的 documentId **必须**是 `metadata.document_id`（T-106）；
 *   * 打开不执行脚本——这里只调既有的项目打开链路（T-107）。
 */
import { create } from 'zustand'
import { captureTelemetry } from '@/lib/telemetry'
import { msg, type UiMessage } from '@/i18n'
import {
  ApiError,
  backendErrorMsg,
  fetchLayout,
  fetchLayoutNames,
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
  /** 画布文档读不出来（schema 不兼容 / 缺文件） */
  | 'document_failed'
  /** 用户在确认框里取消 */
  | 'cancelled'
  /** 宿主没提供 Tutorial API（embedded） */
  | 'no_api'

export type TutorialOutcome =
  | { ok: true; kind: 'started' | 'resumed' | 'restarted' }
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

function failureMessage(reason: TutorialFailure, e?: unknown): UiMessage {
  if (reason === 'open_failed' || reason === 'locked' || reason === 'unavailable') {
    // 后端带 code 的错误按 code 翻（`errors:backend.tutorial_*`），没有就原文透出
    if (e instanceof ApiError && typeof e.body.code === 'string') return backendErrorMsg(e)
  }
  return msg(`onboarding.failure.${reason}`, undefined, 'dialogs')
}

function classify(e: unknown): TutorialFailure {
  if (e instanceof ApiError) {
    const code = typeof e.body.code === 'string' ? e.body.code : ''
    if (code === 'tutorial_locked') return 'locked'
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

/**
 * 把教程画布装回来（给引擎用：切回教程项目 / 重启后文档不是教程画布时）。
 * 元数据不在就先取一次；取不到就什么都不做（入口按钮那条路会说清失败）。
 */
export async function ensureTutorialDocument(): Promise<boolean> {
  let meta = useTutorialStore.getState().meta
  if (!meta) meta = (await loadTutorialStatus())?.metadata ?? null
  if (!meta) return false
  if (useDocumentStore.getState().documentId === meta.document_id) return true
  return loadTutorialDocument(meta)
}

const inTutorialProject = (projectId: string | undefined | null) =>
  !!projectId && useProjectStore.getState().project?.id === projectId

/**
 * 「用示例了解 Tavotto」。
 *
 * 1. `POST /api/tutorial/open`（缺文件补齐、副本不在就建）；
 * 2. 走既有的项目认领链路（与打开任何项目完全相同）；
 * 3. 装教程画布；
 * 4. 上次暂停在这份教程里就继续，否则从头开始。
 */
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

export async function startTutorial(source?: TutorialEntrySource): Promise<TutorialOutcome> {
  if (mutating()) return fail('open_failed')
  // 请求 + 认领是一次切换：请求在路上时别的打开就得排在它后面（Codex #550）
  return useProjectStore
    .getState()
    .switchTransaction((adopt) => startTutorialNow(adopt, source))
}

async function startTutorialNow(
  adopt: AdoptProject,
  source?: TutorialEntrySource,
): Promise<TutorialOutcome> {
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
  const out = await landTutorial(res, 'start', adopt, source)
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
  const known =
    useTutorialStore.getState().meta?.document_id ?? useOnboardingStore.getState().tutorialDocumentId
  return known !== null && current === known ? current : null
}

/**
 * 「重置教程项目」（换回干净副本 + 进度从头）。先确认——教程项目里如果有用户另存的画布文件，确认框会
 * 点名列出来（重置换的是整个副本目录，它们会一起没）。
 */
export async function resetTutorial(): Promise<TutorialOutcome> {
  if (mutating()) return fail('open_failed')
  const meta = useTutorialStore.getState().meta
  const extras = await savedLayoutsInTutorial(meta)
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
  return useProjectStore.getState().switchTransaction((adopt) => resetTutorialNow(adopt, meta))
}

async function resetTutorialNow(
  adopt: AdoptProject,
  meta: TutorialMetadata | null,
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
    res = await resetTutorialApi({ default: true })
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

/** 教程项目里除教程画布之外、用户自己另存的画布文件名 */
async function savedLayoutsInTutorial(meta: TutorialMetadata | null): Promise<string[]> {
  if (!meta || !inTutorialProject(useOnboardingStore.getState().tutorialProjectId)) return []
  try {
    const names = await fetchLayoutNames()
    return names.filter((n) => n !== meta.document_name)
  } catch {
    return []
  }
}

/** 事务里用的认领：直接执行、不再排切换队列（见 `projectStore.switchTransaction`） */
type AdoptProject = ProjectState['adoptOpenedProject']

async function landTutorial(
  res: TutorialOpenResult,
  how: 'start' | 'reset',
  adopt: AdoptProject,
  source?: TutorialEntrySource,
): Promise<TutorialOutcome> {
  const meta = res.tutorial
  const projectId = res.project.id ?? null
  useTutorialStore.setState({ meta })
  const proj = useProjectStore.getState()
  const alreadyHere = proj.phase === 'open' && inTutorialProject(projectId)
  let docOk = true
  if (alreadyHere) {
    // 同一个项目绝不再走一遍认领（那会把用户正在排的版换成空白文档——与
    // `lib/openRequest` 同一条纪律）；只在文档不是教程画布时换过去
    if (useDocumentStore.getState().documentId !== meta.document_id || how === 'reset') {
      docOk = await loadTutorialDocument(meta)
    }
  } else {
    try {
      await adopt(res.project, {
        prepareDocument: async () => {
          docOk = await loadTutorialDocument(meta)
        },
      })
    } catch (e) {
      return fail('open_failed', e)
    }
  }
  if (!docOk) return fail('document_failed')

  const ob = useOnboardingStore.getState()
  const sameRun =
    ob.tutorialProjectId === projectId && ob.tutorialDocumentId === meta.document_id
  let kind: 'started' | 'resumed' | 'restarted'
  if (how === 'reset') {
    ob.start({ projectId, documentId: meta.document_id })
    kind = 'restarted'
  } else if (sameRun && ob.status === 'paused') {
    ob.resume()
    kind = 'resumed'
  } else if (sameRun && ob.status === 'active') {
    kind = 'resumed'
  } else {
    ob.start({ projectId, documentId: meta.document_id })
    kind = 'started'
  }
  useTutorialStore.setState({ busy: null, failure: null })
  useUiStore.getState().setStatus(msg(`onboarding.landed.${kind}`, undefined, 'dialogs'))
  // 遥测只记**真的开始了**的那一次（含重新开始）；继续不是开始。入口不传
  // source（重置那条路、测试）就不记——来源说不出来就别编一个。
  if (kind !== 'resumed' && source) {
    captureTelemetry('tutorial_started', { source, tutorial_version: ONBOARDING_FLOW_VERSION })
  }
  return { ok: true, kind }
}

/* ------------------------------ 四个入口共用 ------------------------------ */

export type TutorialEntryKind = 'start' | 'resume' | 'restart'

/**
 * 入口该说什么。**纯读**：
 *   * 进行中 / 暂停 → 「继续教程」；
 *   * 已完成 / 已跳过 → 「再看一遍教程」（走 start：副本保留进度、onboarding 从头；
 *     与「重置教程项目」是两件事，后者才换副本）；
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

/** 入口点下去：三种都走 `startTutorial()`——「重新开始」的语义是 onboarding 从头，副本不换 */
export async function runTutorialEntry(source: TutorialEntrySource): Promise<TutorialOutcome> {
  const out = await startTutorial(source)
  if (!out.ok) useUiStore.getState().setStatus(out.message, 'error')
  return out
}

/** 「重置提示」：所有一次性情境提示重新可见 */
export function resetHints(): void {
  useOnboardingStore.getState().resetHints()
  useUiStore.getState().setStatus(msg('onboarding.hintsReset', undefined, 'dialogs'))
}
