import { create } from 'zustand'
import type { UiMessage } from '@/i18n'
import type { Severity } from '@/lib/profile'

export type LeftTab = 'canvases' | 'assets' | 'layers' | 'elements' | 'problems'
/** 右栏三模式：属性 / 改图助手 / 画布设置 */
export type RightTab = 'properties' | 'assistant' | 'canvas'
export type Tool = 'select' | 'text' | 'arrow' | 'rect' | 'ellipse' | 'line'
/** 工作区断点：≥1440 可双栏钉住 / 1024–1439 左右互斥 / <1024 覆盖式抽屉 */
export type WorkspaceLayout = 'wide' | 'medium' | 'narrow'

const LS_KEY = 'tavotto.ui'

export const LEFT_MIN = 280
export const LEFT_MAX = 360
/**
 * 右栏 320–480px（默认 360）。296–320 的旧宽度装不下「可见标签 + 控件」的
 * 检查器排版——字体/字号只能挤成无标签的一行（见 docs/ux/INSPECTOR_REDESIGN.md
 * 的 P2/P3），所以下限抬到 320、上限放到 480，默认 360。
 */
export const RIGHT_MIN = 320
export const RIGHT_MAX = 480
export const RIGHT_DEFAULT = 360
/** 常驻图标轨道宽度 */
export const RAIL_W = 44

/**
 * 工作区断点。画布是主角，窄下来时先让侧栏让路，而不是压缩画布：
 * - ≥1280 左右可同时钉住（1366×768 是最常见的笔记本档位，左树 + 画布 +
 *   属性栏必须共存——否则「左边找对象、右边改属性」变成来回开关侧栏）；
 * - 1024–1279 左右互斥，同时只留一侧：两栏 + 轨道至少 644px，双停靠会把
 *   画布压破 600px；
 * - <1024 侧栏改成盖在画布上的抽屉，画布宽度完全不受影响。
 *
 * 放在 store 里而不是 useWorkspaceLayout：初始 persisted 状态就要按当前窗口
 * 宽度裁一次（见 readPersisted），hook 反过来 import store，搁那边会成环。
 */
export const WIDE = 1280
export const MEDIUM = 1024

export const layoutFor = (w: number): WorkspaceLayout =>
  w >= WIDE ? 'wide' : w >= MEDIUM ? 'medium' : 'narrow'

interface Persisted {
  /**
   * 偏好版本号。默认值改动过一次（右栏由「等选中再弹」改成常驻），老用户
   * 本机存的是旧默认，直接改 DEFAULTS 对他们等于没改——按版本号补一次。
   */
  prefsVersion: number
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
  /**
   * 拖动子图时带上随行元素：被手动摆过位置的标题 / 轴标签 / 图例，
   * 以及色条轴、twinx 的孪生轴。关掉就只动子图本身。
   */
  dragAxesWithCompanions: boolean
  rightOpen: boolean
  leftTab: LeftTab
  rightTab: RightTab
  showRulers: boolean
  showGrid: boolean
}

export const PREFS_VERSION = 2

const DEFAULTS: Persisted = {
  prefsVersion: PREFS_VERSION,
  // 素材抽屉 + 右栏都开着：属性栏是编辑过程持续要看的，让它常驻
  leftOpen: true,
  leftWidth: 300,
  rightWidth: RIGHT_DEFAULT,
  leftPinned: false,
  rightPinned: true,
  gridSize: 10,
  snapEnabled: true,
  snapToGrid: false,
  snapToGuides: true,
  snapToObjects: true,
  guidesLocked: false,
  showSafeArea: false,
  dragAxesWithCompanions: true,
  rightOpen: true,
  leftTab: 'assets',
  rightTab: 'properties',
  showRulers: true,
  showGrid: true,
}

/**
 * 用户对「左右两侧要不要常驻」的**偏好**，与此刻实际开着没开分开记。
 *
 * 两者会分开正是因为界面必须响应窗口：互斥断点上打开一侧要收起另一侧、
 * 窄屏开机不铺覆盖层。那些都是**这一刻的排布**，不是用户说过的话。
 *
 * 不分开的后果是真的：把窗口拖窄一次（左抽屉自动让位），此后**任何一次**
 * persist（改个网格、拖个宽度）都会把 `leftOpen: false` 当成偏好写进本机，
 * 于是回到大屏、重启之后，常驻的左栏再也回不来了——而用户从没关过它。
 *
 * 写进偏好的只有用户自己的动作与产品规则（素材抽屉挑完让位给属性栏）；
 * 响应式让位一律不写。
 */
