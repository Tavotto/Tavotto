/**
 * 新手教程 / 一次性提示的**本机状态**（Prompt 21，ADR 0040；三步原生教程 ADR 0136）。
 *
 * ### 它记什么、不记什么
 *
 * 记：状态机（未开始 / 进行中 / 已暂停 / 已完成 / 已跳过）、当前步骤 id、
 * 完成过的步骤 id、每类提示看过没有、起止时间、欢迎页出现过没有、教程演示图上的
 * 两处改动（图例相对初始位置挪了多少 pt、标题字号 pt——演示图自己的模型坐标，
 * 不是 DOM 坐标，见 `lib/onboarding/demoFigure`）。
 *
 * 不记：DOM 节点、翻译好的字符串、文件路径、对象 id / gid、用户内容。
 *
 * 三步教程（ADR 0136）**不打开任何项目**：它只用内置的演示图，不执行 Python。
 * `tutorialProjectId` / `tutorialDocumentId` 是 v2 的 coachmark 教程留下的格式字段，
 * 读旧数据时原样保留、新流程写 null。
 *
 * 不持久化的只有 `surface`：教程层此刻显示哪一屏（没有 / 欢迎页 / 教程）。
 *
 * ### 与项目文档分离
 *
 * 一格 `localStorage`（`tavotto.onboarding`），与 `.tavotto` 文档、项目设置、
 * 遥测同意互不相干。「重置教程项目」（后端换副本）与「重置 onboarding」（清这
 * 一格）是两件事：前者清的是磁盘上的教程副本 + 本机的进度，后者只清本机；
 * 提示看过的记录另有 `resetHints()`，教程重开**不**抹掉它（用户已经知道的事
 * 不必再被提示一遍）。
 *
 * ### 版本
 *
 * `schemaVersion` 说的是这一格的**格式**；`flowVersion` 说的是**步骤内容**。
 * 格式认不出 → 回安全默认（未开始）；步骤内容升了版 → 上一版留下的已完成 / 已跳过 /
 * 进行中 / 暂停**一律回到新手态**，把新教程自动看一次（2026-10-21 用户裁决，ADR 0136 修订；
 * 只发生一次，见 `migratePersisted`），`hintSeen` 保留。
 *
 * ### embedded
 *
 * 宿主可以用 `configureOnboardingPersistence(null)` 关掉持久化（纯内存），或
 * 提供自己的 adapter。核心状态机不分叉。
 */
import { create } from 'zustand'
import { clampLegendOffset, normalizeTitleSize, TITLE_SIZE } from '@/lib/onboarding/demoFigure'
import { firstIncomplete, isStepId, nextStepId, STEP_IDS, type StepId } from '@/lib/onboarding/stepIds'

export const ONBOARDING_SCHEMA_VERSION = 1
/**
 * 步骤内容的版本。改了步骤（增删 / 改完成条件）就升它，**不要改 step id**。
 *
 * v2（2026-09-06，审计 T36）：每步多了前置校验与行动按钮、`add_to_layout` 按文档里
 * 实际有几张教程图出变体、结束页按「完成 / 跳过」分别计数（`skippedSteps`）。
 *
 * v3（2026-10-19，ADR 0136）：八步工作台 coachmark 教程换成三步原生演示教程
 * （`demo_import` / `demo_move_legend` / `demo_change_title`）。
 *
 * v4（2026-10-21，同一教程内容，只升迁移纪元）：「老用户升级后看一次」的迁移要认得出**已经被 v3 代码读过并写回**的旧
 * blob——v3 代码读旧 blob 时把 `flowVersion` 规范成 3 再原样存回（如 `markHintSeen`），按 `< 3` 判就认成「当前」了
 * （Codex #1269）。升到 4 之后，凡 `flowVersion < 4`（含这些被规范成 3 的旧 blob）都迁回新手态一次。
 * 代价：在 0.19.0b1 里真走完 v3 教程的人也会再看一次。
 */
export const ONBOARDING_FLOW_VERSION = 4

export const ONBOARDING_KEY = 'tavotto.onboarding'

export type OnboardingStatus = 'not_started' | 'active' | 'paused' | 'completed' | 'skipped'
export const ONBOARDING_STATUSES: readonly OnboardingStatus[] = [
  'not_started',
  'active',
  'paused',
  'completed',
  'skipped',
]
export const isOnboardingStatus = (v: unknown): v is OnboardingStatus =>
  typeof v === 'string' && (ONBOARDING_STATUSES as readonly string[]).includes(v)

/** 一次性情境提示的种类（闭集）；每类只出现一次 */
export const HINT_KINDS = [
  'panel_editable',
  'panel_layout_only',
  'fast_edit_entered',
  'multi_select',
  'problem_found',
  'layout_page_bounds',
] as const
export type HintKind = (typeof HINT_KINDS)[number]
export const isHintKind = (v: unknown): v is HintKind =>
  typeof v === 'string' && (HINT_KINDS as readonly string[]).includes(v)

