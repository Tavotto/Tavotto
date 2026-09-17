import { create } from 'zustand'
import {
  cancelPackageJob,
  fetchManagedPackages,
  fetchPackageJob,
  lookupPackage,
  planPackageJob,
  runPackageJob,
  type ManagedPackages,
  type PackageJob,
  type PackageLookup,
  type PackageOp,
  type PackageProgress,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'

/**
 * 设置 → 包管理的界面状态（ADR 0038）。
 *
 * 目标环境只有当前项目的 Tavotto 受管环境，磁盘上的一切都在后端
 * （`engine/deprepair.py` 的作业模型）；这里只有「清单 + 当前作业 + 错误」。
 *
 * 两步是刻意的：`plan(op, spec)` 什么都不改，把「会发生什么」交回来
 * （卸载时是「谁依赖它」）；`run(jobId)` 才执行——请求体里只有 job_id。
 * 进度经 SSE `engine.package` 推过来，SSE 断了由 `poll()` 补拉。
 * 界面**按 state 换文案，不解析日志**——日志只在「详细日志」里原样显示。
 */
/**
 * 本标签页经 `run()` 起过的作业：作业号 → 起它那一刻本标签页认领的项目
 * （`currentProjectId()`）。进度事件只认这里面的作业号；「这个作业属于哪个项目」
 * 是作业自己的一个字段（issue #309），不靠「此刻开着哪个项目」隐含——作业还在
 * 后端跑的时候用户可以切走再切回来。
 */
const startedJobs = new Map<string, string | null>()

/** `jobs` 表的键：没有项目时也要有个落点（那时后端不会放行作业，只是类型上要闭合） */
const jobKey = (project: string | null): string => project ?? ''

/**
 * 查找的序号。两次查找重叠时**只有最后一次的答案能落地**——先发的那次回来
 * 得晚的话，用户会看到自己刚刚已经改掉的那个名字的结果。比较查询串是不够的：
 * 同一个名字连点两次，两次的串一模一样。
 */
let lookupSeq = 0

/**
 * 项目代际。`clear()`（换项目）加一——A 项目的清单 / 查找响应在途时切到 B，
 * 落地必须作废（与 `scriptLibraryStore` / `runtimeAssetStore` / `scriptRunStore`
 * 同一条纪律，见 `docs/rules/frontend/asset-library.md`「三个 store 都有项目代际」那条）。
 *
 * **与 `lookupSeq` 是两条独立的轴，不能合并成一个**：序号答的是「同一个项目里
 * 哪一次查找最新」，代际答的是「这个响应属于哪个项目」。只有序号的话，A 项目
 * 那次查找在 B 里**仍然是最新的一次**——它会带着 A 环境里的 `installed` 版本
 * 与 A 的索引源落进 B 的包页面，而那一页的安装按钮作用在 B 上。
 *
 * 反过来也一样：`clear()` **只**换代、不动 `lookupSeq`。两件事各由一条判据
 * 负责，拆掉任何一条都要有用例红——两条都做同一件事的话，拆掉其中一条会照样
 * 全绿（同一条保证实现两遍 = 谁也杀不死）。
 */
let projectEpoch = 0

/** 查找的状态机。四态闭集，界面按它换文案。 */
export type LookupStatus = 'idle' | 'loading' | 'found' | 'error'

export interface LookupState {
  /** 这一次查的是哪个名字（用户可能已经把输入框改了，措辞要认这个） */
  query: string
  status: LookupStatus
  result: PackageLookup | null
  /** 失败的稳定 code（`package_lookup_*` 四档之一，或语法不合法那一条） */
  code: string
  /** 后端原文，没有对应文案时的回退 */
  text: string
}

const IDLE_LOOKUP: LookupState = { query: '', status: 'idle', result: null, code: '', text: '' }

/**
 * 过滤 / 查找用的名字：把版本约束切掉。
 *
 * 输入框是**一个**（既是安装规范，也是搜索词）。用户输入 `lmfit>=1.3` 时，
 * 拿整串去过滤清单会一个都匹配不上，拿它去查找会被后端按语法拒掉——两种都
 * 不是他想要的。切掉约束之后两件事都对：过滤 `lmfit`、查找 `lmfit`，而
 * **安装仍然用他输入的原串**（那才是他想装的东西）。
 */
export const searchTerm = (value: string): string =>
  value.trim().split(/[<>=!~,;[(]/, 1)[0]?.trim() ?? ''

interface PackageState {
  data: ManagedPackages | null
  loading: boolean
  /** 上一次加载失败的原文（保留上次成功的 data） */
  loadError: string
  /**
   * 每个项目最近一次作业的进度，按作业**所属**项目存（`run()` 那一刻的项目，issue #309）。
   * 没有条目 = 那个项目没起过作业或已经关掉。切项目**不清**它（作业还在后端跑，
   * `job_id` 是前端唯一的把手），切回去 `progressFor` 就接得上。
   */
  jobs: Readonly<Record<string, PackageProgress>>
  /** 某个项目的页面上该显示的作业；作业属于别的项目就是 null */
  progressFor: (project: string | null) => PackageProgress | null
  /** 关掉当前项目那条已经结束的作业条 */
  dismissJob: () => void
  /** 形成作业 / 发起执行期间的 busy（防连点） */
  busy: boolean
  errorCode: string
  errorText: string
  /** 「在 PyPI 查找」的结果。**出网的动作只有它**，且只由用户点击触发 */
  lookup: LookupState
  load: () => Promise<void>
  /** 形成作业（不改任何东西）；失败时把 code 记在 store 里并回 null */
  plan: (op: PackageOp, spec: string) => Promise<PackageJob | null>
  run: (jobId: string) => Promise<boolean>
  cancel: () => Promise<void>
  poll: () => Promise<void>
  onProgress: (p: PackageProgress) => void
  clearError: () => void
  /** 按名字问一次索引源。名字是**用户点下去那一刻**的那个，不是输入框的实时值 */
  runLookup: (name: string) => Promise<void>
  clearLookup: () => void
  /** 换项目：项目级状态整份丢掉 + 换代，在途响应绝不落进新项目 */
  clear: () => void
}

const failure = (e: unknown): { code: string; text: string } => {
  const body = (e as { body?: { code?: string; error?: string } })?.body
  const text = e instanceof Error ? e.message : ''
  return { code: body?.code || '', text: body?.error || text }
}

export const RUNNING_STATES: PackageProgress['state'][] = [
  'preparing',
  'creating_env',
  'installing',
  'verifying',
]

export const isPackageJobRunning = (p: PackageProgress | null): boolean =>
  !!p && RUNNING_STATES.includes(p.state)

/** 只改一个项目的那格；`null` = 删掉它 */
const withJob = (
  jobs: Readonly<Record<string, PackageProgress>>,
  project: string | null,
  p: PackageProgress | null,
): Readonly<Record<string, PackageProgress>> => {
  const next = { ...jobs }
  if (p) next[jobKey(project)] = p
  else delete next[jobKey(project)]
  return next
}

export const usePackageStore = create<PackageState>((set, get) => ({
  data: null,
  loading: false,
  loadError: '',
  jobs: {},
  busy: false,
  errorCode: '',
  errorText: '',
  lookup: IDLE_LOOKUP,

  load: async () => {
    const epoch = projectEpoch
    set({ loading: true })
    try {
      const data = await fetchManagedPackages()
      if (epoch !== projectEpoch) return // 切过项目：这份清单说的是旧项目的环境
      set({ data, loading: false, loadError: '' })
    } catch (e) {
      if (epoch !== projectEpoch) return
      // 保留上一次成功的清单：清空的话用户会看到「什么都没装」，那是假的
      set({ loading: false, loadError: failure(e).text })
    }
  },

  plan: async (op, spec) => {
    if (get().busy) return null
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      const { job } = await planPackageJob(op, spec)
      set({ busy: false })
      return job
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
      return null
    }
  },

  progressFor: (project) => get().jobs[jobKey(project)] ?? null,

  dismissJob: () => set({ jobs: withJob(get().jobs, currentProjectId(), null) }),

  run: async (jobId) => {
    if (get().busy) return false
    // 作业归起它那一刻的项目：之后的进度、取消、终态副作用都按这个字段派发
    const project = currentProjectId()
    startedJobs.set(jobId, project)
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来，那一下空窗期里
      // 按钮已经禁用了，界面却还什么都没说
      const preparing: PackageProgress = { job_id: jobId, state: 'preparing', log: '', error: null, code: '' }
      set({ jobs: withJob(get().jobs, project, preparing) })
      const res = await runPackageJob(jobId)
      const merged = { ...(get().progressFor(project) ?? preparing), ...res } as PackageProgress
      set({ busy: false, jobs: withJob(get().jobs, project, merged) })
      return true
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, jobs: withJob(get().jobs, project, null), errorCode: code, errorText: text })
      return false
    }
  },

  cancel: async () => {
    // 只取消**当前项目**的作业：别的项目的作业在这一页上根本不显示，后端也会按项目拒掉
    const id = get().progressFor(currentProjectId())?.job_id
    if (!id) return
    try {
      await cancelPackageJob(id)
    } catch {
      // 取消与「做完了」天然赛跑，输了不是错误
    }
  },

  poll: async () => {
    // 补拉同样只问当前项目的作业：`/job` 按项目核，问别的项目的只会得到 409
    const mine = get().progressFor(currentProjectId())
    const id = mine?.job_id
    if (!id || !isPackageJobRunning(mine)) return
    try {
      const p = await fetchPackageJob(id)
      if (p.job_id === id && p.state !== 'idle') get().onProgress(p)
    } catch {
      // 下一轮再问
    }
  },

  onProgress: (p) => {
    // 只认**自己起过**的作业：SSE 是全进程共享的一条流，同一后端下另一个项目的标签页
    // 也会收到这条进度。以前只挡「与当前作业不同」，空闲标签页（没有作业）
    // 会把别人的作业认领成自己的——终态时刷错项目的环境、进行中时露出别人的取消按钮
    // （评审 #228）。后端 cancel / job 也按项目核，这一层是前端自己的那一份。
    if (!p.job_id || !startedJobs.has(p.job_id)) return
    // 进度落到作业**所属**项目那一格，不落到此刻开着的项目上（issue #309）：
    // A 起的作业在 B 上收到进度，只更新 A 的把手，B 的页面上什么都不出现
    const project = startedJobs.get(p.job_id) ?? null
    set({ jobs: withJob(get().jobs, project, p) })
    if (p.state === 'done' || p.state === 'failed' || p.state === 'cancelled') {
      // 终态的副作用只在**此刻开着的就是作业所属项目**时派发：清单 / 环境状态 /
      // 渲染重排 / 错误文案说的都是「当前项目」，A 的作业做完了不该让 B 刷一遍，
      // 更不该把 A 的失败写成 B 的错误。切回 A 时页面挂载会自己 load()。
      if (project !== currentProjectId()) return
      if (p.state !== 'done') set({ errorCode: p.code || '', errorText: p.error || '' })
      // 环境那半边变了（建了环境 / 换了解释器 / 版本变了）：清单与环境状态都刷一次
      void get().load()
      void useEnvStore.getState().refresh()
      if (p.state === 'done') {
        // 装完把因缺包失败的渲染重新排上（与依赖修复同一条处置）
        useRenderStore.getState().retryEnvironmentFailures()
      }
    }
  },

  clearError: () => set({ errorCode: '', errorText: '' }),

  runLookup: async (name) => {
    const query = searchTerm(name)
    if (!query) return
    const seq = ++lookupSeq
    // 发请求那一刻的项目代际：答案回来时项目可能已经换了
    const epoch = projectEpoch
    set({ lookup: { query, status: 'loading', result: null, code: '', text: '' } })
    try {
      const result = await lookupPackage(query)
      if (seq !== lookupSeq) return // 有更新的一次查找在飞，这次的答案已经过期
      if (epoch !== projectEpoch) return // 这是上一个项目问的，答案不归这一页
      set({ lookup: { query, status: 'found', result, code: '', text: '' } })
    } catch (e) {
      if (seq !== lookupSeq || epoch !== projectEpoch) return
      const { code, text } = failure(e)
      // code 拿不到时也要有个落点：否则界面上是一句空白的失败
      set({ lookup: { query, status: 'error', result: null, code, text } })
    }
  },

  clearLookup: () => {
    // 序号也要往前走：正在飞的那次回来时不该覆盖用户刚清掉的结果
    lookupSeq += 1
    set({ lookup: IDLE_LOOKUP })
  },

  clear: () => {
    // 换代**排在清空之前**：清空只处置已经落地的那份，换代处置还在飞的那些
    projectEpoch += 1
    // 清单、查找结果、上一次的错误——三份说的都是旧项目那个环境。查找结果
    // 尤其危险：它带着 A 环境里的 `installed` 版本与 A 的索引源，而这一页的
    // 安装按钮作用在**当前**项目上（本轮评审 P2）。
    //
    // **`jobs` / 已起过的作业号刻意不动。** 那个作业改的是 A 的环境、还在
    // 后端跑着（切个项目不等于「我不要那次安装了」，与导出作业、native 会话
    // 同一条纪律，ADR 0021 §14），而 `job_id` 是前端手里唯一的把手——丢掉它
    // 就再也接不回去了。「B 的页面上不该显示 A 的作业」由 `jobs` 按作业所属
    // 项目分格 + `progressFor(project)` 解决（issue #309）：切回 A 就接得上。
    set({ data: null, loading: false, loadError: '', errorCode: '', errorText: '', lookup: IDLE_LOOKUP })
  },
}))
