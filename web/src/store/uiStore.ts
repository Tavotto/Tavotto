import { create } from 'zustand'

export type LeftTab = 'canvases' | 'assets' | 'layers' | 'elements'
/** 右栏三模式：属性 / 改图助手 / 画布设置 */
export type RightTab = 'properties' | 'assistant' | 'canvas'
export type Tool = 'select' | 'text' | 'arrow' | 'rect' | 'ellipse' | 'line'
/** 工作区断点：≥1440 可双栏钉住 / 1024–1439 左右互斥 / <1024 覆盖式抽屉 */
export type WorkspaceLayout = 'wide' | 'medium' | 'narrow'

/** 旧键 mm3.ui 由 storageMigration 一次性搬迁过来 */
const LS_KEY = 'magplot.ui'

export const LEFT_MIN = 280
export const LEFT_MAX = 360
export const RIGHT_MIN = 296
export const RIGHT_MAX = 320
/** 常驻图标轨道宽度 */
export const RAIL_W = 44

interface Persisted {
  leftOpen: boolean
  /** 左抽屉宽度（px），素材卡片列数靠它决定 */
  leftWidth: number
  /** 右栏宽度（px） */
  rightWidth: number
  /** 钉住：宽屏下选中对象时不自动收起该侧 */
  leftPinned: boolean
  rightPinned: boolean
  /** 网格间距（mm） */
  gridSize: number
  /** 吸附总开关与三类吸附目标 */
  snapEnabled: boolean
  snapToGrid: boolean
  snapToGuides: boolean
  snapToObjects: boolean
  /** 参考线锁定后不可拖动 */
  guidesLocked: boolean
  /** 显示页面安全区域（页边距）参考框 */
  showSafeArea: boolean
  rightOpen: boolean
  leftTab: LeftTab
  rightTab: RightTab
  showRulers: boolean
  showGrid: boolean
}

const DEFAULTS: Persisted = {
  // 初始只开素材抽屉；右栏等选中对象或用户主动打开
  leftOpen: true,
  leftWidth: 300,
  rightWidth: 304,
  leftPinned: false,
  rightPinned: false,
  gridSize: 10,
  snapEnabled: true,
  snapToGrid: false,
  snapToGuides: true,
  snapToObjects: true,
  guidesLocked: false,
  showSafeArea: false,
  rightOpen: false,
  leftTab: 'assets',
  rightTab: 'properties',
  showRulers: true,
  showGrid: true,
}

function readPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Persisted>) }
  } catch {
    /* 用默认值 */
  }
  return DEFAULTS
}

/** 一次等待用户点头的请求；resolve 由 ConfirmDialog 调用 */
export interface ConfirmRequest {
  title: string
  body: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  resolve: (ok: boolean) => void
}

interface UiState extends Persisted {
  status: string
  statusTone: 'info' | 'error'
  /** 正在双击编辑的文字对象 */
  editingTextId: string | null
  /** 进入裁剪模式的面板 */
  cropTargetId: string | null
  /** 进入图内元素编辑的面板（画布对象 id） */
  elementPanelId: string | null
  /** 图内选中的元素 gid（末位为主选；axes 可 shift 多选做对齐） */
  selectedGids: string[]
  /** 当前绘制工具，画完自动回到 select */
  tool: Tool
  exportOpen: boolean
  layoutOpen: boolean
  /** 布局版本时间线抽屉 */
  versionsOpen: boolean
  /** 论文样式弹窗 */
  stylesOpen: boolean
  /** 脚本注册表面板（stem↔脚本 映射：扫描 / 试运行 / 手工裁决） */
  registryOpen: boolean
  /** 快捷键帮助 */
  shortcutHelpOpen: boolean
  /** 设置面板 */
  settingsOpen: boolean
  /** 打开设置时直接跳到哪一节（如顶栏「有新版本」→ 检查更新）；null = 沿用上次 */
  settingsSection: string | null
  /** 打开「布局文件」弹窗时用户想做的是哪件事，决定焦点落在保存还是载入 */
  layoutIntent: 'save' | 'load'
  /** 全局确认框；由 askConfirm() 写入，ConfirmDialog 渲染 */
  confirm: ConfirmRequest | null
  /** 当前断点，由 useWorkspaceLayout 上报；侧栏互斥规则依赖它 */
  layout: WorkspaceLayout