export interface OnboardingPersisted {
  schemaVersion: number
  flowVersion: number
  status: OnboardingStatus
  currentStep: StepId | null
  /**
   * **走过**的步骤（真的完成或用户跳过都算）：状态机「下一步是哪一步」与
   * 迁移时「回到第一个未完成的步骤」都按它。名字是历史遗留（v1 只有它一份账）。
   */
  completedSteps: StepId[]
  /**
   * 其中被用户点「跳过此步」跳过的子集。结束页按它把「完成 n 步」与「跳过 m 步」
   * 分开说——跳过不是完成，不能用完成式总结一份全跳过的教程。
   * 重做一步（返回再真的做完）会把它从这里移出。
   */
  skippedSteps: StepId[]
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
  /** 欢迎页第一次出现的时间：首启自动弹欢迎页只弹这一次（之后从入口进） */
  welcomeSeenAt: number | null
  /** 教程演示图上的改动（模型坐标，pt）；重新开始清掉 */
  demo: DemoEdits
}

/**
 * 教程演示图上用户做过的两处改动。单位都是演示图自己的 pt（`lib/onboarding/demoFigure`），
 * 与窗口大小、缩放、DPI 无关——返回上一步、重启之后按它把图画回来，前后对比的「后」就是它。
 * `null` = 没改过（画脚本里的原样）。
 */
export interface DemoEdits {
  /** 图例相对脚本里原位置的位移 [dx, dy]（pt，向右 / 向下为正） */
  legendOffset: [number, number] | null
  /** 标题字号（pt） */
  titleSizePt: number | null
}

export const DEMO_EDITS_EMPTY: DemoEdits = { legendOffset: null, titleSizePt: null }

/** 教程层此刻显示哪一屏（不持久化） */
export type OnboardingSurface = 'none' | 'welcome' | 'tutorial'

