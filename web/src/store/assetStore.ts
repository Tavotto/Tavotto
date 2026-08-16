import { create } from 'zustand'
import { fetchPanels, type PanelInfo } from '@/lib/api'

const USED_KEY = 'magplot.assetUsed'

function readUsed(): Record<string, number> {
  try {
    const raw = localStorage.getItem(USED_KEY)
    const v = raw ? JSON.parse(raw) : null
    return v && typeof v === 'object' ? (v as Record<string, number>) : {}
  } catch {
    return {}
  }
}

interface AssetState {
  panels: PanelInfo[]
  byId: Record<string, PanelInfo>
  figuresDir: string
  loading: boolean
  /** 至少成功加载过一次；用来区分「首次加载」与「刷新」两种 loading */
  loaded: boolean
  error: string | null
  /** fileId → 最近一次加入画布的时间戳；「最近使用」排序用，与文件 mtime 是两回事 */
  recentlyUsed: Record<string, number>
  load: () => Promise<void>
  markUsed: (id: string) => void
}

export const useAssetStore = create<AssetState>((set) => ({
  panels: [],
  byId: {},
  figuresDir: '',
  loading: false,
  loaded: false,
  error: null,
  recentlyUsed: readUsed(),

  load: async () => {
    set({ loading: true, error: null })
    try {
      const data = await fetchPanels()
      set({
        panels: data.panels,
        byId: Object.fromEntries(data.panels.map((p) => [p.id, p])),
        figuresDir: data.figures_dir,
        loading: false,
        loaded: true,
      })
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : String(err) })
    }
  },

  markUsed: (id) =>
    set((s) => {
      const recentlyUsed = { ...s.recentlyUsed, [id]: Date.now() }
      try {
        localStorage.setItem(USED_KEY, JSON.stringify(recentlyUsed))
      } catch {
        /* 存不下就只在本次会话里有效 */
      }
      return { recentlyUsed }
    }),
}))

/** 文件夹名的中文显示，未知目录原样显示 */
export const FOLDER_LABEL: Record<string, string> = {
  '.': 'figures 根目录',
  main_text_panels: '正文面板',
  supplementary_panels: '补充材料',
  base: '素材',
}

export const folderLabel = (folder: string) => FOLDER_LABEL[folder] ?? folder
