/**
 * 素材库的**浏览状态**：搜索词与筛选条件。
 *
 * 它住在组件外面，是因为 `AssetBrowser` 会被卸载——切到左轨的别的页、
 * 收起抽屉再展开、窄断点让位，每一次都是一次 unmount。放在组件 state 里的
 * 输入就跟着没了：用户刚打了半个文件名、切去看一眼问题面板、回来清单又是
 * 全量的（UI 审计 T06）。
 *
 * **只在内存里，不落 localStorage**：筛选是这一次会话里的临时视角，重启之后
 * 还带着"只看已使用"的话，用户看到的是一份莫名其妙变短的清单，而且没有任何
 * 东西告诉他为什么。换项目 `clear()`——那些筛选说的是上一个项目的目录与素材。
 */
import { create } from 'zustand'

export type AssetTypeFilter = 'all' | 'pdf' | 'raster' | 'script' | 'runtime'
export type AssetSortKey = 'name' | 'recent' | 'used'

export interface AssetFilters {
  source: string
  type: AssetTypeFilter
  sort: AssetSortKey
  usedOnly: boolean
}

export const DEFAULT_ASSET_FILTERS: AssetFilters = {
  source: 'all',
  type: 'all',
  sort: 'name',
  usedOnly: false,
}

interface AssetBrowseState {
  query: string
  filters: AssetFilters
  setQuery: (query: string) => void
  setFilters: (filters: AssetFilters | ((prev: AssetFilters) => AssetFilters)) => void
  /** 换项目：搜索词与筛选都属于旧项目 */
  clear: () => void
}

export const useAssetBrowseStore = create<AssetBrowseState>((set) => ({
  query: '',
  filters: DEFAULT_ASSET_FILTERS,
  setQuery: (query) => set({ query }),
  setFilters: (filters) =>
    set((s) => ({ filters: typeof filters === 'function' ? filters(s.filters) : filters })),
  clear: () => set({ query: '', filters: DEFAULT_ASSET_FILTERS }),
}))
