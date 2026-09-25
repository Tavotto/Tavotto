import { create } from 'zustand'
import {
  cancelDependencyPlan,
  cancelJointDependencies,
  createDependencyPlan,
  createJointDependencyPlan,
  installDependencyPlan,
  prepareJointDependencies,
  rebuildManagedEnvironment,
  setProjectUserEnvironment,
  skipDependencyPreparation,
  type DependencyProgress,
  type DependencyRepairPlan,
  type InterpreterPin,
  type JointDependencyPlan,
  type JointDependencyRepairPlan,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'

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
const projectKey = (project: string | null): string => project ?? ''

/**
 * 受控依赖修复的界面状态（ADR 0019）。
 *
 * 两步是**故意的**，不是流程繁琐：
 *
 *   1. `plan(target)` —— 问后端「装什么、装到哪、会不会改你的环境」。
 *      这一步什么都不装，用户看到的确认文案就来自它。
 *   2. `install()` —— 执行**那个计划**（只发 plan_id）。
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
  ) => Promise<void>
  install: () => Promise<void>
  /**
   * 采用这台机器上已有的、已经装着那个包的解释器（ADR 0044）。**不是安装**：
   * 走项目环境 PATCH（带 `module` 让后端连那个包一起验），成功后把失败的
   * 渲染重新排上——与装完包之后那半边同一件事。
   */
  adoptSystemPython: (python: string, module: string) => Promise<void>
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

  // ---- 联合准备（U04，ADR 0061）：跑前的那一次授权 ----
  // 载荷本身（整份联合计划 + 可选目标）住在 `envStore.dependencyPreparation`（与运行目录的确认
  // 同一个家；渲染 store 静态 import 得到它，本 store 反向 import 会成环）。这里只管执行。
  /** 绑定好的联合计划（不装）；null = 还没到执行那一步 */
  jointPlan: JointDependencyRepairPlan | null
  /** 计划绑定不了（blocked / 什么都不缺）时后端交回的计划——界面按 blocked 的理由说下一步 */
  jointBlocked: JointDependencyPlan | null
  /** 一步：绑定计划 → 执行（只发 plan_id）。目标由用户在框里选；脚本来自 envStore 里的载荷。 */
  prepare: (target: 'project_venv' | 'tavotto_managed') => Promise<void>
  cancelPreparation: () => Promise<void>
  /** 「不准备，直接运行」：明确的 skip（这道门一直问到有答案），然后关框并重排那次失败的渲染 */
  skipPreparation: () => Promise<void>
}

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
  parked: {},

  prepare: async (target) => {
    const offer = useEnvStore.getState().dependencyPreparation
    if (!offer || get().busy) return
    const epoch = projectEpoch
    let planId = ''
    set({ busy: true, errorCode: '', errorText: '', jointBlocked: null })
    try {
      const { plan } = await createJointDependencyPlan({ script: offer.script, target })
      // 绑定回来时已经切了项目：这是 A 的计划，不在 B 上执行（绑定不装，丢掉即可）
      if (epoch !== projectEpoch) return
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

  skipPreparation: async () => {
    const offer = useEnvStore.getState().dependencyPreparation
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
  },

  makePlan: async (args) => {
    if (get().busy) return
    const epoch = projectEpoch
    set({ busy: true, errorCode: '', errorText: '', plan: null })
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

  install: async () => {
    const plan = get().plan
    if (!plan || get().busy) return
    const epoch = projectEpoch
    startedPlans.set(plan.plan_id, currentProjectId())
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来，中间那一下
      // 空窗期里按钮已经禁用了，界面却还什么都没说。
      set({ progress: { plan_id: plan.plan_id, state: 'preparing', log: '', error: null, code: '' } })
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
    const epoch = projectEpoch
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      const res = await setProjectUserEnvironment(id, script)
      // 采用记在 A 上（后端按请求那一刻的 pj）；`res.project` 是 A 的环境状态，不许写进 B 的 env，
      // 也不许关 B 的框、重排 B 的渲染
      if (epoch !== projectEpoch) return
      const envStore = useEnvStore.getState()
      const env = envStore.env
      if (env) useEnvStore.setState({ env: { ...env, project: res.project } })
      else await envStore.refresh()
      set({ busy: false })
      envStore.dismissDependencyPreparation()
      useRenderStore.getState().retryEnvironmentFailures()
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
    }
  },

  adoptSystemPython: async (python, module) => {
    if (get().busy) return
    const epoch = projectEpoch
    set({ busy: true, errorCode: '', errorText: '' })
    const error = await useEnvStore.getState().setProjectPython(python, module)
    if (epoch !== projectEpoch) return
    if (error) {
      // `setProjectPython` 已经把后端原文翻成一句话；code 由环境 store 吞掉了，
      // 这里只有原文可显示——它本来就是 `backendErrorText()` 按 code 翻好的。
      set({ busy: false, errorCode: '', errorText: error })
      return
    }
    set({ busy: false })
    useRenderStore.getState().retryEnvironmentFailures()
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
    if (get().busy) return
    const epoch = projectEpoch
    // 所属项目在发请求**之前**记：响应回来时可能已经切到 B
    const owner = currentProjectId()
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      await rebuildManagedEnvironment()
      startedPlans.set(REBUILD_ID, owner)
      const started: DependencyProgress = { plan_id: REBUILD_ID, state: 'creating_env', log: '', error: null, code: '' }
      if (epoch !== projectEpoch) {
        // 重建在 A 上起了、界面已经在 B：进度收进 A 那格，B 不显示
        set({ parked: { ...get().parked, [projectKey(owner)]: started } })
        return
      }
      set({ busy: false, progress: started })
    } catch (e) {
      if (epoch !== projectEpoch) return
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
    }
  },

  onProgress: (p) => {
    // 作业属于**别的项目**（本标签页起的、起完切走了）：进度只更新它那一格，此刻的界面与副作用
    // （刷环境 / 重排渲染 / 关框 / 错误文案）一个都不碰——那些说的都是此刻开着的项目（issue #590）
    const owner = startedPlans.get(p.plan_id)
    if (owner !== undefined && projectKey(owner) !== projectKey(currentProjectId())) {
      if (get().parked[projectKey(owner)]?.plan_id === p.plan_id)
        set({ parked: { ...get().parked, [projectKey(owner)]: p } })
      return
    }
    // 只认**自己发起的**那条：单包计划 / 联合计划 / 自己点的重建（三处都在发请求之前就把 id 记下了）。
    // `engine.dependency` 不带项目判别、广播给每个订阅者——别的标签页 / 项目的计划装完，不能收掉
    // 这里的授权框、也不能把这里的渲染重排（Codex #470 P2）。
    const { plan, jointPlan, progress } = get()
    const owned = p.plan_id === plan?.plan_id || p.plan_id === jointPlan?.plan_id || p.plan_id === progress?.plan_id
    if (!owned) return
    set({ progress: p })
    if (p.state === 'done' || p.state === 'failed' || p.state === 'cancelled') {
      // 环境那半边变了（换了解释器 / 建了受管环境），刷一次环境状态
      void useEnvStore.getState().refresh()
      if (p.state === 'done') {
        // **装完必须把那次失败的渲染重新排上**，否则这条主路走不完：
        // 失败那次的 wantPatches 仍等于当前 overrides，同步器会跳过它，
        // 卡片就一直停在「缺 X」上，图要等到用户改点别的或刷新才出来
        // （Codex 评审 P1）。后端那半边已经作废了 worker，这里补前端这半边。
        useRenderStore.getState().retryEnvironmentFailures()
        // 联合准备装完：授权框收掉（渲染会重排；缺的那一次错误也随之清）
        if (p.flow === 'joint') useEnvStore.getState().dismissDependencyPreparation()
      }
      if (p.state !== 'done') {
        set({ errorCode: p.code || '', errorText: p.error || '', pinned: p.pinned ?? null })
      }
      // 计划是一次性的：成功也好失败也好，都不该留着一个已经被消费掉的
      // plan_id 让用户再点一次「安装」。
      set({ plan: null, jointPlan: null })
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
    }),

  clear: () => {
    // 换代**排在清空之前**：清空只处置已经落地的那份，换代处置还在飞的那些
    projectEpoch += 1
    const { progress, parked } = get()
    const next = { ...parked }
    // 此刻显示的作业收进它**所属**项目那格（`resetForNewProject` 跑的时候 currentProjectId 已经是新项目，
    // 所属项目只能问作业自己）。认不出所属的（不是本标签页起的）不收——本来也不该显示
    if (progress && startedPlans.has(progress.plan_id))
      next[projectKey(startedPlans.get(progress.plan_id) ?? null)] = progress
    // 新项目上次切走时收着的作业放回来：还在跑就接着显示，切走期间结束了就把结局交出来（不静默丢）
    const here = projectKey(currentProjectId())
    const back = next[here] ?? null
    delete next[here]
    const ended = !!back && (back.state === 'failed' || back.state === 'cancelled')
    set({
      plan: null,
      jointPlan: null,
      jointBlocked: null,
      pinned: null,
      busy: false,
      errorCode: ended ? back.code || '' : '',
      errorText: ended ? back.error || '' : '',
      progress: back,
      parked: next,
    })
  },
}))

/** 重建受管环境的进度 id（后端 `deprepair.REBUILD_PROGRESS_ID`，每个项目都是这一个） */
const REBUILD_ID = 'managed-rebuild'

/**
 * 切项目之后才失败的那次执行请求（install / prepare 的 POST 被拒）：作业没起来，它所属项目那格要说出
 * 结局，而不是永远停在乐观的 preparing 上。B 的界面不动。
 */
function lateFailure(planId: string, e: unknown): void {
  const owner = startedPlans.get(planId)
  if (owner === undefined) return
  const key = projectKey(owner)
  const parked = useDepRepairStore.getState().parked
  const p = parked[key]
  if (!p || p.plan_id !== planId) return
  const { code, text } = failure(e)
  useDepRepairStore.setState({ parked: { ...parked, [key]: { ...p, state: 'failed', code, error: text } } })
}

/** 安装是不是正在进行（界面据此禁用按钮、显示进度而不是选项） */
export const isRepairRunning = (p: DependencyProgress | null): boolean =>
  !!p && ['preparing', 'creating_env', 'installing', 'verifying'].includes(p.state)
