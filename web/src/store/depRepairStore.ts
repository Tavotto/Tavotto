import { create } from 'zustand'
import {
  cancelDependencyPlan,
  cancelJointDependencies,
  createDependencyPlan,
  createJointDependencyPlan,
  fetchDependencyState,
  installDependencyPlan,
  prepareJointDependencies,
  rebuildManagedEnvironment,
  setEngineEnvironment,
  setProjectEnvironment,
  adoptEnvironmentCandidate,
  setProjectUserEnvironment,
  skipDependencyPreparation,
  backendErrorText,
  ApiError,
  type DependencyPreparationOffer,
  type DependencyProgress,
  type DependencyRepairOffer,
  type DependencyRepairPlan,
  type InterpreterPin,
  type JointDependencyPlan,
  type JointDependencyRepairPlan,
  type PrivatePythonOffer,
  privatePythonOrigin,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

/**
 * 项目代际（issue #590，与 `packageStore` / `scriptRunStore` 同一条纪律，名单见
 * `docs/rules/frontend/asset-library.md`「项目代际」）。`clear()`（换项目）加一：A 项目的计划 / 绑定 /
 * 采用 / 跳过请求在途时切到 B，回来的响应一律作废——计划与错误说的是 A 的环境，而 B 上的按钮作用在 B 上。
 */
let projectEpoch = 0

/**
 * 本标签页起过的作业：plan_id → 起它那一刻的项目（`currentProjectId()`）。装包作业**切项目不取消**：
 * 后端的 `close_project` 只停 watcher 与 worker 池，`install_async` / `prepare_async` 的线程与它无关，
 * 结果按计划自己的 `project` 记账（ADR 0019 / 0061）。所以前端也不丢作业，只按所属项目分格：进度落进
 * 所属项目那格，终态副作用只在所属项目此刻开着时派发，切回 A 接得上（与 `packageStore` 的作业同一形状）。
 */
const startedPlans = new Map<string, string | null>()
/**
 * 与 `parked` 同一格收着的「就地重试」上下文（最近一次请求 + 受管环境那次授权）：切走时随作业收起、切回来随作业
 * 放回——不然切回来看到失败 / 取消的结局，卡片却没有「重试」（`onRetry` 要 `request`，Codex #709）。
 * 模块级而不进 store：界面不读它，只有 `clear()` 收放。
 */
const parkedRetry = new Map<
  string,
  {
    request: RepairRequest | null
    authorized: RepairDisclosure | null
    scriptOffer: ScriptRepairOffer | null
    /** 素材库脚本行发起的联合准备属于哪个脚本（进度认领 / 取消 / 装好重跑） */
    jointScript: string
    jointOffer: DependencyPreparationOffer | null
  }
>()
const projectKey = (project: string | null): string => project ?? ''

/**
 * 「环境换过了、那几行该重跑」但换的那一刻用户已经切到别的项目：按所属项目停放，切回来时再续（与装包作业
 * 「切走期间装好 → 切回来补跑」同一条路，`clear()` 里放回）。不是装包作业、没有进度可收放，所以单独一格。
 * 改用已有解释器 / 清掉全局固定两条路共用（Codex #742）
 */
const pendingEnvChanges = new Map<string, EnvChange>()

/**
 * 一次「环境换过了」（改用已有解释器 / 清掉全局固定）的**带标签的结局**（Codex #742 P1）：成功还是失败、失败的原文、
 * 成功时该如何核实，连同那几行要重跑的脚本一起。不在所属项目上的时候整份停放——**绝不把成败当成未知**：失败的
 * 回来只在卡片上说那一句、不重跑；成功的回来先按此刻重新读一次环境、核实确实生效了才重跑，没生效按失败处理。
 * （用户自己的 Python 以脚本目录为 cwd，脚本在走到缺的那个 import 之前就可能写项目里的文件——在没换成的解释器下
 * 重跑不是「无害」的。）
 */
interface EnvChange {
  kind: 'adopt' | 'unpin'
  module: string
  script?: string
  peers: string[]
  /** 从脚本行发起的：那一行的 offer（失败留在卡片上时，切项目清空了运行记录，卡片靠它挂回来） */
  scriptOffer: ScriptRepairOffer | null
  /** 改用成功后要重跑的门（#740 的 `rerunGated`）：授权框里改用装齐的用户环境走它 */
  gate?: 'needs_preparation'
  outcome:
    | { ok: false; error: string }
    | {
        ok: true
        /** 核实用：改用 → 项目此刻该用的解释器（后端回的规范路径）；清固定 → 被清掉的那条固定 */
        expectPython: string
        pinnedSource?: string
        /** 当场（没切过项目）就用的响应：写回 env */
        apply: () => void
      }
}

/**
 * 只给测试用：清空本模块的停放槽（作业所属 `startedPlans`、重试上下文 `parkedRetry`、环境改动的结局
 * `pendingEnvChanges`）。它们是模块级的、活得比一次 zustand reset 长——不清的话上一条用例停放下来的东西会在下一条
 * 用例切项目时被 drain，用例之间互相串（`projectEpoch` 只增不减，不清：各处只比「变没变」）
 */
export function __resetDepRepairParkingForTests(): void {
  startedPlans.clear()
  parkedRetry.clear()
  pendingEnvChanges.clear()
}

/** 所属项目此刻就是开着的那个（没切过，或 A → B → A 已经切回来了） */
const ownerIsCurrent = (owner: string | null): boolean => projectKey(owner) === projectKey(currentProjectId())

/**
 * 异步结果交给所属项目的**唯一**规则（Codex #742）：结果回来时所属项目就是当前项目，立即执行；不是，就停放到
 * 它那一格，回到它时由 `clear()` drain。判的是「所属项目是不是当前项目」，**不是**「代际变没变」——A → B → A
 * 在回来之前就切完了的话代际变了、所属项目却正开着，而 `clear()` 那次 drain 已经过去，停放下去就没人再取
 */
function deliverToOwner(owner: string | null, now: () => void, park: () => void): boolean {
  if (ownerIsCurrent(owner)) {
    now()
    return true
  }
  park()
  return false
}

/** 单包修复的一次请求（重试时原样再发一次） */
export interface RepairRequest {
  module: string
  script: string
  target: 'project_venv' | 'tavotto_managed'
  distribution?: string
}

/**
 * 从素材库**脚本行**发起的修复：那一行卡片的前提（`missing_dependency` 的 offer）。脚本行的卡片本来读
 * `scriptRunStore` 里那次运行的 offer，而换项目时 `resetForNewProject()` 刻意把 `scriptRunStore` 清空——
 * A → B → A 之后作业与重试上下文放回来了，那一行却因为没有 offer 不渲染卡片，进度 / 取消 / 重试都够不着
 * （#729）。所以发起时把 offer 与请求一起记下，随作业一起收放（`parkedRetry`）；脚本行在自己的运行里没有
 * offer 时用它。画布上那张卡发起的不记（它的前提在文档里，不会被清）。
 */
export interface ScriptRepairOffer {
  offer: DependencyRepairOffer
  module: string
  /**
   * 发起那一刻同样停在缺这个包上的**其它**脚本（2026-09-29：同一个包缺在几行上只挂一张卡，装好一起重跑）。随作业
   * 收放：切走期间装好的话，切回来时 `scriptRunStore` 已被清空、认不出谁也缺它，只能按这份名单补跑（Codex #742）
   */
  peers?: string[]
  /**
   * 这份 offer 挂在哪一行（改用 / 清固定失败、结局留在卡片上时用：那时没有 `request`，脚本行按它认领；
   * 装包作业那条路的认领仍按 `request.script`）
   */
  script?: string
}

/**
 * 用户点下去之前**已经看到**的那几样（一次授权的凭据）：装什么、装到哪、要不要先下载私有 Python
 * （版本 / 体积 / 已缓存）。卡片从 offer 上把它们说出口，点一次就形成计划并执行——前提是后端算出来的
 * 计划与这里**逐项相符**（`planMatchesDisclosure`）；不符就停在确认页，让用户按计划本身再看一遍。
 */
export interface RepairDisclosure {
  requirement: string
  /** 卡片说过要装的全部包（规范串）；计划里的每一个都得在其中——没有就只认 `requirement` */
  requirements?: string[]
  target_kind: 'tavotto_managed'
  private_python: PrivatePythonOffer | null
}

/**
 * 计划有没有超出用户看到的：目标、需求串逐字相同，不改用户环境；私有 Python 这一段来源要对得上、只许「更少」——
 * 看到的是「要下载 N MB」，计划变成「已缓存 / 不用下载」可以；看到的是「不用下载」或更小的体积，
 * 计划却要下载（或换了一份 Python），就不算同一次授权。
 */
/** 需求串 → 包名的比较键（PEP 503：大小写与 `-_.` 不分；不含 extras / 版本） */
export const requirementKey = (requirement: string): string =>
  (/^[A-Za-z0-9._-]+/.exec(requirement.trim())?.[0] ?? requirement).toLowerCase().replace(/[-_.]+/g, '-')

export function planMatchesDisclosure(plan: DependencyRepairPlan, seen: RepairDisclosure): boolean {
  if (plan.target_kind !== seen.target_kind || plan.modifies_user_environment) return false
  if (plan.requirement !== seen.requirement) return false
  // 没说过全部清单 = 只披露了 `requirement` 这一个：计划里多出的任何包（脚本 / 声明在点击前多了一个、或 offer
  // 时算不出而计划时算得出）都不在授权之内，停在确认页。按包名比（extras / 版本写法不算另一个包）
  const allowed = new Set((seen.requirements ?? [seen.requirement]).map(requirementKey))
  if ((plan.requirements ?? [plan.requirement]).some((r) => !allowed.has(requirementKey(r)))) return false
  const now = plan.private_python ?? null
  if (!now) return true
  const was = seen.private_python
  if (!was || now.id !== was.id || now.version !== was.version) return false
  // 来源（安装包自带 / 已缓存 / 下载）是计划的一部分：后端按它绑定、执行时对不上就当计划过期（#743）。卡片说的是
  // 「用自带的 / 用已缓存的」，计划却换成了另一个——两边都是 cached、零字节也不算同一次授权（Codex #742）。
  // 只有「说的是要下载，计划变成不用下载 / 下得更少」这一种「更少」放行
  const nowOrigin = privatePythonOrigin(now)
  const wasOrigin = privatePythonOrigin(was)
  if (wasOrigin !== 'download') return nowOrigin === wasOrigin
  return nowOrigin !== 'download' || now.download_bytes <= was.download_bytes
}

/**
 * 受控依赖修复的界面状态（ADR 0019）。
 *
 * 后端的两步是**故意的**（ADR 0019 §四，计划绑定，不是 `confirmed=true`）：
 *
 *   1. `plan(target)` —— 问后端「装什么、装到哪、会不会改你的环境」。
 *      这一步什么都不装，用户看到的确认文案就来自它。
 *   2. `install()` —— 执行**那个计划**（只发 plan_id）。
 *
 * 界面上受管环境那一条是**一次授权**（`installNow`）：卡片已经把计划里的全部要素说出口
 * （装什么、联网、私有 Python 版本与体积、建隔离环境、不改源码与现有环境），点一次就连着发这两步；
 * 计划超出了卡片说过的（`planMatchesDisclosure` 不成立）才停在确认页。项目 `.venv` 那一条仍然先到
 * 确认页——改用户自己的环境不可逆，要明确确认（ADR 0019 §八）。
 *
 * 进度经 SSE `engine.dependency` 推过来（useServerEvents 转发到这里）。
 * 界面**按 state 换文案，绝不解析日志**——日志只在「安装详情」里原样显示。
 */
interface DepRepairState {
  /** 已经形成、等用户确认的计划；null = 还没到确认那一步 */
  plan: DependencyRepairPlan | null
  progress: DependencyProgress | null
  /** 形成计划 / 发起安装期间的 busy 标记（防连点） */
  busy: boolean
  /** 出错时的机器可读 code（界面按它查文案） */
  errorCode: string
  /** 后端给的中文兜底原文（前端没有对应文案时才显示） */
  errorText: string
  /**
   * offer 形成**之后**才被钉上的全局解释器（#465，Codex 评审 P2）：plan 的 400 与
   * 安装失败事件都带着它。留在这里，卡片据此切到「恢复自动检测」那一支——
   * 否则关掉错误之后又是那几个注定无效的安装目标，用户可以无限重复同一个拒绝。
   */
  pinned: InterpreterPin | null
  makePlan: (
    args: { module: string; script: string; target: 'project_venv' | 'tavotto_managed'; distribution?: string },
    scriptOffer?: ScriptRepairOffer | null,
  ) => Promise<void>
  install: () => Promise<void>
  /**
   * 一次授权（受管环境）：形成计划，与用户看到的 `seen` 逐项相符就直接执行；不符停在确认页（`plan`）。
   */
  installNow: (args: RepairRequest, seen: RepairDisclosure, scriptOffer?: ScriptRepairOffer | null) => Promise<void>
  /** 失败 / 取消之后在同一张卡上再来一次：受管环境按上次授权的要素走一次授权，项目环境回到确认页 */
  retry: () => Promise<void>
  /** 最近一次请求（`retry` 用它；脚本行据 `script` 判断这张卡是不是自己的） */
  request: RepairRequest | null
  /** 最近一次请求若是从脚本行发起的：那一行卡片的 offer（随作业收放，#729；画布发起的为 null） */
  scriptOffer: ScriptRepairOffer | null
  /** 最近一次真正执行过的受管环境授权（`retry` 按它判计划有没有超出用户看到的） */
  authorized: RepairDisclosure | null
  /**
   * 受管目标「能不能用」offer 形成时还不知道（`available: null`：后端正在后台探基础解释器，于是 offer 上也没挂
   * 私有 Python）时，卡片先形成一份计划**只为读出它的真实要素**（要不要下载、多大；计划这一步什么都不装，
   * ADR 0019 §四）。卡片按它披露、按它授权——不然用户点一次「一键修复」，计划却多出一段下载，
   * `planMatchesDisclosure` 不符，只能停在确认页再点一次。
   *
   * **按脚本 + 模块分格**（Codex #742）：右栏与脚本行上可以同时挂着两张卡，一格一份，谁都不覆盖谁；切项目清空
   * （计划说的是那个项目的环境），`reset()` 不动（关掉一张卡不该让另一张卡回到「正在检查」）
   */
  managedPreviews: Readonly<Record<string, ManagedPreview>>
  previewManaged: (args: RepairRequest) => Promise<void>
  /**
   * 采用这台机器上已有的、已经装着那个包的解释器（ADR 0044）。**不是安装**：
   * 走项目环境 PATCH（带 `module` 让后端连那个包一起验），成功后把失败的
   * 渲染重新排上——与装完包之后那半边同一件事。素材库里因缺这个包停下的脚本行（发起的那一行 `script` 与同样
   * 缺它的其它行）同样重跑：一键修复的首选就是这条路，改用之后那一行还停在缺包上的话卡片永远收不掉（Codex #742）
   */
  adoptSystemPython: (
    python: string,
    module: string,
    script?: string,
    scriptOffer?: ScriptRepairOffer | null,
    /** 目标带着「看到它那一刻」的候选 id + 环境代时必须交（确认模式下的项目环境建议）：走代次绑定的采用 */
    candidate?: { id: string; generation: string } | null,
  ) => Promise<void>
  /**
   * 修复卡上的「恢复自动检测」：清掉全局显式解释器（`setPython(null)`），成功后收起这张卡、重排失败的渲染、重跑
   * 停在缺包上的脚本行。回 `null` = 成功，否则是那句错误。发请求那一刻记下项目代际：回来时已切项目的话，这些
   * 属于 A 的副作用一个都不在 B 上做，重跑停放到 A、切回来再续（Codex #742 P1）
   */
  clearPinnedInterpreter: (
    module: string,
    script?: string,
    pinned?: InterpreterPin | null,
    scriptOffer?: ScriptRepairOffer | null,
  ) => Promise<string | null>
  /**
   * 依赖弹窗里点「改用这个环境」（ADR 0079）：交 id 给后端体检并记成本项目的选择；成功就关框、
   * 把卡在这道门上的面板重排。失败把原文留在框里。
   */
  adoptUserEnvironment: (id: string, script: string) => Promise<void>
  cancel: () => Promise<void>
  rebuildManaged: () => Promise<void>
  onProgress: (p: DependencyProgress) => void
  /** 关掉确认卡片 / 换一个目标时回到干净状态 */
  reset: () => void
  /**
   * 换项目（`resetForNewProject()` 调）：换代作废在途请求，计划 / 错误 / 钉住的解释器整份丢掉；**作业不丢**——
   * 此刻显示的进度按所属项目收进 `parked`，此刻开着的项目（已经是新项目）若有收着的就放回来。
   */
  clear: () => void
  /** 切走的项目上还没看到结局的作业：项目 → 最后一条进度（界面不读它，切回去时 `clear()` 放回 `progress`） */
  parked: Readonly<Record<string, DependencyProgress>>
  /**
   * 各项目正在跑的受管环境重建：项目 → 进度 id（`clear()` 不动）。每次重建的进度 id 各不相同、由这里在发请求
   * 之前生成（#606 第 3 条），进度认得出是谁的，所以**只在同一项目里单飞**——A 的重建没结束，A 起不了第二次；
   * B 照常可以重建自己的（#605 那时 id 固定是 `managed-rebuild`，只能全局单飞）。按钮读 `rebuildRunningFor(项目)`。
   */
  rebuilding: Readonly<Record<string, string>>
  rebuildRunningFor: (project: string | null) => boolean

  // ---- 联合准备（U04，ADR 0061）：跑前的那一次授权 ----
  // 载荷本身（整份联合计划 + 可选目标）住在 `envStore.dependencyPreparation`（与运行目录的确认
  // 同一个家；渲染 store 静态 import 得到它，本 store 反向 import 会成环）。这里只管执行。
  /** 绑定好的联合计划（不装）；null = 还没到执行那一步 */
  jointPlan: JointDependencyRepairPlan | null
  /** 计划绑定不了（blocked / 什么都不缺）时后端交回的计划——界面按 blocked 的理由说下一步 */
  jointBlocked: JointDependencyPlan | null
  /** 最近一次 `prepare` 是为哪个脚本发起的（素材库脚本行据此认领进度 / 失败；`reset` / 切项目清空） */
  jointScript: string
  /** 最近一次 `prepare` 用的那份联合计划载荷（脚本行渲染进度 / 失败 / 重试要它；切项目随作业停放、切回放回） */
  jointOffer: DependencyPreparationOffer | null
  /**
   * 一步：绑定计划 → 执行（只发 plan_id）。目标由用户在框里选；脚本来自 envStore 里的载荷——素材库脚本行
   * （那里没有框、载荷住在那次运行的错误里）把载荷作为 `offerArg` 直接交进来。
   */
  prepare: (target: 'project_venv' | 'tavotto_managed', offerArg?: DependencyPreparationOffer) => Promise<void>
  cancelPreparation: () => Promise<void>
  /** 「不准备，直接运行」：明确的 skip（这道门一直问到有答案），然后关框并重排那次失败的渲染 */
  skipPreparation: (offerArg?: DependencyPreparationOffer) => Promise<void>
}

/** 预读的那份计划：`key` 认是哪个脚本的哪个包；`pending` 期间卡片的主按钮等它 */
export interface ManagedPreview {
  key: string
  pending: boolean
  plan: DependencyRepairPlan | null
  /** 形成不了计划时后端的 code（`managed_env_unavailable` = 这台电脑真的无路可走） */
  code: string
}

export const managedPreviewKey = (args: { script: string; module: string }): string =>
  `${args.script}\n${args.module}`

/** 后端错误 → (code, 原文, 固定)。没有 code 的一律归到通用安装失败。 */
const failure = (e: unknown): { code: string; text: string; pinned: InterpreterPin | null } => {
  const body = (e as { body?: { code?: string; error?: string; pinned?: InterpreterPin } })?.body
  const text = e instanceof Error ? e.message : ''
  return { code: body?.code || '', text: body?.error || text, pinned: body?.pinned ?? null }
}

export const useDepRepairStore = create<DepRepairState>((set, get) => ({
  plan: null,
  progress: null,
  busy: false,
  errorCode: '',
  errorText: '',
  pinned: null,
  jointPlan: null,
  jointBlocked: null,
  jointScript: '',
  jointOffer: null,
  request: null,
  authorized: null,
  scriptOffer: null,
  managedPreviews: {},
  parked: {},
  rebuilding: {},
  rebuildRunningFor: (project) => !!get().rebuilding[projectKey(project)],

  prepare: async (target, offerArg) => {
    const offer = offerArg ?? useEnvStore.getState().dependencyPreparation
    if (!offer || get().busy) return
    const epoch = projectEpoch
    let planId = ''
    // 脚本行也直接弹框：按发起时的运行记录记归属，切回项目后继续那次试运行。
    // 只有画布渲染的门没有脚本行意图，切回后仍只重排渲染。
    const fromScript = !!offerArg || useScriptRunStore.getState().byScript[offer.script]?.phase === 'needs_preparation'
    set({ busy: true, errorCode: '', errorText: '', jointBlocked: null, jointScript: fromScript ? offer.script : '', jointOffer: offer })
    try {
      const { plan } = await createJointDependencyPlan({ script: offer.script, target })
      // 绑定回来时已经切了项目：这是 A 的计划，不在 B 上执行（绑定不装，丢掉即可）
      if (epoch !== projectEpoch) return
      // 绑定回来的计划超出了用户看到的（offer 里那份）：不执行，按此刻的输入重新披露——脚本行重跑一次拿新的 offer，
      // 授权框关掉后重排那次失败的渲染（新的框带新的清单）。计划只是记录、不装，丢掉即可
      const seen = new Set(offer.plan.requirements.map(requirementKey))
      if (plan.requirements.some((r) => !seen.has(requirementKey(r)))) {
        set({ busy: false, jointPlan: null, jointScript: '', jointOffer: null })
        useEnvStore.getState().dismissDependencyPreparation()
        useRenderStore.getState().retryEnvironmentFailures()
        if (fromScript) void useScriptRunStore.getState().run(offer.script)
        return
      }
      planId = plan.plan_id
      startedPlans.set(planId, currentProjectId())
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来
      set({
        jointPlan: plan,
        progress: {
          plan_id: plan.plan_id,
          state: 'preparing',
          log: '',
          error: null,
          code: '',
          flow: 'joint',
          requirements: plan.requirements,
          // 乐观的这一条也带上目标：阶段数按它定（项目 venv 只有两步），不能等 SSE 第一条（Codex #742）
          target_kind: plan.target_kind,
          script: plan.script,
        },
      })
      await prepareJointDependencies(plan.plan_id)
      if (epoch !== projectEpoch) return // 作业照跑、已经收进 A 那格；B 的界面不动
      set({ busy: false })
    } catch (e) {
      if (epoch !== projectEpoch) return lateFailure(planId, e)
      const { code, text } = failure(e)
      const joint = (e as { body?: { joint?: JointDependencyPlan } })?.body?.joint ?? null
      set({ busy: false, progress: null, jointPlan: null, jointBlocked: joint, errorCode: code, errorText: text })
    }
  },

  cancelPreparation: async () => {
    const id = get().progress?.plan_id
    if (!id) return
    try {
      await cancelJointDependencies(id)
    } catch {
      // 取消与「装完了」天然赛跑，输了不是错误（过了提交点后端会说 committed）
    }
  },

  skipPreparation: async (offerArg) => {
    const offer = offerArg ?? useEnvStore.getState().dependencyPreparation
    if (!offer || get().busy) return
    const epoch = projectEpoch
    set({ busy: true })
    try {
      await skipDependencyPreparation(offer.script)
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
      return
    }
    // A 的「直接跑」已经记在后端（按 A 的项目）；关框 / 重排说的是此刻开着的项目，切过就不动
    if (epoch !== projectEpoch) return
    set({ busy: false, jointPlan: null, jointBlocked: null })
    useEnvStore.getState().dismissDependencyPreparation()
    // 门放行了：那次「先准备」的渲染重新排上，缺包会以 missing_dependency 回来（运行后那条路）
    useRenderStore.getState().retryEnvironmentFailures()
    // 素材库脚本行上停在这道门上的那次试运行同样重跑（图还没上画布时，门是从那里撞上的）
    useScriptRunStore.getState().rerunGated('needs_preparation', offer.script)
  },

  makePlan: async (args, scriptOffer = null) => {
    if (get().busy) return
    const epoch = projectEpoch
    set({ busy: true, errorCode: '', errorText: '', plan: null, request: args, scriptOffer: withPeers(scriptOffer, args.script) })
    try {
      const { plan } = await createDependencyPlan(args)
      if (epoch !== projectEpoch) return // A 的计划不落进 B 的确认卡片
      set({ plan, busy: false })
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text, pinned } = failure(e)
      set({ busy: false, errorCode: code, errorText: text, pinned })
    }
  },

  installNow: async (args, seen, scriptOffer = null) => {
    if (get().busy) return
    const epoch = projectEpoch
    set({
      busy: true,
      errorCode: '',
      errorText: '',
      plan: null,
      progress: null,
      request: args,
      scriptOffer: withPeers(scriptOffer, args.script),
    })
    let plan: DependencyRepairPlan
    try {
      plan = (await createDependencyPlan(args)).plan
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text, pinned } = failure(e)
      set({ busy: false, errorCode: code, errorText: text, pinned })
      return
    }
    if (epoch !== projectEpoch) return // A 的计划不落进 B，更不在 B 上执行
    set({ plan, busy: false })
    // 计划超出了卡片说过的：不执行，停在确认页（它把计划本身的要素说出口）
    if (!planMatchesDisclosure(plan, seen)) return
    await get().install()
  },

  previewManaged: async (args) => {
    const key = managedPreviewKey(args)
    if (get().managedPreviews[key]) return // 同一份只问一次（在途或已有结论）
    const epoch = projectEpoch
    const put = (v: ManagedPreview) => set({ managedPreviews: { ...get().managedPreviews, [key]: v } })
    put({ key, pending: true, plan: null, code: '' })
    try {
      const { plan } = await createDependencyPlan({ ...args, target: 'tavotto_managed' })
      if (epoch !== projectEpoch) return
      put({ key, pending: false, plan, code: '' })
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, pinned } = failure(e)
      put({ key, pending: false, plan: null, code })
      // 预读时才发现被全局固定：与形成计划被拒同一支（卡片切到「恢复自动检测」）
      if (pinned) set({ pinned })
    }
  },

  retry: async () => {
    const { request, authorized, scriptOffer } = get()
    if (!request || get().busy) return
    if (authorized && request.target === 'tavotto_managed') {
      await get().installNow(request, authorized, scriptOffer)
      return
    }
    set({ progress: null })
    await get().makePlan(request, scriptOffer)
  },

  install: async () => {
    const plan = get().plan
    if (!plan || get().busy) return
    const epoch = projectEpoch
    startedPlans.set(plan.plan_id, currentProjectId())
    set({ busy: true, errorCode: '', errorText: '' })
    if (plan.target_kind === 'tavotto_managed') {
      // 执行的就是这一份：之后的重试按它判「计划有没有超出用户看到的」
      set({
        authorized: {
          requirement: plan.requirement,
          // 用户在确认页看到的整份清单（重试沿用它：之后计划再多出的包同样要回到确认页）
          ...(plan.requirements && plan.requirements.length > 1 ? { requirements: plan.requirements } : {}),
          target_kind: 'tavotto_managed',
          private_python: plan.private_python ?? null,
        },
      })
    }
    try {
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来，中间那一下
      // 空窗期里按钮已经禁用了，界面却还什么都没说。
      // 目标 / 脚本 / 包名同样先带上：阶段数、那一句「正在安装 X」、脚本行认领都按它们，不等 SSE 第一条（Codex #742）
      set({
        progress: {
          plan_id: plan.plan_id,
          state: 'preparing',
          log: '',
          error: null,
          code: '',
          target_kind: plan.target_kind,
          import_name: plan.import_name,
          distribution: plan.distribution,
          script: get().request?.script,
        },
      })
      await installDependencyPlan(plan.plan_id)
      if (epoch !== projectEpoch) return
      set({ busy: false })
    } catch (e) {
      if (epoch !== projectEpoch) return lateFailure(plan.plan_id, e)
      const { code, text, pinned } = failure(e)
      set({ busy: false, progress: null, errorCode: code, errorText: text, pinned })
    }
  },

  adoptUserEnvironment: async (id, script) => {
    if (get().busy) return
    const owner = currentProjectId()
    const epoch = projectEpoch
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      const res = await setProjectUserEnvironment(id, script)
      // 采用记在 A 上（后端按请求那一刻的 pj）；`res.project` 是 A 的环境状态，不许写进 B 的 env，也不许关 B 的框、
      // 重排 B 的渲染——结局交给所属项目（与改用已有解释器同一条 `settleEnvChange`）：开着就当场落地，切走了就停放，
      // 回来时按此刻重新读环境、核实生效了才重跑（Codex #760：改用成功后，发起的脚本行也要重跑）
      const switched = epoch !== projectEpoch
      if (!switched) useEnvStore.getState().dismissDependencyPreparation()
      await settleEnvChange(
        owner,
        {
          kind: 'adopt',
          module: '',
          script,
          peers: [],
          scriptOffer: null,
          gate: 'needs_preparation',
          outcome: {
            ok: true,
            expectPython: res.project?.python ?? '',
            apply: () => {
              const env = useEnvStore.getState().env
              if (env) useEnvStore.setState({ env: { ...env, project: res.project } })
              else void useEnvStore.getState().refresh()
            },
          },
        },
        switched,
      )
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
    }
  },

  adoptSystemPython: async (python, module, script, scriptOffer = null, candidate = null) => {
    if (get().busy) return
    const owner = currentProjectId()
    const epoch = projectEpoch
    // 同样缺这个包的其它行此刻就记下：切走再切回之后 scriptRunStore 已清空，只能按这份名单认
    const peers = scriptsMissing(module).filter((s) => s !== script)
    set({ busy: true, errorCode: '', errorText: '' })
    let change: EnvChange
    try {
      // 直接问后端、自己拿**带标签的**结局：`envStore.setProjectPython` 换代之后成败都回 null，分不出来
      // 带着绑定的目标走与环境建议同一个代次绑定的端点：环境在看到之后被重建 → 409，不采用另一代
      const res = candidate
        ? await adoptEnvironmentCandidate(candidate, script, module)
        : await setProjectEnvironment(python, module)
      change = {
        kind: 'adopt', module, script, peers, scriptOffer,
        outcome: {
          ok: true,
          expectPython: res.project?.python ?? python,
          apply: () => {
            const env = useEnvStore.getState().env
            if (env) useEnvStore.setState({ env: { ...env, project: res.project } })
            else void useEnvStore.getState().refresh()
          },
        },
      }
    } catch (e) {
      // 与 `envStore.adoptCandidate` 同一个 409 语义：看到建议之后环境被重建 / 候选没了，建议已过期，重新拿一份
      if (candidate && e instanceof ApiError && (e.body?.code === 'environment_changed' || e.body?.code === 'environment_candidate_gone'))
        void useEnvStore.getState().refresh()
      change = {
        kind: 'adopt', module, script, peers, scriptOffer,
        outcome: { ok: false, error: e instanceof Error ? backendErrorText(e) : t('engine.setPythonFailed', { ns: 'errors' }) },
      }
    }
    await settleEnvChange(owner, change, epoch !== projectEpoch)
  },

  clearPinnedInterpreter: async (module, script, pinned = null, scriptOffer = null) => {
    const owner = currentProjectId()
    const epoch = projectEpoch
    const peers = scriptsMissing(module).filter((s) => s !== script)
    let change: EnvChange
    try {
      const env = await setEngineEnvironment(null)
      change = {
        kind: 'unpin', module, script, peers, scriptOffer,
        outcome: {
          ok: true,
          expectPython: pinned?.python ?? '',
          pinnedSource: pinned?.source,
          apply: () => {
            useEnvStore.setState({ env })
            if (!env.project) void useEnvStore.getState().refresh()
          },
        },
      }
    } catch (e) {
      change = {
        kind: 'unpin', module, script, peers, scriptOffer,
        outcome: { ok: false, error: e instanceof Error ? backendErrorText(e) : t('engine.setPythonFailed', { ns: 'errors' }) },
      }
    }
    // 当场（所属项目没离开过）失败：错误交回卡片自己的那一行说；其余一律走 `settleEnvChange`
    if (!change.outcome.ok && epoch === projectEpoch) {
      return change.outcome.error
    }
    await settleEnvChange(owner, change, epoch !== projectEpoch)
    return null
  },

  cancel: async () => {
    const id = get().progress?.plan_id
    if (!id) return
    try {
      await cancelDependencyPlan(id)
    } catch {
      // 取消与「装完了」天然赛跑，输了不是错误
    }
  },

  rebuildManaged: async () => {
    const owner = currentProjectId()
    if (get().busy || get().rebuildRunningFor(owner)) return
    const epoch = projectEpoch
    // 所属项目、单飞、乐观进度**全在发请求之前**落定（#605 评审第二轮 P1）：后端的线程可能比 POST 的响应先
    // 推进度——所属登记在 await 之后的话，那几条会被当成「不是自己起的」丢掉。进度 id 也在这里生成（#606）
    const id = newRebuildProgressId()
    startedPlans.set(id, owner)
    const started: DependencyProgress = {
      plan_id: id,
      state: 'creating_env',
      log: '',
      error: null,
      code: '',
      target_kind: 'tavotto_managed',
    }
    set({
      busy: true,
      errorCode: '',
      errorText: '',
      rebuilding: { ...get().rebuilding, [projectKey(owner)]: id },
      progress: started,
    })
    try {
      await rebuildManagedEnvironment(id)
      // 进度此后只由 SSE 推进（切走时 `clear()` 已经把它收进 A 那格），这里不再写进度
      if (epoch !== projectEpoch) return
      set({ busy: false })
    } catch (e) {
      // 请求失败**不等于没起来**（#606 第 5 条）：网络层断在响应上时，后端可能已经在建了。先拿同一个 id 问实况，
      // 在跑 / 已有结局就照实接回来；确认没到过后端（idle）或连实况都问不到，才当作没起来
      let actual: DependencyProgress | null = null
      try {
        actual = await fetchDependencyState(id)
      } catch {
        actual = null
      }
      if (actual && actual.plan_id === id && actual.state !== 'idle') {
        adoptActualRebuild(owner, actual)
        return
      }
      // 没起来：结局交给所属那格（此刻开着就是当前卡片），撤掉乐观进度与这个项目的单飞
      if (epoch !== projectEpoch) lateFailure(id, e)
      releaseRebuild(owner, id)
      if (epoch !== projectEpoch) return
      const { code, text } = failure(e)
      set({ busy: false, progress: null, errorCode: code, errorText: text })
    }
  },

  onProgress: (p) => {
    // 重建到了终局：那个项目的单飞放开——不管此刻开着哪个项目
    if (TERMINAL.includes(p.state)) {
      const owner = startedPlans.get(p.plan_id)
      if (owner !== undefined) releaseRebuild(owner, p.plan_id)
    }
    // 作业属于**别的项目**（本标签页起的、起完切走了）：进度只更新它那一格，此刻的界面与副作用
    // （刷环境 / 重排渲染 / 关框 / 错误文案）一个都不碰——那些说的都是此刻开着的项目（issue #590）
    // 同一条规则（`deliverToOwner`）：所属项目此刻开着（含 A → B → A 已经切回）就往下走、当场派发；不是就停放
    const owner = startedPlans.get(p.plan_id)
    if (
      owner !== undefined &&
      !deliverToOwner(
        owner,
        () => {},
        () => {
          if (get().parked[projectKey(owner)]?.plan_id === p.plan_id)
            set({ parked: { ...get().parked, [projectKey(owner)]: keepTarget(p, get().parked[projectKey(owner)]) } })
        },
      )
    )
      return
    // 只认**自己发起的**那条：单包计划 / 联合计划 / 自己点的重建（三处都在发请求之前就把 id 记下了）。
    // `engine.dependency` 不带项目判别、广播给每个订阅者——别的标签页 / 项目的计划装完，不能收掉
    // 这里的授权框、也不能把这里的渲染重排（Codex #470 P2）。
    const { plan, jointPlan, progress } = get()
    const owned = p.plan_id === plan?.plan_id || p.plan_id === jointPlan?.plan_id || p.plan_id === progress?.plan_id
    if (!owned) return
    set({ progress: keepTarget(p, progress) })
    if (p.state === 'done' || p.state === 'failed' || p.state === 'cancelled') {
      // 环境那半边变了（换了解释器 / 建了受管环境），刷一次环境状态
      void useEnvStore.getState().refresh()
      let rerun = false
      if (p.state === 'done') {
        // **装完必须把那次失败的渲染重新排上**，否则这条主路走不完：
        // 失败那次的 wantPatches 仍等于当前 overrides，同步器会跳过它，
        // 卡片就一直停在「缺 X」上，图要等到用户改点别的或刷新才出来
        // （Codex 评审 P1）。后端那半边已经作废了 worker，这里补前端这半边。
        useRenderStore.getState().retryEnvironmentFailures()
        // 素材库「脚本」行上因缺包停下的那次运行同样重跑（图还没上画布时，修复入口在脚本行上）：
        // 只重跑这份计划所属的脚本，且只在它此刻仍停在 missing_dependency 时——用户已经重跑 /
        // 收起过的不动
        // 从脚本行发起、中途切过项目的（#729）：那次停在缺包上的运行已随切项目清空，按收放回来的 offer 认
        const { request, scriptOffer } = get()
        rerun = rerunScriptAfterRepair(p.script, !!scriptOffer && request?.script === p.script)
        // 同一个包缺在别的脚本上（素材库只给它们挂了一张卡）：装进的是同一个项目环境，一起重跑
        rerunSameModule(p.import_name, p.script, request?.script === p.script ? scriptOffer?.peers : undefined)
        // 联合准备装完：授权框收掉（渲染会重排；缺的那一次错误也随之清）；素材库脚本行上停在这道门上的
        // 那次试运行重跑——试运行撞上的门，授权后应当直接出图（Windows 真机验收 main 493a1310）
        if (p.flow === 'joint') {
          const script = p.script || useEnvStore.getState().dependencyPreparation?.script
          useEnvStore.getState().dismissDependencyPreparation()
          if (script) useScriptRunStore.getState().rerunGated('needs_preparation', script)
        }
      }
      if (p.state !== 'done') {
        set({ errorCode: p.code || '', errorText: p.error || '', pinned: p.pinned ?? null })
      }
      // 计划是一次性的：成功也好失败也好，都不该留着一个已经被消费掉的
      // plan_id 让用户再点一次「安装」。预读的那些同理：环境刚变过（可能已经有了私有 Python），它们说的下载
      // 已经不算数，下一张卡要按新环境重读
      set({ plan: null, jointPlan: null, ...(p.state === 'done' ? { managedPreviews: {} } : {}) })
      // 从脚本行起的修复：那一行已经重跑、卡片随之收起，「已安装」这条进度没有地方再「知道了」——
      // 留着它，别的脚本行会因为「修复属于别人」一直不给卡片
      if (rerun) get().reset()
    }
  },

  reset: () =>
    set({
      plan: null,
      progress: null,
      busy: false,
      errorCode: '',
      errorText: '',
      pinned: null,
      jointPlan: null,
      jointBlocked: null,
      jointScript: '',
      jointOffer: null,
      request: null,
      authorized: null,
      scriptOffer: null,
    }),

  clear: () => {
    // 换代**排在清空之前**：清空只处置已经落地的那份，换代处置还在飞的那些
    projectEpoch += 1
    const { progress, parked, request, authorized, scriptOffer, jointScript, jointOffer } = get()
    const next = { ...parked }
    // 此刻显示的作业收进它**所属**项目那格（`resetForNewProject` 跑的时候 currentProjectId 已经是新项目，
    // 所属项目只能问作业自己）。认不出所属的（不是本标签页起的）不收——本来也不该显示
    if (progress && startedPlans.has(progress.plan_id)) {
      const owner = projectKey(startedPlans.get(progress.plan_id) ?? null)
      next[owner] = progress
      parkedRetry.set(owner, { request, authorized, scriptOffer, jointScript, jointOffer })
    }
    // 新项目上次切走时收着的作业放回来：还在跑就接着显示，切走期间结束了就把结局交出来（不静默丢）
    const here = projectKey(currentProjectId())
    const back = next[here] ?? null
    delete next[here]
    const retryCtx = back ? parkedRetry.get(here) : undefined
    parkedRetry.delete(here)
    const ended = !!back && (back.state === 'failed' || back.state === 'cancelled')
    set({
      plan: null,
      jointPlan: null,
      jointBlocked: null,
      jointScript: retryCtx?.jointScript ?? '',
      jointOffer: retryCtx?.jointOffer ?? null,
      pinned: null,
      managedPreviews: {},
      request: retryCtx?.request ?? null,
      authorized: retryCtx?.authorized ?? null,
      scriptOffer: retryCtx?.scriptOffer ?? null,
      busy: false,
      errorCode: ended ? back.code || '' : '',
      errorText: ended ? back.error || '' : '',
      progress: back,
      parked: next,
    })
    // 从脚本行发起的修复在切走期间装好了（#729）：终态副作用当时不在别的项目上派发，切回来补上与「没切走」
    // 同一件事——重跑那一行（同一条 `rerunScriptAfterRepair`）、收起卡片。收起之后这份作业不再被收放，
    // 再切走切回不会重复触发
    // 切走期间「改用已有解释器 / 清掉全局固定」回来了（停放在这一格）：与装好之后同一件事，补跑那几行
    const pend = pendingEnvChanges.get(here)
    if (pend) {
      pendingEnvChanges.delete(here)
      // 回到所属项目：失败的只说那一句、不重跑；成功的按此刻重新读一次环境、核实生效了才重跑
      void applyEnvChange(here === '' ? null : here, pend, true)
    }
    // 联合准备统一在弹窗里：切回所属项目时用停放的载荷恢复同一份进度 / 取消 / 重试。
    if (back && back.flow === 'joint' && back.state !== 'done' && retryCtx?.jointOffer)
      useEnvStore.getState().requestDependencyPreparation(retryCtx.jointOffer, currentProjectId())
    if (back?.state === 'done' && (retryCtx?.scriptOffer || retryCtx?.jointScript)) {
      const script = back.script ?? retryCtx.request?.script ?? retryCtx.jointScript
      // 同样缺这个包的其它行一起补跑（与没切走时 `rerunSameModule` 同一件事；那几行的运行记录也随切项目清掉了）
      rerunSameModule(undefined, script, retryCtx.scriptOffer?.peers)
      if (rerunScriptAfterRepair(script, true)) get().reset()
    }
  },
}))

