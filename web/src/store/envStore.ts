import { create } from 'zustand'
import { t } from '@/i18n'
import {
  backendErrorText,
  type DependencyPreparationOffer,
  fetchEngineEnvironment,
  installEngineEnvironment,
  setEngineEnvironment,
  setProjectEnvironment,
  setProjectWorkdir,
  type EngineEnvironment,
  type UserEnvironmentSource,
  type WorkdirConfirmation,
  type WorkdirMode,
} from '@/lib/api'
import { createDismissTimer } from '@/lib/dismissTimer'
import { currentProjectId } from '@/lib/session'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { msg } from '@/i18n'

/**
 * 渲染环境状态。⚡ 参数化编辑需要一个能 import 用户脚本依赖的 Python；
 * 找不到时不该把「找不到装有 matplotlib 的 Python」这句话甩给用户，
 * 而是给一个能点的出口：让 Tavotto 自己装一个，或指定已有的解释器。
 *
 * 安装进度由后端经 SSE `engine.bootstrap` 推过来（useServerEvents 转发到这里）。
 */
interface EnvState {
  env: EngineEnvironment | null
  /** 安装过程的滚动日志 */
  log: string
  installing: boolean
  refresh: () => Promise<void>
  install: () => Promise<void>
  setPython: (path: string | null) => Promise<string | null>
  /**
   * 只为**当前项目**指定解释器（ADR 0018）。传 `null` = 回到默认链条。
   * 与 `setPython` 的区别只有作用域，但那个区别很大：`setPython` 写的是
   * 全局设置，会连带改变别的项目的渲染环境。
   */
  setProjectPython: (path: string | null, module?: string) => Promise<string | null>
  /**
   * 切当前项目 safe worker 的工作目录模式（ADR 0047）。开到 `project` 要先
   * 确认一次——文案与机制逐条一致：相对路径读得到、相对路径写的文件落进项目、
   * 守卫与 savefig 捕获不变。用户取消回 `null` 且什么都不改；失败回错误文案。
   * 成功后把「脚本跑完没出图」那些面板重新排上。
   */
  setWorkdirMode: (mode: WorkdirMode, opts?: { confirmed?: boolean }) => Promise<string | null>
  /**
   * 首开的那一次确认（U03，ADR 0057）：后端在起第一个 worker 之前判出「数据只有项目根
   * 找得到」或「两处同名不同值」，渲染以 `workdir_confirmation_required` 回来——这不是
   * 错误块，是一次选择。渲染 store 把载荷交到这里，`WorkdirConfirmDialog` 渲染它；
   * 同一时刻只开一份（同一项目多张图同时撞上时后来的不覆盖先到的）。
   */
  workdirConfirmation: WorkdirConfirmation | null
  /**
   * `projectId` 是**发那次渲染时**的项目：渲染在途中用户切了项目，A 的失败回来时不许把 A 的
   * 问题摆到 B 上（一点「运行」就把真实目录的授权给错项目，Codex #456 P1）——对不上就丢。
   */
  requestWorkdirConfirmation: (payload: WorkdirConfirmation, projectId?: string | null) => void
  dismissWorkdirConfirmation: () => void
  /**
   * 跑前的那一次授权（U04，ADR 0061）：后端在起第一个 worker 之前判出「脚本开跑要的包目标环境
   * 里没有、能一次装全」，渲染以 `dependency_preparation_required` 回来——同样不是错误块，是
   * 一次授权。载荷放这里（与运行目录的确认同一个家），`DependencyPrepareDialog` 渲染它，执行
   * 归 `depRepairStore.prepare`。同一时刻只开一份；换了项目的旧载荷不弹。
   */
  dependencyPreparation: DependencyPreparationOffer | null
  requestDependencyPreparation: (offer: DependencyPreparationOffer, projectId?: string | null) => void
  dismissDependencyPreparation: () => void
  /**
   * 跑前的门刚刚**自动改用**了用户自己的环境（ADR 0079，SSE `engine.environment_adopted`）：
   * 通知轨上说一句「改用了哪个」并给「改回」。只是说出口，不是一次授权——改用已经发生了。
   */
  adoptedEnvironment: AdoptedEnvironment | null
  noteEnvironmentAdopted: (env: Omit<AdoptedEnvironment, 'token'>) => void
  dismissAdoptedEnvironment: () => void
  /**
   * 「改回」：本项目明确选回默认链条（后端 `remember_default`，之后不再自动挑），所有面板按原来的
   * 环境重渲——缺的包于是又会走依赖弹窗。回 null 或一句失败原文。
   */
  revertAdoptedEnvironment: () => Promise<string | null>
  /**
   * 换项目：`env.project`（项目环境 / 工作目录模式）属于旧项目，立刻清掉再按
   * 新项目重取。不清的话在请求回来之前，开关与错误块的建议说的都是上一个
   * 项目的模式（Codex 评审 P1）。
   */
  resetProject: () => void
  /** SSE 推进度时调用 */
  onProgress: (p: { state: string; log: string; error: string | null }) => void
}

