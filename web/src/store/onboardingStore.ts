/**
 * 新手教程 / 一次性提示的**本机状态**（Prompt 21，ADR 0040）。
 *
 * ### 它记什么、不记什么
 *
 * 记：状态机（未开始 / 进行中 / 已暂停 / 已完成 / 已跳过）、当前步骤 id、
 * 完成过的步骤 id、每类提示看过没有、起止时间、教程项目与文档的 id。
 *
 * 不记：DOM 节点、翻译好的字符串、文件路径、对象 id / gid、用户内容。
 * 「教程里那张图是哪个对象」由 `lib/onboarding/steps` 在需要的那一刻按
 * `tutorial_meta.json` 的 `file` / role 现找——对象 id 会随重置 / 重建变化，
 * 记下来只会连错。
 *
 * ### 与项目文档分离
 *
 * 一格 `localStorage`（`tavotto.onboarding`），与 `.tavotto` 文档、项目设置、
 * 遥测同意互不相干。「重新开始教程」（后端换副本）与「重置 onboarding」（清这
 * 一格）是两件事：前者清的是磁盘上的教程副本 + 本机的进度，后者只清本机；
 * 提示看过的记录另有 `resetHints()`，教程重开**不**抹掉它（用户已经知道的事
 * 不必再被提示一遍）。
 *
 * ### 版本
 *
 * `schemaVersion` 说的是这一格的**格式**；`flowVersion` 说的是**步骤内容**。
 * 格式认不出 → 回安全默认（未开始）；步骤内容升了版 → 进行中 / 暂停的用户回到
 * 第一个未完成的步骤（新加的步骤会被提供），已完成 / 已跳过的用户**不被重新
 * 打扰**、完成历史原样保留。
 *
 * ### embedded
 *
 * 宿主可以用 `configureOnboardingPersistence(null)` 关掉持久化（纯内存），或
 * 提供自己的 adapter。核心状态机不分叉。
 */
import { create } from 'zustand'
import { firstIncomplete, isStepId, STEP_IDS, type StepId } from '@/lib/onboarding/stepIds'

export const ONBOARDING_SCHEMA_VERSION = 1
/** 步骤内容的版本。改了步骤（增删 / 改完成条件）就升它，**不要改 step id** */
export const ONBOARDING_FLOW_VERSION = 1

export const ONBOARDING_KEY = 'tavotto.onboarding'

export type OnboardingStatus = 'not_started' | 'active' | 'paused' | 'completed' | 'skipped'

/** 一次性情境提示的种类（闭集）；每类只出现一次 */
export const HINT_KINDS = [
  'panel_editable',
  'panel_layout_only',
  'fast_edit_entered',
  'multi_select',
  'problem_found',
] as const
export type HintKind = (typeof HINT_KINDS)[number]
export const isHintKind = (v: unknown): v is HintKind =>
  typeof v === 'string' && (HINT_KINDS as readonly string[]).includes(v)

export interface OnboardingPersisted {
  schemaVersion: number
  flowVersion: number
  status: OnboardingStatus
  currentStep: StepId | null
  completedSteps: StepId[]
  /** 看过的提示 → 看到的时间戳 */
  hintSeen: Partial<Record<HintKind, number>>
  startedAt: number | null
  completedAt: number | null
  /** 教程副本的项目 id（后端给的短 id）；换项目时据此判「离开了教程」 */
  tutorialProjectId: string | null
  /** 教程画布的 documentId（= `tutorial_meta.document_id`） */
  tutorialDocumentId: string | null
  /** 暂停是用户按的（关掉 coachmark / Esc）还是系统按的（切走了项目） */
  pausedBy: 'user' | 'system' | null
}

export const ONBOARDING_DEFAULTS: OnboardingPersisted = {
  schemaVersion: ONBOARDING_SCHEMA_VERSION,
  flowVersion: ONBOARDING_FLOW_VERSION,
  status: 'not_started',
  currentStep: null,
  completedSteps: [],
  hintSeen: {},
  startedAt: null,
  completedAt: null,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
}