/**
 * 一次重建的进度 id：32 位小写十六进制（与后端 `deprepair.REBUILD_PROGRESS_ID_RE` 是一对同源，格式不对后端拒收）。
 * 每次重建一个——两个项目同时重建时进度各归各的，同一项目先后两次也不会把上一次的终态认成这一次的（#606）
 */
export function newRebuildProgressId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 这个项目的重建单飞放开（只放**这一次**的：同一项目已经起了新的一次就不动它） */
function releaseRebuild(owner: string | null, id: string): void {
  const rebuilding = useDepRepairStore.getState().rebuilding
  const key = projectKey(owner)
  if (rebuilding[key] !== id) return
  const next = { ...rebuilding }
  delete next[key]
  useDepRepairStore.setState({ rebuilding: next })
}

/**
 * POST 失败、但后端说这次重建其实起了（#606 第 5 条）：照 SSE 进度的规矩把实况接回来——在跑就接着显示、
 * 单飞保持；已有结局就交给 `onProgress`（放开单飞、派发终局）。所属项目此刻不开着，就收进它那一格。
 */
function adoptActualRebuild(owner: string | null, actual: DependencyProgress): void {
  const store = useDepRepairStore.getState()
  // 同一条规则：所属项目此刻开着（含 A → B → A 已经切回）就接到当前卡片上，否则停放到它那格
  deliverToOwner(
    owner,
    () => useDepRepairStore.setState({ busy: false, progress: actual }),
    () => useDepRepairStore.setState({ parked: { ...store.parked, [projectKey(owner)]: actual } }),
  )
  if (TERMINAL.includes(actual.state)) useDepRepairStore.getState().onProgress(actual)
}
const TERMINAL: readonly DependencyProgress['state'][] = ['done', 'failed', 'cancelled']

