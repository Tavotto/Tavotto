import { create } from 'zustand'
import { t } from '@/i18n'
import {
  fetchEngineEnvironment,
  installEngineEnvironment,
  setEngineEnvironment,
  setProjectEnvironment,
  setProjectWorkdir,
  type EngineEnvironment,
  type WorkdirMode,
} from '@/lib/api'
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
  setWorkdirMode: (mode: WorkdirMode) => Promise<string | null>
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

  setWorkdirMode: async (mode) => {
    const current = get().env?.project?.workdir?.mode ?? 'sandbox'
    if (mode === current) return null
    if (mode === 'project') {
      const ok = await askConfirm({
        title: msg('engine.workdirConfirmTitle', undefined, 'errors'),
        body: msg('engine.workdirConfirmBody', undefined, 'errors'),
        confirmLabel: msg('engine.workdirConfirmOk', undefined, 'errors'),
      })
      if (!ok) return null
    }
    try {
      const res = await setProjectWorkdir(mode)
      const env = get().env
      if (env) set({ env: { ...env, project: res.project } })
      else await get().refresh()
      // 后端已经关掉了这个项目的会话；把因「没出图」失败的面板重新排上
      const { useRenderStore } = await import('@/store/renderStore')
      useRenderStore.getState().retryEnvironmentFailures()
      useUiStore.getState().setStatus(
        msg(mode === 'project' ? 'engine.workdirNowProject' : 'engine.workdirNowSandbox', undefined, 'errors'),
      )
      return null
    } catch (e) {
      return e instanceof Error ? e.message : t('engine.setPythonFailed', { ns: 'errors' })
    }
  },

  resetProject: () => {
    const env = get().env
    if (env) set({ env: { ...env, project: { open: false } } })
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