/* ------------------------------ 持久化 adapter ------------------------------ */

export interface OnboardingPersistence {
  read(): string | null
  write(raw: string): void
  remove(): void
}

const localStoragePersistence: OnboardingPersistence = {
  read: () => localStorage.getItem(ONBOARDING_KEY),
  write: (raw) => localStorage.setItem(ONBOARDING_KEY, raw),
  remove: () => localStorage.removeItem(ONBOARDING_KEY),
}

let persistence: OnboardingPersistence | null = localStoragePersistence

/**
 * 换持久化后端；`null` = 纯内存（embedded 宿主不想让教程状态落进 iframe 的
 * localStorage 时用）。换完会按新后端重读一次。
 */
export function configureOnboardingPersistence(p: OnboardingPersistence | null): void {
  persistence = p
  useOnboardingStore.setState({ ...readPersisted() })
}

function readPersisted(): OnboardingPersisted {
  if (!persistence) return { ...ONBOARDING_DEFAULTS }
  try {
    const raw = persistence.read()
    return migratePersisted(raw ? JSON.parse(raw) : null)
  } catch {
    return { ...ONBOARDING_DEFAULTS }
  }
}

function writePersisted(state: OnboardingPersisted): void {
  if (!persistence) return
  try {
    persistence.write(JSON.stringify(pick(state)))
  } catch {
    /* 存不下只影响下次打开还记不记得进度 */
  }
}

const PERSISTED_KEYS: (keyof OnboardingPersisted)[] = [
  'schemaVersion',
  'flowVersion',
  'status',
  'currentStep',
  'completedSteps',
  'hintSeen',
  'startedAt',
  'completedAt',
  'tutorialProjectId',
  'tutorialDocumentId',
  'pausedBy',
]

function pick(state: OnboardingPersisted): OnboardingPersisted {
  return Object.fromEntries(PERSISTED_KEYS.map((k) => [k, state[k]])) as unknown as OnboardingPersisted
}

/* --------------------------------- 迁移 ----------------------------------- */

const STATUSES: readonly OnboardingStatus[] = ['not_started', 'active', 'paused', 'completed', 'skipped']

const numOrNull = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/**
 * 任何来路的 blob → 一份合法状态。**坏数据回安全默认（未开始），绝不抛。**
 *
 * 逐字段校验而不是整份信任：手改过 / 半截写入 / 旧版本的 blob 里可能只有
 * 一两个字段坏了，能保住的进度要保住。
 */
export function migratePersisted(raw: unknown): OnboardingPersisted {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...ONBOARDING_DEFAULTS }
  const v = raw as Record<string, unknown>
  if (v.schemaVersion !== ONBOARDING_SCHEMA_VERSION) return { ...ONBOARDING_DEFAULTS }

  const status = STATUSES.includes(v.status as OnboardingStatus)
    ? (v.status as OnboardingStatus)
    : 'not_started'
  const completedSteps = Array.isArray(v.completedSteps)
    ? (v.completedSteps.filter(isStepId) as StepId[])
    : []
  const hintSeen: Partial<Record<HintKind, number>> = {}
  if (v.hintSeen && typeof v.hintSeen === 'object' && !Array.isArray(v.hintSeen)) {
    for (const [k, val] of Object.entries(v.hintSeen as Record<string, unknown>)) {
      const at = numOrNull(val)
      if (isHintKind(k) && at !== null) hintSeen[k] = at
    }
  }
  const flowVersion = numOrNull(v.flowVersion) ?? 0
  const done = new Set<string>(completedSteps)
  let currentStep: StepId | null = isStepId(v.currentStep) ? v.currentStep : null
  const inFlow = status === 'active' || status === 'paused'
  if (inFlow) {
    // 步骤内容升了版，或记的步骤已经不存在：回到第一个未完成的步骤。
    // 完成历史一条不抹——用户做过的事仍然做过。
    if (flowVersion < ONBOARDING_FLOW_VERSION || currentStep === null) {
      currentStep = firstIncomplete(done)
    }
  } else {
    currentStep = null
  }
  const pausedBy = status === 'paused' && (v.pausedBy === 'user' || v.pausedBy === 'system')
    ? v.pausedBy
    : status === 'paused'
      ? 'user'
      : null

  return {
    schemaVersion: ONBOARDING_SCHEMA_VERSION,
    flowVersion: ONBOARDING_FLOW_VERSION,
    status,
    currentStep,
    completedSteps,
    hintSeen,
    startedAt: numOrNull(v.startedAt),
    completedAt: numOrNull(v.completedAt),
    tutorialProjectId: strOrNull(v.tutorialProjectId),
    tutorialDocumentId: strOrNull(v.tutorialDocumentId),
    pausedBy,
  }
}