/**
 * 切项目之后才失败的那次执行请求（install / prepare 的 POST 被拒）：作业没起来，它所属项目那格要说出
 * 结局，而不是永远停在乐观的 preparing 上。B 的界面不动。
 */
function lateFailure(planId: string, e: unknown): void {
  const owner = startedPlans.get(planId)
  if (owner === undefined) return
  const { code, text } = failure(e)
  const store = useDepRepairStore.getState()
  // 同一条规则（`deliverToOwner`）。A → B → A 之后才被拒：作业已经被 `clear()` 放回当前的 `progress`，`parked`
  // 里没有它了（#605 评审 P2）——所属项目此刻开着，就在当前卡片上说出结局；不开着就记进它那格
  deliverToOwner(
    owner,
    () => {
      if (store.progress?.plan_id !== planId) return
      useDepRepairStore.setState({
        busy: false,
        progress: { ...store.progress, state: 'failed', code, error: text },
        errorCode: code,
        errorText: text,
      })
    },
    () => {
      const key = projectKey(owner)
      const p = store.parked[key]
      if (!p || p.plan_id !== planId) return
      useDepRepairStore.setState({ parked: { ...store.parked, [key]: { ...p, state: 'failed', code, error: text } } })
    },
  )
}

/**
 * 修好之后把素材库里因缺这个包停下的那次脚本运行重跑一遍（见 `onProgress` 的 done 分支）。
 * `fromScriptRow`：修复是从这一行发起、offer 随作业收放回来的（#729）——切项目把那次运行清空了，
 * 这一行此刻**没有运行记录**也算「仍停在缺包上」；用户切回来之后自己跑过（有记录）就不动
 */
