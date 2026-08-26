import { create } from 'zustand'
import { fetchRegistry, type RegistryView } from '@/lib/api'

/**
 * 素材库「脚本」区的数据源：`/api/registry` 的完整视图（注册表 + 静态扫描
 * 报告 + 全部脚本清单）。RegistryDialog 也读同一个端点但自己管状态——它是
 * 高级诊断入口，打开频率低、每次都要最新现状；这里是常驻面板，值得缓存 +
 * 幂等去重。刷新时机：素材面板挂载、registry.changed SSE、probe 成功。
 */
interface ScriptLibraryStore {
  view: RegistryView | null
  loading: boolean
  loaded: boolean
  error: string | null
  load: () => Promise<void>
  clear: () => void
}

let inflight: Promise<void> | null = null
/** 项目代际：`clear()`（切项目）加一——A 项目的 /api/registry 响应在途时
 *  切到 B，落地必须作废（与 runtimeAssetStore / scriptRunStore 同一条纪律）。 */
let epoch = 0

export const useScriptLibraryStore = create<ScriptLibraryStore>((set) => ({
  view: null,
  loading: false,
  loaded: false,
  error: null,

  load: () => {
    if (inflight) return inflight
    const started = epoch
    set({ loading: true })
    inflight = fetchRegistry()
      .then((view) => {
        if (epoch !== started) return // 切过项目：旧清单作废
        set({ view, error: null, loaded: true })
      })
      .catch((e) => {
        if (epoch !== started) return
        set({ error: e instanceof Error ? e.message : String(e) })
      })
      .finally(() => {
        if (epoch !== started) return // 新项目自己的 inflight 不归旧响应管
        inflight = null
        set({ loading: false })
      })
    return inflight
  },

  clear: () => {
    epoch += 1
    inflight = null
    set({ view: null, loading: false, loaded: false, error: null })
  },
}))