/* --------------------------------- store ---------------------------------- */

interface OnboardingState extends OnboardingPersisted {
  /** 从头开始一轮教程（绑到这份教程项目 / 文档）。提示看过的记录不动 */
  start: (ids: { projectId: string | null; documentId: string }) => void
  /** 暂停：`user` = 用户关掉了 coachmark / 按了 Esc；`system` = 切走了项目 */
  pause: (by: 'user' | 'system') => void
  resume: () => void
  skip: () => void
  complete: () => void
  /** 某一步的真实完成条件满足了 */
  markStep: (id: StepId) => void
  /** 把当前步骤挪到这里（前进 / 返回都经它） */
  goTo: (id: StepId) => void
  /** 返回上一步；已在第一步就什么都不做。**不撤销**完成记录 */
  back: () => void
  markHintSeen: (kind: HintKind) => void
  /** 只清提示记录 */
  resetHints: () => void
  /** 整格清掉：状态、进度、提示。教程副本（磁盘）不归这里管 */
  resetOnboarding: () => void
}

function commitState(patch: Partial<OnboardingPersisted>) {
  useOnboardingStore.setState(patch)
  writePersisted(useOnboardingStore.getState())
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  ...readPersisted(),

  start: ({ projectId, documentId }) =>
    commitState({
      status: 'active',
      currentStep: STEP_IDS[0],
      completedSteps: [],
      startedAt: Date.now(),
      completedAt: null,
      tutorialProjectId: projectId,
      tutorialDocumentId: documentId,
      pausedBy: null,
    }),

  pause: (by) => {
    if (get().status !== 'active') return
    commitState({ status: 'paused', pausedBy: by })
  },

  resume: () => {
    if (get().status !== 'paused') return
    commitState({ status: 'active', pausedBy: null })
  },

  skip: () => {
    const s = get().status
    if (s !== 'active' && s !== 'paused') return
    commitState({ status: 'skipped', currentStep: null, pausedBy: null })
  },

  complete: () => {
    const s = get().status
    if (s !== 'active' && s !== 'paused') return
    commitState({
      status: 'completed',
      currentStep: null,
      completedSteps: [...STEP_IDS],
      completedAt: Date.now(),
      pausedBy: null,
    })
  },

  markStep: (id) => {
    if (get().completedSteps.includes(id)) return
    commitState({ completedSteps: [...get().completedSteps, id] })
  },

  goTo: (id) => {
    if (get().currentStep === id) return
    commitState({ currentStep: id })
  },

  back: () => {
    const cur = get().currentStep
    if (!cur) return
    const i = STEP_IDS.indexOf(cur)
    if (i <= 0) return
    commitState({ currentStep: STEP_IDS[i - 1] })
  },

  markHintSeen: (kind) => {
    if (get().hintSeen[kind]) return
    commitState({ hintSeen: { ...get().hintSeen, [kind]: Date.now() } })
  },

  resetHints: () => commitState({ hintSeen: {} }),

  resetOnboarding: () => {
    set({ ...ONBOARDING_DEFAULTS })
    try {
      persistence?.remove()
    } catch {
      /* 删不掉就留着；下次读到的是默认值以外的旧数据，迁移仍然安全 */
    }
  },
}))

/** 一次性提示看过没有 */
export const hintSeen = (kind: HintKind): boolean => !!useOnboardingStore.getState().hintSeen[kind]