export const ONBOARDING_DEFAULTS: OnboardingPersisted = {
  schemaVersion: ONBOARDING_SCHEMA_VERSION,
  flowVersion: ONBOARDING_FLOW_VERSION,
  status: 'not_started',
  currentStep: null,
  completedSteps: [],
  skippedSteps: [],
  hintSeen: {},
  startedAt: null,
  completedAt: null,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
  welcomeSeenAt: null,
  demo: DEMO_EDITS_EMPTY,
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
  'skippedSteps',
  'hintSeen',
  'startedAt',
  'completedAt',
  'tutorialProjectId',
  'tutorialDocumentId',
  'pausedBy',
  'welcomeSeenAt',
  'demo',
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
 * 演示改动的校验：只认有限数、且落在演示图能画的范围里（位移不超过图幅）。
 * 位移先过格式校验（挡 NaN、Infinity、字符串、离谱的数），再夹进拖动控件同一套边界
 * （`clampLegendOffset`）：夹不进去的值会把图例画到视口外，指针够不着、第二步永远完成不了。
 * 字号**不**用宽松范围：标题控件真实的区间是 `demoFigure.TITLE_SIZE`，落在它之外的值任何编辑都
 * 产生不了，却会被第三步当成「进入时的字号」，使「调大」永远达不到——见 `migrateDemo`。
 */
export const DEMO_LIMITS = { offsetPt: 500 } as const

export function migrateDemo(raw: unknown): DemoEdits {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...DEMO_EDITS_EMPTY }
  const v = raw as Record<string, unknown>
  const off = v.legendOffset
  const legendOffset =
    Array.isArray(off) &&
    off.length === 2 &&
    off.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= DEMO_LIMITS.offsetPt)
      ? clampLegendOffset([off[0], off[1]])
      : null
  const size = numOrNull(v.titleSizePt)
  // 区间外 → 丢掉（回默认字号），不夹到边界：夹成 max 会让第三步一进来就站在上限、无法再「调大」；
  // 丢掉则回到初始字号，教程可以正常走。区间内再吸附到 0.5 pt（与输入框的提交同一套）
  const titleSizePt =
    size !== null && size >= TITLE_SIZE.min && size <= TITLE_SIZE.max ? normalizeTitleSize(size) : null
  return { legendOffset, titleSizePt }
}

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
  const recorded = Array.isArray(v.completedSteps) ? (v.completedSteps.filter(isStepId) as StepId[]) : []
  // 还在流程里（进行中 / 暂停）的完成历史只认「从第一步起连续完成」的那一段，在第一个缺口处截断：
  // 步骤只能按顺序完成（「下一步」只在当前步骤完成时才挪），跳着完成的历史只能来自手改 / 半截写入——
  // 留着它，用户做完前一步就会落到一个「已完成」的步骤上，「下一步」直接可点，那一步的交互被绕过去。
  // 已完成 / 已跳过的不再走流程，历史原样（v2 迁来的 `done` 照旧保留）
  const inFlowStatus = v.status === 'active' || v.status === 'paused'
  const completedSteps: StepId[] = inFlowStatus ? [] : recorded
  if (inFlowStatus) {
    for (const id of STEP_IDS) {
      if (!recorded.includes(id)) break
      completedSteps.push(id)
    }
  }
  // v1 的 blob 没有这一项：那时跳过与完成记在同一份账里，分不出来——按「都是
  // 完成」读（旧数据没说过谁被跳过，不替它编）。只认同时也在 completedSteps 里的
  const passed = new Set<string>(completedSteps)
  const skippedSteps = Array.isArray(v.skippedSteps)
    ? (v.skippedSteps.filter((id): id is StepId => isStepId(id) && passed.has(id)) as StepId[])
    : []
  const hintSeen: Partial<Record<HintKind, number>> = {}
  if (v.hintSeen && typeof v.hintSeen === 'object' && !Array.isArray(v.hintSeen)) {
    for (const [k, val] of Object.entries(v.hintSeen as Record<string, unknown>)) {
      const at = numOrNull(val)
      if (isHintKind(k) && at !== null) hintSeen[k] = at
    }
  }
  const flowVersion = numOrNull(v.flowVersion) ?? 0
  // 升级后让每个人把新教程看一次（2026-10-21 用户裁决，ADR 0136 修订，覆盖「已完成 / 已跳过的不被
  // 重新打扰」）：上一版流程留下的 completed / skipped（也包括 active / paused——它们的步骤 id 在新流程里
  // 本来就认不出，进度是空的）一律回到「新手态」，与一个真正的新用户同一条路径：主页显示新手版
  // （`homeVariant`）、首次进入自动弹欢迎页（`useAutoOpen`）。**只发生一次**：返回值里的 `flowVersion`
  // 是当前版本，之后用户在新版里完成 / 跳过就照常是老手版，下次读到时不再满足 `flowVersion < 当前`。
  // 保留：`hintSeen`（用户已经知道的事不必再提示）。清掉：完成 / 跳过历史、起止时间（描述的是另一套
  // 教程，留在 not_started 里自相矛盾；新一轮 `start()` 本就会重写）、`welcomeSeenAt`（它正是「自动弹一次」
  // 的闸，不清则老用户永远不会自动弹；不另设标志位）、演示改动。`currentStep` 在未开始时恒为 null，
  // 起点由 `start()` 设为第一步。tutorialProjectId / DocumentId 是 v2 遗留的格式字段，原样保留。
  // 旧版的 not_started 也走这里：上一版看过欢迎页却没开始的人带着 `welcomeSeenAt`，不清就永远不会自动弹。
  if (flowVersion < ONBOARDING_FLOW_VERSION) {
    return {
      ...ONBOARDING_DEFAULTS,
      hintSeen,
      tutorialProjectId: strOrNull(v.tutorialProjectId),
      tutorialDocumentId: strOrNull(v.tutorialDocumentId),
    }
  }
  const done = new Set<string>(completedSteps)
  // 「欢迎」步骤已于 2026-09-30 取消；v2 的八个 coachmark 步骤于 2026-10-19 换成三步演示
  // （ADR 0136）。停在认不出的步骤上的进行中 / 暂停用户一律落到第一个未完成的新步骤；
  // 旧 id 在 completedSteps 里的记录已被 isStepId 滤掉——跳过过的旧步骤不会被当成新步骤的完成
  let currentStep: StepId | null = isStepId(v.currentStep) ? v.currentStep : null
  const inFlow = status === 'active' || status === 'paused'
  if (inFlow) {
    // 步骤内容升了版，或记的步骤已经不存在：回到第一个未完成的步骤。
    // 完成历史一条不抹——用户做过的事仍然做过。
    if (currentStep === null) {
      currentStep = firstIncomplete(done)
    }
    // 停在前面还有没完成的步骤之后（手改 / 半截写入：如完成页 + 一步没做）：回到第一个未完成的。
    // 「下一步」只在当前步骤完成时才挪，所以合法的进度永远不会越过它；往回翻（停在已完成的步骤）照常保留
    const reachable = firstIncomplete(done)
    if (currentStep !== null && STEP_IDS.indexOf(currentStep) > STEP_IDS.indexOf(reachable)) {
      currentStep = reachable
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
    skippedSteps,
    hintSeen,
    startedAt: numOrNull(v.startedAt),
    completedAt: numOrNull(v.completedAt),
    tutorialProjectId: strOrNull(v.tutorialProjectId),
    tutorialDocumentId: strOrNull(v.tutorialDocumentId),
    pausedBy,
    welcomeSeenAt: numOrNull(v.welcomeSeenAt),
    demo: migrateDemo(v.demo),
  }
}