function rerunScriptAfterRepair(script: string | undefined, fromScriptRow = false): boolean {
  if (!script) return false
  const runs = useScriptRunStore.getState()
  const phase = runs.byScript[script]?.phase
  if (phase !== 'missing_dependency' && !(fromScriptRow && phase === undefined)) return false
  void runs.run(script)
  return true
}

/**
 * 项目的环境刚换过（改用了已有的解释器 / 清掉了全局固定），不是装包作业、没有进度可等：素材库里停在缺这个包上的
 * 脚本行——发起的那一行与同样缺它的其它行——现在就重跑（只认仍停在 `missing_dependency` 的，用户已经重跑 / 收起过的不动）。
 * 与装好之后 `onProgress` 那半边同一件事
 */
export function rerunAfterEnvironmentChange(
  script: string | undefined,
  importName: string | undefined,
  peers: string[] = [],
  /** 发起之后切过项目（A → B → A）：运行记录随切项目清空了，发起行「没有记录」也算仍停在缺包上（同 #729） */
  afterSwitch = false,
): void {
  rerunScriptAfterRepair(script, afterSwitch)
  rerunSameModule(importName, script, peers)
}

/**
 * 环境改动的结局交给所属项目（同 `deliverToOwner` 那一条规则）：所属项目开着就当场落地，不开着就整份停放，
 * 回到它时 `clear()` 取出来落地。`switched` = 发请求之后换过代（含 A → B → A 已经切回）：那时响应里的环境属于
 * 发请求那一刻，不直接写，改为按此刻重新读一次再核实
 */