  toggleLeft: () => void
  toggleRight: () => void
  setLeftTab: (tab: LeftTab) => void
  setRightTab: (tab: RightTab) => void
  setLeftWidth: (px: number) => void
  setRightWidth: (px: number) => void
  setLeftPinned: (v: boolean) => void
  setRightPinned: (v: boolean) => void
  /** 点图标轨道：同一项再点一次关抽屉，否则换页并打开 */
  railClick: (tab: LeftTab) => void
  /** 选中对象时的自动路由：素材抽屉让位、右栏进属性；停在助手时不抢 */
  autoShowProperties: () => void
  /** 选择清空：未钉住的属性栏不再占位 */
  autoHideProperties: () => void
  setCanvasPref: (patch: Partial<Persisted>) => void
  setShowRulers: (v: boolean) => void
  setShowGrid: (v: boolean) => void
  setStatus: (msg: string, tone?: 'info' | 'error') => void
  setEditingText: (id: string | null) => void
  setCropTarget: (id: string | null) => void
  setElementPanel: (id: string | null) => void
  setSelectedGid: (gid: string | null) => void
  toggleSelectedGid: (gid: string) => void
  setTool: (tool: Tool) => void
  setExportOpen: (v: boolean) => void
  setLayoutOpen: (v: boolean, intent?: 'save' | 'load') => void
  setVersionsOpen: (v: boolean) => void
  setStylesOpen: (v: boolean) => void
  setRegistryOpen: (v: boolean) => void
  setShortcutHelpOpen: (v: boolean) => void
  setSettingsOpen: (v: boolean, section?: string) => void
  setConfirm: (req: ConfirmRequest | null) => void
  setLayout: (layout: WorkspaceLayout) => void
}

function persist(state: UiState) {
  const keys: (keyof Persisted)[] = [
    'leftOpen', 'rightOpen', 'leftTab', 'rightTab', 'showRulers', 'showGrid',
    'leftWidth', 'rightWidth', 'leftPinned', 'rightPinned', 'gridSize',
    'snapEnabled', 'snapToGrid', 'snapToGuides', 'snapToObjects',
    'guidesLocked', 'showSafeArea',
  ]
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify(Object.fromEntries(keys.map((k) => [k, state[k]]))),
    )
  } catch {
    /* 忽略存储失败 */
  }
}

let statusTimer: number | undefined

/** 非宽屏两侧互斥：打开一侧就得收起另一侧，避免把画布挤没 */
const exclusive = (s: UiState) => s.layout !== 'wide'

