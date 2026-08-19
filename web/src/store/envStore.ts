import { create } from 'zustand'
import { t } from '@/i18n'
import {
  fetchEngineEnvironment,
  installEngineEnvironment,
  setEngineEnvironment,
  type EngineEnvironment,
} from '@/lib/api'

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
