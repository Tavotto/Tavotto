/**
 * 教程的**入口动作**——项目选择器、Help、命令面板、设置四个入口都调这里，
 * 状态逻辑只有这一份（Prompt 21 §十）。
 *
 * ```text
 * startTutorial()   GET/POST /api/tutorial/open → 认领项目 → 装教程画布 → 开始 / 继续 onboarding
 * resetTutorial()   确认 → POST /api/tutorial/reset → 忘掉本机那格 autosave → 重新装 → 从头开始
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
import { forgetLocalDocument, readAutosaveDoc, useDocumentStore } from '@/store/documentStore'
import { useOnboardingStore, type OnboardingStatus } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
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
  const store = useDocumentStore.getState()
  const { doc: saved } = await readAutosaveDoc(id)
  if (saved) return store.switchDocument(saved, id)
  let raw: unknown
  try {
    raw = await fetchLayout(meta.document_name)
  } catch {
    return false
  }
  const pd = migrateToProject(raw)
  if (!pd) return false
  return useDocumentStore.getState().switchDocument(pd, id)
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
export async function startTutorial(): Promise<TutorialOutcome> {
  if (useTutorialStore.getState().busy) return fail('open_failed')
  useTutorialStore.setState({ busy: 'open', failure: null })
  let res: TutorialOpenResult
  try {
    res = await openTutorialApi({ default: true })
  } catch (e) {
    return fail(classify(e), e)
  }
  return landTutorial(res, 'start')
}

/**
 * 「重新开始教程」。先确认——教程项目里如果有用户另存的画布文件，确认框会
 * 点名列出来（重置换的是整个副本目录，它们会一起没）。
 */
export async function resetTutorial(): Promise<TutorialOutcome> {
  if (useTutorialStore.getState().busy) return fail('open_failed')
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
  useTutorialStore.setState({ busy: 'reset', failure: null })
  let res: TutorialOpenResult
  try {
    res = await resetTutorialApi({ default: true })
  } catch (e) {
    return fail(classify(e), e)
  }
  // 后端只清了磁盘那格；本机这格不忘掉的话 readAutosaveDoc 会把旧进度推回去
  forgetLocalDocument(res.tutorial.document_id)
  return landTutorial(res, 'reset')
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

async function landTutorial(res: TutorialOpenResult, how: 'start' | 'reset'): Promise<TutorialOutcome> {
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
      await proj.adoptOpenedProject(res.project, {
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
  return { ok: true, kind }
}

/* ------------------------------ 四个入口共用 ------------------------------ */

export type TutorialEntryKind = 'start' | 'resume' | 'restart'

/**
 * 入口该说什么。**纯读**：
 *   * 进行中 / 暂停 → 「继续教程」；
 *   * 已完成 / 已跳过 → 「重新开始教程」（走 start：副本保留进度、onboarding 从头）；
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
export async function runTutorialEntry(): Promise<TutorialOutcome> {
  const out = await startTutorial()
  if (!out.ok) useUiStore.getState().setStatus(out.message, 'error')
  return out
}

/** 「重置提示」：所有一次性情境提示重新可见 */
export function resetHints(): void {
  useOnboardingStore.getState().resetHints()
  useUiStore.getState().setStatus(msg('onboarding.hintsReset', undefined, 'dialogs'))
}