export const useUiStore = create<UiState>((set, get) => ({
  ...readPersisted(),
  status: '',
  statusTone: 'info',
  editingTextId: null,
  cropTargetId: null,
  elementPanelId: null,
  selectedGids: [],
  tool: 'select',
  exportOpen: false,
  layoutOpen: false,
  versionsOpen: false,
  stylesOpen: false,
  registryOpen: false,
  shortcutHelpOpen: false,
  settingsOpen: false,
  settingsSection: null,
  layoutIntent: 'save',
  confirm: null,
  layout: 'wide',

  toggleLeft: () => {
    set((s) => {
      const leftOpen = !s.leftOpen
      return leftOpen && exclusive(s) ? { leftOpen, rightOpen: false } : { leftOpen }
    })
    persist(get())
  },
  toggleRight: () => {
    set((s) => {
      const rightOpen = !s.rightOpen
      return rightOpen && exclusive(s) ? { rightOpen, leftOpen: false } : { rightOpen }
    })
    persist(get())
  },
  railClick: (tab) => {
    set((s) => {
      if (s.leftOpen && s.leftTab === tab) return { leftOpen: false }
      return exclusive(s)
        ? { leftTab: tab, leftOpen: true, rightOpen: false }
        : { leftTab: tab, leftOpen: true }
    })
    persist(get())
  },
  setLeftTab: (leftTab) => {
    set((s) =>
      exclusive(s)
        ? { leftTab, leftOpen: true, rightOpen: false }
        : { leftTab, leftOpen: true },
    )
    persist(get())
  },
  setRightTab: (rightTab) => {
    set((s) =>
      exclusive(s)
        ? { rightTab, rightOpen: true, leftOpen: false }
        : { rightTab, rightOpen: true },
    )
    persist(get())
  },
  autoShowProperties: () => {
    set((s) => {
      // 停留在助手：只换作用目标，不抢走当前模式
      const rightTab = s.rightOpen && s.rightTab === 'assistant' ? 'assistant' : 'properties'
      const patch: Partial<UiState> = { rightOpen: true, rightTab }
      // 素材抽屉是「进去挑一次」的模式；未钉住就让位给属性
      if (s.leftOpen && s.leftTab === 'assets' && !(s.leftPinned && s.layout === 'wide')) {
        patch.leftOpen = false
      } else if (exclusive(s)) {
        patch.leftOpen = false
      }
      return patch
    })
    persist(get())
  },
  autoHideProperties: () => {
    set((s) => {
      if (!s.rightOpen || s.rightTab !== 'properties') return s
      // 常驻在 wide 与 medium 都生效（medium 靠互斥保证画布空间）；
      // narrow 是覆盖层，物理上无法常驻
      if (s.rightPinned && s.layout !== 'narrow') return s
      return { rightOpen: false }
    })
    persist(get())
  },
  setLeftWidth: (px) => {
    set({ leftWidth: Math.min(LEFT_MAX, Math.max(LEFT_MIN, Math.round(px))) })
    persist(get())
  },
  setRightWidth: (px) => {
    set({ rightWidth: Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, Math.round(px))) })
    persist(get())
  },
  setLeftPinned: (leftPinned) => {
    set({ leftPinned })
    persist(get())
  },
  setRightPinned: (rightPinned) => {
    set({ rightPinned })
    persist(get())
  },
  setCanvasPref: (patch) => {
    set(patch as Partial<UiState>)
    persist(get())
  },
  setShowRulers: (showRulers) => {
    set({ showRulers })
    persist(get())
  },
  setShowGrid: (showGrid) => {
    set({ showGrid })
    persist(get())
  },

  setStatus: (status, statusTone = 'info') => {
    set({ status, statusTone })
    window.clearTimeout(statusTimer)
    // 普通状态短暂即逝；错误保留到用户处理（toast 上有关闭键）
    if (status && statusTone !== 'error') {
      statusTimer = window.setTimeout(() => set({ status: '', statusTone: 'info' }), 4500)
    }
  },

  setEditingText: (editingTextId) => set({ editingTextId }),
  setCropTarget: (cropTargetId) => set({ cropTargetId }),
  setElementPanel: (elementPanelId) =>
    set({ elementPanelId, selectedGids: [], cropTargetId: null }),
  setSelectedGid: (gid) => set({ selectedGids: gid ? [gid] : [] }),
  toggleSelectedGid: (gid) =>
    set((s) => ({
      selectedGids: s.selectedGids.includes(gid)
        ? s.selectedGids.filter((g) => g !== gid)
        : [...s.selectedGids, gid],
    })),
  setTool: (tool) => set({ tool }),
  setExportOpen: (exportOpen) => set({ exportOpen }),
  setLayoutOpen: (layoutOpen, intent) =>
    set(intent ? { layoutOpen, layoutIntent: intent } : { layoutOpen }),
  setVersionsOpen: (versionsOpen) => set({ versionsOpen }),
  setStylesOpen: (stylesOpen) => set({ stylesOpen }),
  setRegistryOpen: (registryOpen) => set({ registryOpen }),
  setShortcutHelpOpen: (shortcutHelpOpen) => set({ shortcutHelpOpen }),
  setSettingsOpen: (settingsOpen, settingsSection = undefined) =>
    set({ settingsOpen, ...(settingsSection ? { settingsSection } : {}) }),
  setConfirm: (confirm) => set({ confirm }),
  setLayout: (layout) =>
    set((s) => {
      if (s.layout === layout) return s
      // 缩进互斥断点时两侧都开着就收左侧：检查器是编辑过程持续要看的
      const squeeze = layout !== 'wide' && s.leftOpen && s.rightOpen
      return squeeze ? { layout, leftOpen: false } : { layout }
    }),
}))

/** 弹出全局确认框，等用户回答；替代 window.confirm。 */
export function askConfirm(
  req: Omit<ConfirmRequest, 'resolve'>,
): Promise<boolean> {
  return new Promise((resolve) => {
    useUiStore.getState().setConfirm({ ...req, resolve })
  })
}
