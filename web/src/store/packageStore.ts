import { create } from 'zustand'
import {
  cancelPackageJob,
  fetchManagedPackages,
  fetchPackageJob,
  planPackageJob,
  runPackageJob,
  type ManagedPackages,
  type PackageJob,
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
  load: () => Promise<void>
  /** 形成作业（不改任何东西）；失败时把 code 记在 store 里并回 null */
  plan: (op: PackageOp, spec: string) => Promise<PackageJob | null>
  run: (jobId: string) => Promise<boolean>
  cancel: () => Promise<void>
  poll: () => Promise<void>
  onProgress: (p: PackageProgress) => void
  clearError: () => void
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
    const current = get().progress
    // 只认自己那条作业：SSE 是全进程共享的一条流
    if (current && current.job_id !== p.job_id) return
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
}))