let prefOpen = { left: DEFAULTS.leftOpen, right: DEFAULTS.rightOpen }

function readPersisted(): Persisted {
  let saved: Partial<Persisted> | null = null
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) saved = JSON.parse(raw) as Partial<Persisted>
  } catch {
    /* 用默认值 */
  }
  let state: Persisted = saved ? { ...DEFAULTS, ...saved } : DEFAULTS
  // v0 → v1：右栏改成默认常驻。老用户本机躺着 rightPinned:false，不补一次
  // 的话「默认常驻」对他们永远不生效；补完盖上版本号，之后用户自己关了就
  // 一直是关的。
  // 判据取 saved 而不是合并后的 state：DEFAULTS 里的 prefsVersion 会把
  // 「老 blob 没有这个键」这件事补没了，那样迁移永远不触发。
  if (saved && (saved.prefsVersion ?? 0) < PREFS_VERSION) {
    const from = saved.prefsVersion ?? 0
    // v0 → v1 那一档只对真正的 v0 用户跑：v1 用户自己关掉的右栏是主动偏好，
    // v2 迁移不得再把它掰回来。
    if (from < 1) state = { ...state, rightOpen: true, rightPinned: true }
    // v1 → v2：右栏可用范围从 296–320 放宽到 320–480，默认 360。旧 blob 里
    // 的宽度必然 ≤320（老上限），全部迁到新默认——那是旧约束的产物，不是
    // 用户的主动偏好；迁完盖版本号，用户此后拖出的宽度原样保留。
    if (from < 2 && (state.rightWidth ?? 0) <= 320) {
      state = { ...state, rightWidth: RIGHT_DEFAULT }
    }
    state = { ...state, prefsVersion: PREFS_VERSION }
  }
  // 版本号相同也要收进合法区间：手工改过 localStorage / 未来回滚都不该让
  // 界面拿到一个画不出来的宽度
  state = {
    ...state,
    rightWidth: Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, state.rightWidth)),
    leftWidth: Math.min(LEFT_MAX, Math.max(LEFT_MIN, state.leftWidth)),
  }
  // 偏好在**响应式裁剪之前**定格：下面那一刀是「这台机器此刻的窗口太窄」，
  // 不是用户的意思。
  prefOpen = { left: state.leftOpen, right: state.rightOpen }
  // 窄屏下右栏是盖在画布上的覆盖层，开机就铺满等于把画布藏了；常驻标记留着，
  // 拉宽窗口自然生效。
  if (typeof window !== 'undefined' && layoutFor(window.innerWidth) === 'narrow') {
    state = { ...state, rightOpen: false }
  }
  return state
}

/**
 * 一次等待用户点头的请求；resolve 由 ConfirmDialog 调用。
 *
 * 文案全部是**描述符**而不是翻译好的字符串：确认框可能挂着等用户很久，
 * 中途切了语言得跟着换。用户自己的内容（文件名、画布名）走 values 插值。
 */
export interface ConfirmRequest {
  title: UiMessage
  body: UiMessage
  confirmLabel?: UiMessage
  cancelLabel?: UiMessage
  danger?: boolean
  resolve: (ok: boolean) => void
}