/** 「已改用你的环境」那条提示多久自己走（指针 / 焦点停在上面不走表，见 `NotificationRail`） */
export const ADOPTED_NOTICE_MS = 12_000
export const adoptedDismissTimer = createDismissTimer()

export interface AdoptedEnvironment {
  source: UserEnvironmentSource
  label: string
  python_version: string
  /** 每次改用自增：同一条提示重复出现时 key 换掉，走表重新开始 */
  token: number
}

/**
 * 项目代际（issue #605 评审 / #606 第 1 条）：`resetProject()`（换项目）加一。`env.project`、工作目录模式、
 * 项目解释器说的都是**发请求那个项目**；A 的 GET / PATCH 在切到 B 之后才回来的话，写进来就是把 A 的项目
 * 环境摆在 B 上。所以每个 await 之后、写状态之前按代际判一次——判在写的这一侧，调用方（`depRepairStore`
 * 的采用、设置页）不必各自再挡一遍，也挡不住（写在它们看得见结果之前就发生了）。
 */
let projectEpoch = 0

export const useEnvStore = create<EnvState>((set, get) => ({
  env: null,
  adoptedEnvironment: null,
  log: '',
  installing: false,
  workdirConfirmation: null,
  dependencyPreparation: null,

  requestWorkdirConfirmation: (payload, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().workdirConfirmation) return
    set({ workdirConfirmation: payload })
  },
  dismissWorkdirConfirmation: () => set({ workdirConfirmation: null }),
  requestDependencyPreparation: (offer, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().dependencyPreparation) return
    set({ dependencyPreparation: offer })
  },
  dismissDependencyPreparation: () => set({ dependencyPreparation: null }),
  noteEnvironmentAdopted: (env) => {
    set((s) => ({ adoptedEnvironment: { ...env, token: (s.adoptedEnvironment?.token ?? 0) + 1 } }))
    adoptedDismissTimer.start(ADOPTED_NOTICE_MS, () => get().dismissAdoptedEnvironment())
  },
  dismissAdoptedEnvironment: () => {
    adoptedDismissTimer.cancel()
    set({ adoptedEnvironment: null })
  },
  revertAdoptedEnvironment: async () => {
    const epoch = projectEpoch
    const error = await get().setProjectPython(null)
    // 「改回」记在 A 上；切到 B 之后才回来的话，B 的提示与面板一个都不动
    if (epoch !== projectEpoch) return null
    if (error) return error
    get().dismissAdoptedEnvironment()
    // 后端已关掉本项目的会话；每个在用的面板都要按原来的环境重建（不只是失败的那些）
    const { useRenderStore } = await import('@/store/renderStore')
    if (epoch !== projectEpoch) return null
    const render = useRenderStore.getState()
    const ids = [...new Set(Object.values(render.byKey).map((v) => v.fileId))]
    if (ids.length) render.markStale(ids)
    return null
  },

  refresh: async () => {
    const epoch = projectEpoch
    try {
      const env = await fetchEngineEnvironment()
      if (epoch !== projectEpoch) return // 响应里的 project 是发请求那个项目的
      set({ env })
    } catch {
      // 探测失败不该打扰用户：真要渲染时自然会报错
    }
  },

  install: async () => {
    if (get().installing) return
    set({ installing: true, log: '' })
    try {
      await installEngineEnvironment()
    } catch (e) {
      set({
        installing: false,
        log: e instanceof Error ? e.message : t('engine.installFailed', { ns: 'errors' }),
      })
    }
  },

  setPython: async (path) => {
    const epoch = projectEpoch
    try {
      const env = await setEngineEnvironment(path)
      // 全局解释器已经改了（它不属于哪个项目），但响应里带着发请求那个项目的 `project`，切过就不写它。
      // 也不能就此丢掉（#606 第 4 条）：切项目时那次 refresh 可能比这次 PATCH 先回，B 显示的还是旧的全局
      // 解释器——这里按**此刻的**项目重新问一次（refresh 自己按代际判，属于 B）
      if (epoch !== projectEpoch) {
        await get().refresh()
        return null
      }
      set({ env })
      // PATCH 的响应现在与 GET 同形（带 `project`）；老服务端没带的话整体替换会把
      // 受管环境 / 工作目录那几行藏到下一次无关刷新——补一次 GET（Codex 评审 P2）
      if (!env.project) await get().refresh()
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      // 按 code 翻（`environment_mutating` = 安装进行中，暂时不能改），查不到才原文
      return e instanceof Error ? backendErrorText(e) : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  setProjectPython: async (path, module) => {
    const epoch = projectEpoch
    try {
      const res = await setProjectEnvironment(path, module)
      // A 的项目环境不写进 B 的 env（#605 评审 P1）；A 的错误也不在 B 上说
      if (epoch !== projectEpoch) return null
      // 项目那半边变了，全局状态里的 project 换成后端刚算出来的那份
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  setWorkdirMode: async (mode, opts) => {
    const current = get().env?.project?.workdir?.mode ?? 'sandbox'
    // 「决定过」与「模式相同」是两件事：首开确认框里选「继续沙盒」时模式没变，但要把
    // 这个决定记下来（否则下一张图又问一遍）——那时 `confirmed` 为真，照样发 PATCH
    if (mode === current && !opts?.confirmed) return null
    // 两个真实 cwd 模式都要点头一次（ADR 0047 / 0057）：文案与机制逐条一致。首开确认框
    // 自己就是那一次点头（`confirmed`），不再弹第二层
    if (mode !== 'sandbox' && !opts?.confirmed) {
      const ok = await askConfirm(
        mode === 'project_root'
          ? {
              title: msg('engine.workdirRootConfirmTitle', undefined, 'errors'),
              body: msg('engine.workdirRootConfirmBody', undefined, 'errors'),
              confirmLabel: msg('engine.workdirRootConfirmOk', undefined, 'errors'),
            }
          : {
              title: msg('engine.workdirConfirmTitle', undefined, 'errors'),
              body: msg('engine.workdirConfirmBody', undefined, 'errors'),
              confirmLabel: msg('engine.workdirConfirmOk', undefined, 'errors'),
            },
      )
      if (!ok) return null
    }
    const epoch = projectEpoch
    try {
      const res = await setProjectWorkdir(mode)
      // 模式记在 A 上；B 的环境行、确认框、渲染重排、状态栏一个都不动
      if (epoch !== projectEpoch) return null
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      if (epoch !== projectEpoch) return null
      set({ workdirConfirmation: null })
      // 后端已经关掉了这个项目的会话；把因「没出图」/「要先选目录」失败的面板重新排上
      const { useRenderStore } = await import('@/store/renderStore')
      if (epoch !== projectEpoch) return null
      useRenderStore.getState().retryEnvironmentFailures()
      const now =
        mode === 'project_root'
          ? 'engine.workdirNowProjectRoot'
          : mode === 'project'
            ? 'engine.workdirNowProject'
            : 'engine.workdirNowSandbox'
      useUiStore.getState().setStatus(msg(now, undefined, 'errors'))
      return null
    } catch (e) {
      if (epoch !== projectEpoch) return null
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  resetProject: () => {
    // 换代**排在清空与重取之前**：之前发出的请求作废，下面这次 refresh 属于新项目
    projectEpoch += 1
    const env = get().env
    // 首开确认框属于旧项目：A 项目问的问题不能由 B 项目回答
    if (env)
      set({
        env: { ...env, project: { open: false } },
        workdirConfirmation: null,
        dependencyPreparation: null,
        adoptedEnvironment: null,
      })
    else set({ workdirConfirmation: null, dependencyPreparation: null, adoptedEnvironment: null })
    void get().refresh()
  },

  onProgress: (p) => {
    set({ log: p.log })
    if (p.state === 'done' || p.state === 'failed') {
      set({ installing: false })
      void get().refresh()
    } else if (p.state === 'running') {
      set({ installing: true })
    }
  },
}))