async function settleEnvChange(owner: string | null, change: EnvChange, switched: boolean): Promise<void> {
  let run: Promise<void> | null = null
  deliverToOwner(
    owner,
    () => {
      run = applyEnvChange(owner, change, switched)
    },
    () => {
      pendingEnvChanges.set(projectKey(owner), change)
    },
  )
  if (run) await run
}

/** 环境改动落地到所属项目（此刻开着）。`verify`：先按此刻重新读环境、核实生效了才算成功 */
async function applyEnvChange(owner: string | null, change: EnvChange, verify: boolean): Promise<void> {
  const store = useDepRepairStore
  const fail = (error: string) =>
    store.setState({
      busy: false,
      errorCode: '',
      errorText: error,
      // 从脚本行发起的：运行记录可能随切项目清掉了，卡片靠这份 offer 挂回那一行、把失败那一句说出来
      ...(change.scriptOffer && change.script ? { scriptOffer: { ...change.scriptOffer, script: change.script } } : {}),
    })
  const outcome = change.outcome
  if (!outcome.ok) {
    fail(outcome.error)
    return
  }
  if (verify) {
    const epoch = projectEpoch
    await useEnvStore.getState().refresh()
    // 核实期间又切走了：整份再停放回去，回来再核
    if (epoch !== projectEpoch || !ownerIsCurrent(owner)) {
      pendingEnvChanges.set(projectKey(owner), change)
      return
    }
    const env = useEnvStore.getState().env
    const applied =
      change.kind === 'adopt'
        ? env?.project?.python === outcome.expectPython
        : !!env && !(env.source === outcome.pinnedSource && env.python === outcome.expectPython)
    if (!applied) {
      fail(t('engine.repairEnvNotApplied', { ns: 'errors' }))
      return
    }
  } else {
    outcome.apply()
  }
  // 生效了：这张卡的前提没了（清固定时连同记下的那条固定一起清），失败的渲染重排，停在缺包上的那几行重跑
  if (change.kind === 'unpin') store.getState().reset()
  else store.setState({ busy: false })
  useRenderStore.getState().retryEnvironmentFailures()
  rerunAfterEnvironmentChange(change.script, change.module, change.peers, verify)
  if (change.gate) useScriptRunStore.getState().rerunGated(change.gate, change.script)
}