interface UiState extends Persisted {
  /** 当前 toast 的描述符；null = 没有 toast。切语言时 toast 跟着换 */
  status: UiMessage | null
  statusTone: 'info' | 'error'
  /** 正在双击编辑的文字对象 */
  editingTextId: string | null
  /** 进入裁剪模式的面板 */
  cropTargetId: string | null
  /** 进入图内元素编辑的面板（画布对象 id） */
  elementPanelId: string | null
  /** 图内选中的元素 gid（末位为主选；axes 可 shift 多选做对齐） */
  selectedGids: string[]
  /**
   * 定位之后那一下**短暂高亮**（`lib/issueFocus.ts` 写，`OverlaySvg` 画）。
   *
   * 与选中态分开：选中是用户的状态，高亮只是"我把你带到这儿了"的一次提示，
   * 到时自己消失。`token` 让连着定位同一个对象两次也能重新播一遍。
   * 高亮同时用**加粗虚线轮廓**表达，不只靠颜色（`reduced motion` 下不闪，
   * 静静地显示同样长的时间）。
   */
  issueHighlight: { objectId: string | null; gid: string | null; token: number } | null
  /**
   * 问题面板的等级筛选（null = 不筛）。**UI 会话状态**：不进文档、不进
   * 撤销、不跨会话记——它是"我现在想看哪几类"，不是用户的长期偏好。
   */
  problemFilter: Severity[] | null
  /** 当前绘制工具，画完自动回到 select */
  tool: Tool
  exportOpen: boolean
  layoutOpen: boolean
  /** 布局版本时间线抽屉 */
  versionsOpen: boolean
  /** 论文样式弹窗 */
  stylesOpen: boolean
  /**
   * 「项目接入状态」对话框（Prompt 08 的 readiness center）。
   *
   * 名字留着没改：它是这个对话框**唯一**的开关，项目菜单、设置页、素材说明条
   * 与画布上的「为什么不能编辑？」都在用它。再造一个同义标志等于给同一件事两个
   * 出处；聚焦哪一张图由 `projectReadinessStore.focusId` 管，那是另一件事。
   */
  registryOpen: boolean
  /** 快捷键帮助 */
  shortcutHelpOpen: boolean
  /** 设置面板 */
  settingsOpen: boolean
  /** 打开设置时直接跳到哪一节（如顶栏「有新版本」→ 检查更新）；null = 沿用上次 */
  settingsSection: string | null
  /**
   * 从哪个面板深链进设置的（导出面板的「编辑规范」）。关掉设置时回到那里，
   * 而不是把用户扔回画布。只认闭集里的值；null = 不回。
   */
  settingsReturnTo: 'export' | null
  /** 打开「画布文件」弹窗时用户想做的是哪件事，决定焦点落在保存还是载入 */
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
  setStatus: (msg: UiMessage | null, tone?: 'info' | 'error') => void
  setEditingText: (id: string | null) => void
  setIssueHighlight: (v: { objectId: string | null; gid: string | null } | null) => void
  setProblemFilter: (v: Severity[] | null) => void
  setCropTarget: (id: string | null) => void
  setElementPanel: (id: string | null) => void
  setSelectedGid: (gid: string | null) => void
  /** 整组替换（图内元素框选用）；顺序即选择顺序，末位是主选 */
  setSelectedGids: (gids: string[]) => void
  toggleSelectedGid: (gid: string) => void
  setTool: (tool: Tool) => void
  setExportOpen: (v: boolean) => void
  setLayoutOpen: (v: boolean, intent?: 'save' | 'load') => void
  setVersionsOpen: (v: boolean) => void
  setStylesOpen: (v: boolean) => void
  setRegistryOpen: (v: boolean) => void
  setShortcutHelpOpen: (v: boolean) => void
  setSettingsOpen: (v: boolean, section?: string, opts?: { returnTo?: 'export' | null }) => void
  setConfirm: (req: ConfirmRequest | null) => void
  setLayout: (layout: WorkspaceLayout) => void
}

