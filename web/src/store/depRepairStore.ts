import { create } from 'zustand'
import {
  cancelDependencyPlan,
  createDependencyPlan,
  installDependencyPlan,
  rebuildManagedEnvironment,
  type DependencyProgress,
  type DependencyRepairPlan,
} from '@/lib/api'
import { useEnvStore } from '@/store/envStore'

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
  makePlan: (
    args: { module: string; script: string; target: 'project_venv' | 'tavotto_managed'; distribution?: string },
  ) => Promise<void>
  install: () => Promise<void>
  cancel: () => Promise<void>
  rebuildManaged: () => Promise<void>
  onProgress: (p: DependencyProgress) => void
  /** 关掉确认卡片 / 换一个目标时回到干净状态 */
  reset: () => void
}

/** 后端错误 → (code, 原文)。没有 code 的一律归到通用安装失败。 */
const failure = (e: unknown): { code: string; text: string } => {
  const body = (e as { body?: { code?: string; error?: string } })?.body
  const text = e instanceof Error ? e.message : ''
  return { code: body?.code || '', text: body?.error || text }
}

export const useDepRepairStore = create<DepRepairState>((set, get) => ({
  plan: null,
  progress: null,
  busy: false,
  errorCode: '',
  errorText: '',

  makePlan: async (args) => {
    if (get().busy) return
    set({ busy: true, errorCode: '', errorText: '', plan: null })
    try {
      const { plan } = await createDependencyPlan(args)
      set({ plan, busy: false })
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
    }
  },

  install: async () => {
    const plan = get().plan
    if (!plan || get().busy) return
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      // 乐观地先进 preparing：SSE 的第一条要等后端线程起来，中间那一下
      // 空窗期里按钮已经禁用了，界面却还什么都没说。
      set({ progress: { plan_id: plan.plan_id, state: 'preparing', log: '', error: null, code: '' } })
      await installDependencyPlan(plan.plan_id)
      set({ busy: false })
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, progress: null, errorCode: code, errorText: text })
    }
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
    set({ busy: true, errorCode: '', errorText: '' })
    try {
      await rebuildManagedEnvironment()
      set({ busy: false, progress: { plan_id: 'managed-rebuild', state: 'creating_env', log: '', error: null, code: '' } })
    } catch (e) {
      const { code, text } = failure(e)
      set({ busy: false, errorCode: code, errorText: text })
    }
  },

  onProgress: (p) => {
    set({ progress: p })
    if (p.state === 'done' || p.state === 'failed' || p.state === 'cancelled') {
      // 环境那半边变了（换了解释器 / 建了受管环境），刷一次环境状态
      void useEnvStore.getState().refresh()
      if (p.state !== 'done') set({ errorCode: p.code || '', errorText: p.error || '' })
      // 计划是一次性的：成功也好失败也好，都不该留着一个已经被消费掉的
      // plan_id 让用户再点一次「安装」。
      set({ plan: null })
    }
  },

  reset: () => set({ plan: null, progress: null, busy: false, errorCode: '', errorText: '' }),
}))

/** 安装是不是正在进行（界面据此禁用按钮、显示进度而不是选项） */
export const isRepairRunning = (p: DependencyProgress | null): boolean =>
  !!p && ['preparing', 'creating_env', 'installing', 'verifying'].includes(p.state)
