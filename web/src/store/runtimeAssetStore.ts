import { create } from 'zustand'
import { fetchRuntimeStatus, type RuntimeStaleStatus } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { isRuntimePanel } from '@/types/document'

/**
 * Runtime 素材（ADR 0013）的 stale / cache 状态，按 fileId 分键。
 *
 * 生命周期与 lazy rehydrate 的约定：
 * - `ensure()` 只查询（后端端点是只读的），**绝不触发脚本执行**；同一
 *   fileId 只查一次，重跑成功 / 显式失效后才再查。
 * - 渲染成功（runtime 面板拿到权威 SVG）→ `markFresh()`：此刻的图就是
 *   当前脚本跑出来的，比任何缓存的判定都新。
 * - 一次显式重跑渲染失败 → `markRerunFailed()`（`rerun_failed` 的唯一
 *   producer——后端只产磁盘可判定的那几档）。
 * - 换项目 `clear()`。
 */
export interface RuntimeAssetState {
  status: RuntimeStaleStatus
  cached: boolean
  registered: boolean
  /** 已经向后端问过一次（避免每次渲染面板都发请求） */
  checked: boolean
}

interface RuntimeAssetStore {
  byId: Record<string, RuntimeAssetState>
  /** 查询一次该面板的状态（幂等；非 runtime 面板与在途请求直接跳过） */
  ensure: (panel: PanelObject) => void
  markFresh: (fileId: string) => void
  markRerunFailed: (fileId: string) => void
  /** 脚本/注册表变化：作废已缓存的判定，下次 ensure 重新查询 */
  invalidate: (fileIds: string[]) => void
  clear: () => void
}

const inflight = new Set<string>()

export const useRuntimeAssetStore = create<RuntimeAssetStore>((set, get) => ({
  byId: {},

  ensure: (panel) => {
    if (!isRuntimePanel(panel)) return
    const id = panel.fileId
    if (get().byId[id]?.checked || inflight.has(id)) return
    inflight.add(id)
    const source = panel.source
      ? { script: panel.source.script, stem: panel.source.stem }
      : undefined
    void fetchRuntimeStatus(id, source)
      .then((st) =>
        set((s) => ({
          byId: {
            ...s.byId,
            [id]: {
              status: st.status,
              cached: st.cached,
              registered: st.registered,
              checked: true,
            },
          },
        })),
      )
      .catch(() => {
        // 查询失败（未登记 404 / 网络）：按「需要重跑、没有缓存」处理——
        // 面板显示占位与提示，绝不猜成新鲜
        set((s) => ({
          byId: {
            ...s.byId,
            [id]: { status: 'needs_rerun', cached: false, registered: false, checked: true },
          },
        }))
      })
      .finally(() => inflight.delete(id))
  },

  markFresh: (fileId) =>
    set((s) => ({
      byId: {
        ...s.byId,
        [fileId]: {
          ...(s.byId[fileId] ?? { registered: true, checked: true }),
          status: 'fresh',
          cached: true,
          checked: true,
        },
      },
    })),

  markRerunFailed: (fileId) =>
    set((s) => {
      const prev = s.byId[fileId]
      if (!prev) return {}
      return { byId: { ...s.byId, [fileId]: { ...prev, status: 'rerun_failed' } } }
    }),

  invalidate: (fileIds) =>
    set((s) => {
      const byId = { ...s.byId }
      let changed = false
      for (const id of fileIds) {
        if (byId[id]) {
          byId[id] = { ...byId[id], checked: false }
          changed = true
        }
      }
      return changed ? { byId } : {}
    }),

  clear: () => set({ byId: {} }),
}))