function persist(state: UiState) {
  // 两侧开合写**偏好**那一份，其余照抄当前状态（宽度、吸附、网格……那些没有
  // 响应式覆盖，当前值就是用户设的值）
  const snapshot = { ...state, leftOpen: prefOpen.left, rightOpen: prefOpen.right }
  const keys: (keyof Persisted)[] = [
    'prefsVersion',
    'leftOpen', 'rightOpen', 'leftTab', 'rightTab', 'showRulers', 'showGrid',
    'leftWidth', 'rightWidth', 'leftPinned', 'rightPinned', 'gridSize',
    'snapEnabled', 'snapToGrid', 'snapToGuides', 'snapToObjects',
    'guidesLocked', 'showSafeArea', 'dragAxesWithCompanions',
  ]
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify(Object.fromEntries(keys.map((k) => [k, snapshot[k]]))),
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
  status: null,
  statusTone: 'info',
  editingTextId: null,
  cropTargetId: null,
  elementPanelId: null,
  selectedGids: [],
  issueHighlight: null,
  problemFilter: null,
  tool: 'select',
  exportOpen: false,
  layoutOpen: false,
  versionsOpen: false,
  stylesOpen: false,
  registryOpen: false,
  shortcutHelpOpen: false,
  settingsOpen: false,
  settingsSection: null,
  settingsReturnTo: null,
  layoutIntent: 'save',
  confirm: null,
  layout: typeof window === 'undefined' ? 'wide' : layoutFor(window.innerWidth),

  // 下面五个都是**用户自己按出来的**：他动的那一侧写进偏好，被互斥顺手收起的
  // 另一侧不写（那是窗口宽度的结果，不是他说的话）。
  toggleLeft: () => {
    set((s) => {
      const leftOpen = !s.leftOpen
      return leftOpen && exclusive(s) ? { leftOpen, rightOpen: false } : { leftOpen }
    })
    prefOpen.left = get().leftOpen
    persist(get())
  },
  toggleRight: () => {
    set((s) => {
      const rightOpen = !s.rightOpen
      return rightOpen && exclusive(s) ? { rightOpen, leftOpen: false } : { rightOpen }
    })
    prefOpen.right = get().rightOpen
    persist(get())
  },
  railClick: (tab) => {
    set((s) => {
      if (s.leftOpen && s.leftTab === tab) return { leftOpen: false }
      return exclusive(s)
        ? { leftTab: tab, leftOpen: true, rightOpen: false }
        : { leftTab: tab, leftOpen: true }
    })
    prefOpen.left = get().leftOpen
    persist(get())
  },
  setLeftTab: (leftTab) => {
    set((s) =>
      exclusive(s)
        ? { leftTab, leftOpen: true, rightOpen: false }
        : { leftTab, leftOpen: true },
    )
    prefOpen.left = get().leftOpen
    persist(get())
  },
  setRightTab: (rightTab) => {
    set((s) =>
      exclusive(s)
        ? { rightTab, rightOpen: true, leftOpen: false }
        : { rightTab, rightOpen: true },
    )
    prefOpen.right = get().rightOpen
    persist(get())
  },
  autoShowProperties: () => {
    const s = get()
    // 选中对象时一律回到属性页：属性属于「当前选中的对象」，助手属于独立
    // 工作流——用户点了一个对象却对着助手页，是上下文错位（重构前的
    // 「停在助手时不抢」正是这么表现的）。助手会话状态在 aiStore 里，
    // 切走不丢，运行中在助手入口上有状态点。
    const patch: Partial<UiState> = { rightOpen: true, rightTab: 'properties' }
    // 素材抽屉是「进去挑一次」的模式；未钉住就让位给属性。判据只求值一次，
    // 下面写偏好时直接复用——写两遍的话两份迟早分叉，而分叉的表现是
    // 「关掉的抽屉过一会儿自己回来了」。
    const assetsYield =
      s.leftOpen && s.leftTab === 'assets' && !(s.leftPinned && s.layout === 'wide')
    if (assetsYield || exclusive(s)) patch.leftOpen = false
    set(patch)
    prefOpen.right = true
    // 素材抽屉让位是**产品规则**（宽屏一样生效），属于用户这次流程的一部分；
    // 互斥收起是**响应式**，不进偏好。
    if (assetsYield) prefOpen.left = false
    persist(get())
  },
  autoHideProperties: () => {
    const s = get()
    if (!s.rightOpen || s.rightTab !== 'properties') return
    // 常驻在 wide 与 medium 都生效（medium 靠互斥保证画布空间）；
    // narrow 是覆盖层，物理上无法常驻
    if (s.rightPinned && s.layout !== 'narrow') return
    set({ rightOpen: false })
    prefOpen.right = false
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
      statusTimer = window.setTimeout(() => set({ status: null, statusTone: 'info' }), 4500)
    }
  },

  setIssueHighlight: (v) =>
    set((s) => ({
      issueHighlight: v
        ? { ...v, token: (s.issueHighlight?.token ?? 0) + 1 }
        : null,
    })),
  setProblemFilter: (problemFilter) => set({ problemFilter }),
  setEditingText: (editingTextId) => set({ editingTextId }),
  setCropTarget: (cropTargetId) => set({ cropTargetId }),
  setElementPanel: (elementPanelId) =>
    set({ elementPanelId, selectedGids: [], cropTargetId: null }),
  setSelectedGid: (gid) => set({ selectedGids: gid ? [gid] : [] }),
  setSelectedGids: (gids) =>
    set((s) =>
      s.selectedGids.length === gids.length && s.selectedGids.every((g, i) => g === gids[i])
        ? s
        : { selectedGids: gids },
    ),
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
  setSettingsOpen: (settingsOpen, settingsSection = undefined, opts = undefined) =>
    set((s) => {
      if (settingsOpen) {
        return {
          settingsOpen,
          ...(settingsSection ? { settingsSection } : {}),
          settingsReturnTo: opts?.returnTo ?? null,
        }
      }
      // 关闭：深链进来的回到出发的那个面板（只有导出一个来源；其余为 null）
      const back = s.settingsReturnTo
      return {
        settingsOpen,
        settingsReturnTo: null,
        ...(back === 'export' ? { exportOpen: true } : {}),
      }
    }),
  setConfirm: (confirm) => set({ confirm }),
  // **不 persist、不动 prefOpen**：这里改的是「窗口现在多宽」，
  // 而窗口宽度不是用户对常驻侧栏的偏好。
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
