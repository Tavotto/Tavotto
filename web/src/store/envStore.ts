import { create } from 'zustand'
import { t } from '@/i18n'
import {
  fetchEngineEnvironment,
  installEngineEnvironment,
  setEngineEnvironment,
  setProjectEnvironment,
  setProjectWorkdir,
  type EngineEnvironment,
  type WorkdirConfirmation,
  type WorkdirMode,
} from '@/lib/api'
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
   * 换项目：`env.project`（项目环境 / 工作目录模式）属于旧项目，立刻清掉再按
   * 新项目重取。不清的话在请求回来之前，开关与错误块的建议说的都是上一个
   * 项目的模式（Codex 评审 P1）。
   */
  resetProject: () => void
  /** SSE 推进度时调用 */
  onProgress: (p: { state: string; log: string; error: string | null }) => void
}

export const useEnvStore = create<EnvState>((set, get) => ({
  env: null,
  log: '',
  installing: false,
  workdirConfirmation: null,

  requestWorkdirConfirmation: (payload, projectId) => {
    if (projectId !== undefined && projectId !== currentProjectId()) return
    if (get().workdirConfirmation) return
    set({ workdirConfirmation: payload })
  },
  dismissWorkdirConfirmation: () => set({ workdirConfirmation: null }),

  refresh: async () => {
    try {
      set({ env: await fetchEngineEnvironment() })
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
    try {
      set({ env: await setEngineEnvironment(path) })
      return null
    } catch (e) {
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  setProjectPython: async (path, module) => {
    try {
      const res = await setProjectEnvironment(path, module)
      // 项目那半边变了，全局状态里的 project 换成后端刚算出来的那份
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      return null
    } catch (e) {
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
    try {
      const res = await setProjectWorkdir(mode)
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      set({ workdirConfirmation: null })
      // 后端已经关掉了这个项目的会话；把因「没出图」/「要先选目录」失败的面板重新排上
      const { useRenderStore } = await import('@/store/renderStore')
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
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  resetProject: () => {
    const env = get().env
    // 首开确认框属于旧项目：A 项目问的问题不能由 B 项目回答
    if (env) set({ env: { ...env, project: { open: false } }, workdirConfirmation: null })
    else set({ workdirConfirmation: null })
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
