import { create } from 'zustand'
import { fetchAiCapabilities } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'

/**
 * 实验开关 `ai.taf.enabled` 在首页的读法（本机级，不属于项目：不随换项目清）。
 *
 * 后端只有 `GET /api/ai/capabilities` 带这个开关，而它第一次调用要探测本机的编码助手、可能很慢。
 * 所以首页**不等它**：先按「上次读到的」画（本机记一份，读写都不假设存储可用），再在后台问一次，
 * 回来不一样才换版——常见的「开关一直开 / 一直关」两种用户都不会闪；只有刚改了开关的那一次启动才换一下版。
 * 读不到（网络、老后端）一律当没变，不报错、不重试。
 */
const CACHE_KEY = 'tavotto.taf-enabled'

function readCache(): boolean {
  try {
    return window.localStorage.getItem(CACHE_KEY) === '1'
  } catch {
    return false
  }
}

function writeCache(on: boolean): void {
  try {
    window.localStorage.setItem(CACHE_KEY, on ? '1' : '0')
  } catch {
    /* 存不下不影响这次 */
  }
}

interface TafFlagStore {
  enabled: boolean
  /** 已经向后端问过一次（成功过） */
  settled: boolean
  refresh: () => Promise<void>
}

let inflight: Promise<void> | null = null

export const useTafFlagStore = create<TafFlagStore>((set, get) => ({
  enabled: readCache(),
  settled: false,
  refresh: () => {
    if (get().settled) return Promise.resolve()
    // 助手面板那边已经探测过就直接用它的结果，不再多问一次
    const known = useAiStore.getState().caps
    if (known) {
      const on = known.taf_enabled === true
      writeCache(on)
      set({ enabled: on, settled: true })
      return Promise.resolve()
    }
    if (inflight) return inflight
    inflight = fetchAiCapabilities(false)
      .then((caps) => {
        const on = caps?.taf_enabled === true
        writeCache(on)
        set({ enabled: on, settled: true })
      })
      .catch(() => {
        /* 读不到当没变 */
      })
      .finally(() => {
        inflight = null
      })
    return inflight
  },
}))