/** 脚本行发起时记下同样缺这个包的其它脚本（重试时沿用第一次记下的：那时运行记录可能已随切项目清掉） */
function withPeers(scriptOffer: ScriptRepairOffer | null, script: string): ScriptRepairOffer | null {
  if (!scriptOffer || scriptOffer.peers) return scriptOffer
  const importName = scriptOffer.offer.import_name || scriptOffer.module
  return { ...scriptOffer, peers: scriptsMissing(importName).filter((s) => s !== script) }
}

/** 此刻停在 `missing_dependency`、缺的正是这个 import 的脚本 */
function scriptsMissing(importName: string): string[] {
  return Object.entries(useScriptRunStore.getState().byScript)
    .filter(([, state]) => {
      if (state.phase !== 'missing_dependency') return false
      return (state.error?.dependency_repair?.import_name ?? state.error?.params?.module) === importName
    })
    .map(([script]) => script)
}

/**
 * 装好一个包之后，素材库里**因缺同一个包**停下的其它脚本也重跑一遍（2026-09-29：同一原因失败的几行只挂一张卡，
 * 修好一次就该全好）。只认此刻仍停在 `missing_dependency`、且缺的正是这个 import 的那几行
 */
function rerunSameModule(importName: string | undefined, except: string | undefined, peers: string[] = []): void {
  const runs = useScriptRunStore.getState()
  // 此刻看得出也缺它的，加上发起时记下的那几行（切过项目的话运行记录已清空，只能按名单认——那时它们没有运行记录，
  // 与 `rerunScriptAfterRepair` 的 fromScriptRow 同一条判据：没记录或仍停在缺包上才跑；用户切回来之后自己跑过的不动）
  const targets = new Set(importName ? scriptsMissing(importName) : [])
  for (const peer of peers) {
    const phase = runs.byScript[peer]?.phase
    if (phase === undefined || phase === 'missing_dependency') targets.add(peer)
  }
  for (const script of targets) if (script !== except) void runs.run(script)
}

/**
 * 新一条进度没带目标时沿用上一条的（同一个 plan_id）：阶段数按目标定，某一条快照缺字段不能让界面在
 * 「两步」与「四步」之间跳（Codex #742）
 */
function keepTarget(next: DependencyProgress, prev: DependencyProgress | null | undefined): DependencyProgress {
  if (next.target_kind || !prev?.target_kind || prev.plan_id !== next.plan_id) return next
  return { ...next, target_kind: prev.target_kind }
}

/** 安装是不是正在进行（界面据此禁用按钮、显示进度而不是选项） */
export const isRepairRunning = (p: DependencyProgress | null): boolean =>
  !!p && ['preparing', 'downloading_python', 'creating_env', 'installing', 'verifying'].includes(p.state)
