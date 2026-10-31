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
  /** 最近一次已落地视图的请求发出时刻（毫秒）；晚到不代表快照更新 */
  loadedAt: number
  error: string | null
  /** 项目代际：`clear()`（切项目）加一。依赖它的 effect 在换项目后重跑（`loaded` 在 A 失败、B 清空时都是 false，不会变） */
  generation: number
  load: () => Promise<void>
  clear: () => void
}

let inflight: { epoch: number; promise: Promise<void>; trailing: Promise<void> | null } | null = null
/** 项目代际：`clear()`（切项目）加一——A 项目的 /api/registry 响应在途时
 *  切到 B，落地必须作废（与 runtimeAssetStore / scriptRunStore 同一条纪律）。 */
let epoch = 0

export const useScriptLibraryStore = create<ScriptLibraryStore>((set, get) => ({
  view: null,
  loading: false,
  loaded: false,
  loadedAt: 0,
  error: null,
  generation: 0,

  load: () => {
    if (inflight) {
      const cur = inflight
      // 运行完成 / registry.changed 可能晚于服务端读取旧快照：这一批通知共用一次补问。
      cur.trailing ??= cur.promise.then(() => epoch === cur.epoch ? get().load() : undefined)
      return cur.trailing
    }
    const started = epoch
    const requestedAt = Date.now()
    set({ loading: true })
    const promise = fetchRegistry()
      .then((view) => {
        if (epoch !== started) return // 切过项目：旧清单作废
        set({ view, error: null, loaded: true, loadedAt: requestedAt })
      })
      .catch((e) => {
        if (epoch !== started) return
        set({ error: e instanceof Error ? e.message : String(e) })
      })
      .finally(() => {
        if (epoch !== started) return // 新项目自己的 inflight 不归旧响应管
        const trailing = inflight?.trailing
        inflight = null
        // 补问已排队：忙碌态一直交到它结束，不在两个请求之间闪一下。
        if (!trailing) set({ loading: false })
      })
    inflight = { epoch: started, promise, trailing: null }
    return promise
  },

  clear: () => {
    epoch += 1
    inflight = null
    set((s) => ({ view: null, loading: false, loaded: false, loadedAt: 0, error: null, generation: s.generation + 1 }))
  },
}))
