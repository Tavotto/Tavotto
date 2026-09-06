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
/** 本标签页经 `run()` 起过的作业号：进度事件只认这里面的 */
const startedJobs = new Set<string>()

/**
 * 查找的序号。两次查找重叠时**只有最后一次的答案能落地**——先发的那次回来
 * 得晚的话，用户会看到自己刚刚已经改掉的那个名字的结果。比较查询串是不够的：
 * 同一个名字连点两次，两次的串一模一样。
 */
let lookupSeq = 0

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
  /** 正在跑的作业进度；null = 没有 */
  progress: PackageProgress | null
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

export const usePackageStore = create<PackageState>((set, get) => ({
  data: null,
  loading: false,
  loadError: '',
  progress: null,
  busy: false,
  errorCode: '',
  errorText: '',
  lookup: IDLE_LOOKUP,

  load: async () => {
    set({ loading: true })
    try {
      set({ data: await fetchManagedPackages(), loading: false, loadError: '' })
    } catch (e) {
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

  run: async (jobId) => {
    if (get().busy) return false
    startedJobs.add(jobId)
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来，那一下空窗期里
      // 按钮已经禁用了，界面却还什么都没说
      set({ progress: { job_id: jobId, state: 'preparing', log: '', error: null, code: '' } })
      const res = await runPackageJob(jobId)
      set({ busy: false, progress: { ...get().progress, ...res } as PackageProgress })
      return true
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, progress: null, errorCode: code, errorText: text })
      return false
    }
  },

  cancel: async () => {
    const id = get().progress?.job_id
    if (!id) return
    try {
      await cancelPackageJob(id)
    } catch {
      // 取消与「做完了」天然赛跑，输了不是错误
    }
  },

  poll: async () => {
    const id = get().progress?.job_id
    if (!id || !isPackageJobRunning(get().progress)) return
    try {
      const p = await fetchPackageJob(id)
      if (p.job_id === id && p.state !== 'idle') get().onProgress(p)
    } catch {
      // 下一轮再问
    }
  },

  onProgress: (p) => {
    // 只认**自己起过**的作业：SSE 是全进程共享的一条流，同一后端下另一个项目的标签页
    // 也会收到这条进度。以前只挡「与当前作业不同」，空闲标签页（progress 为 null）
    // 会把别人的作业认领成自己的——终态时刷错项目的环境、进行中时露出别人的取消按钮
    // （评审 #228）。后端 cancel / job 也按项目核，这一层是前端自己的那一份。
    if (!p.job_id || !startedJobs.has(p.job_id)) return
    set({ progress: p })
    if (p.state === 'done' || p.state === 'failed' || p.state === 'cancelled') {
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
    set({ lookup: { query, status: 'loading', result: null, code: '', text: '' } })
    try {
      const result = await lookupPackage(query)
      if (seq !== lookupSeq) return // 有更新的一次查找在飞，这次的答案已经过期
      set({ lookup: { query, status: 'found', result, code: '', text: '' } })
    } catch (e) {
      if (seq !== lookupSeq) return
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
}))
