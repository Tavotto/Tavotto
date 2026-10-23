import { create } from 'zustand'
import {
  fetchRuntimeAssets,
  fetchRuntimeStatus,
  type RuntimeAssetInfo,
  type RuntimeStaleStatus,
} from '@/lib/api'
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
  /** 后端注册表认不认这张图。`null` = 未知（失效后 / 还没问过）：**未知 ≠ 假**，不得据此判「源文件不可用」 */
  registered: boolean | null
  /** 上一次这张图是怎么产生的（`native` = 用户自己的 Python，ADR 0021 §9）。
   *  老后端不给这个字段 → `safe`：**未知不等于 native**，把未知当 native 会让
   *  一个普通面板挂上「会话已结束」的角标。 */
  profile: 'safe' | 'native'
  /** 已经向后端问过一次（避免每次渲染面板都发请求） */
  checked: boolean
}

interface RuntimeAssetStore {
  byId: Record<string, RuntimeAssetState>
  /* ------ 素材库「图」区的 RuntimeFigureAsset 清单（Session 5） ------ */
  /** null = 还没取过；取过之后是清单本体（可能为空数组） */
  assets: RuntimeAssetInfo[] | null
  assetsLoading: boolean
  assetsError: string | null
  /** cache 预览的换代计数：重跑刷新了 cache 后 +1，<img> 据此换 src */
  previewNonce: Record<string, number>
  /** 重取清单（只读端点，绝不触发脚本执行）；幂等去重 */
  loadAssets: () => Promise<void>
  bumpPreview: (ids: string[]) => void
  /** 查询一次该面板的状态（幂等；非 runtime 面板与在途请求直接跳过） */
  ensure: (panel: PanelObject) => void
  markFresh: (fileId: string) => void
  markRerunFailed: (fileId: string) => void
  /** 脚本/注册表变化：作废已缓存的判定，下次 ensure 重新查询 */
  invalidate: (fileIds: string[]) => void
  clear: () => void
}

const inflight = new Set<string>()
let assetsInflight: Promise<void> | null = null
/** 项目代际：`clear()`（切项目）加一。模块级的 in-flight 请求活得比一次
 *  Zustand reset 长——A 项目的响应落进 B 项目的清单就是靠这个挡的
 *  （与 scriptRunStore 的 epoch 同一条纪律）。 */
let epoch = 0
/** 逐 id 的代次：`invalidate()` / `markFresh()` 加一；在飞的 `ensure()` 响应回来发现代次变了就丢弃，旧答案不得覆盖新状态 */
const generation = new Map<string, number>()
const genOf = (id: string) => generation.get(id) ?? 0
const bump = (id: string) => {
  generation.set(id, genOf(id) + 1)
  inflight.delete(id) // 下一次 ensure() 要能重新发请求
}

export const useRuntimeAssetStore = create<RuntimeAssetStore>((set, get) => ({
  byId: {},
  assets: null,
  assetsLoading: false,
  assetsError: null,
  previewNonce: {},

  loadAssets: () => {
    if (assetsInflight) return assetsInflight
    const started = epoch
    set({ assetsLoading: true })
    assetsInflight = fetchRuntimeAssets()
      .then((r) => {
        if (epoch !== started) return // 切过项目：旧项目的清单作废
        set({ assets: r.assets, assetsError: null })
      })
      .catch((e) => {
        if (epoch !== started) return
        // 清单取不到（旧后端 404 / 网络）：给空清单 + 错误，不留 null 骨架
        set({ assets: [], assetsError: e instanceof Error ? e.message : String(e) })
      })
      .finally(() => {
        if (epoch !== started) return // 新项目自己的 inflight 不归旧响应管
        assetsInflight = null
        set({ assetsLoading: false })
      })
    return assetsInflight
  },

  bumpPreview: (ids) =>
    set((s) => {
      const previewNonce = { ...s.previewNonce }
      for (const id of ids) previewNonce[id] = (previewNonce[id] ?? 0) + 1
      return { previewNonce }
    }),

  ensure: (panel) => {
    if (!isRuntimePanel(panel)) return
    const id = panel.fileId
    if (get().byId[id]?.checked || inflight.has(id)) return
    inflight.add(id)
    const started = epoch
    const gen = genOf(id)
    const source = panel.source
      ? {
          script: panel.source.script,
          stem: panel.source.stem,
          ...(panel.source.runConfig ? { run_config: panel.source.runConfig } : {}),
        }
      : undefined
    void fetchRuntimeStatus(id, source)
      .then((st) => {
        if (epoch !== started || genOf(id) !== gen) return // 切过项目 / 期间失效或刷新过：旧答案作废
        set((s) => ({
          byId: {
            ...s.byId,
            [id]: {
              status: st.status,
              cached: st.cached,
              registered: st.registered,
              profile: st.execution_profile === 'native' ? 'native' : 'safe',
              checked: true,
            },
          },
        }))
      })
      .catch(() => {
        if (epoch !== started || genOf(id) !== gen) return
        // 查询失败（网络 / 5xx / 非 2xx）：按「需要重跑、没有缓存」处理——面板显示占位与提示，绝不猜成新鲜；
        // 但**注册状态是未知（null），不是「不认」**：只有后端成功响应且明确 registered:false 才是 false。
        // 把传输失败存成 false，单图导出会被静默降级到画布（低保真）——前端预测失败不得静默换口径。
        set((s) => ({
          byId: {
            ...s.byId,
            [id]: {
              status: 'needs_rerun',
              cached: false,
              registered: null,
              profile: 'safe',
              checked: true,
            },
          },
        }))
      })
      .finally(() => {
        if (genOf(id) === gen) inflight.delete(id) // 代次变了：marker 已被 bump 清掉，别误删新请求的
      })
  },

  markFresh: (fileId) => {
    bump(fileId)
    set((s) => ({
      byId: {
        ...s.byId,
        [fileId]: {
          ...(s.byId[fileId] ?? { checked: true, profile: 'safe' as const }),
          status: 'fresh',
          cached: true,
          // 刚渲染出权威图 = 脚本刚跑过、注册表认它：不保留可能是旧的 false
          registered: true,
          checked: true,
        },
      },
    }))
  },

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
        bump(id)
        if (byId[id]) {
          // 脚本 / 注册表变了：注册状态一并作废成「未知」，不留旧的 false（重跑注册后它就是错的）
          byId[id] = { ...byId[id], checked: false, registered: null }
          changed = true
        }
      }
      return changed ? { byId } : {}
    }),

  clear: () => {
    // 换代：在途请求（清单 / 逐面板 status）从此落不进新项目
    epoch += 1
    assetsInflight = null
    inflight.clear()
    generation.clear()
    set({ byId: {}, assets: null, assetsLoading: false, assetsError: null, previewNonce: {} })
  },
}))