/* --------------------------------- store ---------------------------------- */

interface OnboardingState extends OnboardingPersisted {
  /** 教程层此刻显示哪一屏（不持久化：重启之后由 `NativeTutorial` 按状态重新判） */
  surface: OnboardingSurface
  /** 从头开始一轮教程：第一步、清进度与演示改动。提示看过的记录不动 */
  start: () => void
  /** 打开欢迎页（记下它出现过：首启自动弹只弹这一次） */
  openWelcome: () => void
  /**
   * 进教程：进行中 / 暂停的接着上次那一步，其余从头开始。回 `started` / `resumed`
   * 让入口决定记不记 `tutorial_started`（继续不是开始）。
   */
  openTutorial: () => 'started' | 'resumed'
  /** 收起教程层（状态不变：进行中的下次打开接着来） */
  closeSurface: () => void
  resume: () => void
  /** 跳过整个教程（终态，不是完成）；教程层一起收起 */
  skip: () => void
  /** 走完教程（终态）；教程层一起收起 */
  complete: () => void
  /** 这一步的任务真的做到了（由教程界面在真实交互成功的那一刻调） */
  markStep: (id: StepId) => void
  /** 当前步骤做到了才前进一步；没做到是 no-op（「下一步」不能替用户声称完成） */
  next: () => void
  /** 把当前步骤挪到这里 */
  goTo: (id: StepId) => void
  /** 返回上一步；已在第一步就什么都不做。**不撤销**完成记录与演示改动 */
  back: () => void
  /** 记下演示图上的改动（合并） */
  setDemo: (patch: Partial<DemoEdits>) => void
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

const freshRun = (): Partial<OnboardingPersisted> => ({
  status: 'active',
  currentStep: STEP_IDS[0],
  completedSteps: [],
  skippedSteps: [],
  startedAt: Date.now(),
  completedAt: null,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
  demo: { ...DEMO_EDITS_EMPTY },
})

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  ...readPersisted(),
  surface: 'none',

  start: () => commitState(freshRun()),

  openWelcome: () => {
    set({ surface: 'welcome' })
    if (get().welcomeSeenAt === null) commitState({ welcomeSeenAt: Date.now() })
  },

  openTutorial: () => {
    const s = get().status
    if (s === 'active' || s === 'paused') {
      get().resume()
      set({ surface: 'tutorial' })
      return 'resumed'
    }
    get().start()
    set({ surface: 'tutorial' })
    return 'started'
  },

  closeSurface: () => set({ surface: 'none' }),

  resume: () => {
    if (get().status !== 'paused') return
    commitState({ status: 'active', pausedBy: null })
  },

  skip: () => {
    const s = get().status
    if (s !== 'active' && s !== 'paused') return
    set({ surface: 'none' })
    commitState({ status: 'skipped', currentStep: null, pausedBy: null })
  },

  complete: () => {
    const s = get().status
    if (s !== 'active' && s !== 'paused') return
    set({ surface: 'none' })
    // 全部步骤记成走过；**跳过的那份账原样保留**（v2 的数据里可能有）
    commitState({
      status: 'completed',
      currentStep: null,
      completedSteps: [...STEP_IDS],
      completedAt: Date.now(),
      pausedBy: null,
    })
  },

  markStep: (id) => {
    const passed = get().completedSteps
    const skipped = get().skippedSteps
    const nextPassed = passed.includes(id) ? passed : [...passed, id]
    const nextSkipped = skipped.includes(id) ? skipped.filter((x) => x !== id) : skipped
    if (nextPassed === passed && nextSkipped === skipped) return
    commitState({ completedSteps: nextPassed, skippedSteps: nextSkipped })
  },

  next: () => {
    const cur = get().currentStep
    if (!cur || !get().completedSteps.includes(cur)) return
    const to = nextStepId(cur)
    if (to) commitState({ currentStep: to })
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

  setDemo: (patch) => commitState({ demo: { ...get().demo, ...patch } }),

  markHintSeen: (kind) => {
    if (get().hintSeen[kind]) return
    commitState({ hintSeen: { ...get().hintSeen, [kind]: Date.now() } })
  },

  resetHints: () => commitState({ hintSeen: {} }),

  resetOnboarding: () => {
    set({ ...ONBOARDING_DEFAULTS, surface: 'none' })
    try {
      persistence?.remove()
    } catch {
      /* 删不掉就留着；下次读到的是默认值以外的旧数据，迁移仍然安全 */
    }
  },
}))

/** 一次性提示看过没有 */
export const hintSeen = (kind: HintKind): boolean => !!useOnboardingStore.getState().hintSeen[kind]
