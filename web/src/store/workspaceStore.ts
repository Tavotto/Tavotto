/**
 * 工作区模式的状态本体（`useWorkspaceStore`）与「排版视口的寄存处」——从 `store/workspace.ts` 拆出的叶子模块。
 *
 * 为什么单独一个文件：`store/actions` 要读「快速编辑正在编辑哪张图」（`fastEditPanelOf`：全选 / 删除 / 创建副本
 * 只认那张图，Codex #833），而 `store/workspace` 的工作流动作（`openFastEdit` / `addFigureToLayout`…）要调
 * `actions.addPanel` 等。两者互相 import 就是一个环（`importArchitecture.test.ts`）。这里**不许** import
 * `store/actions` 或 `store/workspace`；工作流动作、持久化与对外 API 仍在 `store/workspace.ts`（它原样再导出本文件的
 * 公开名字，既有调用方不用改）。模式规则全文见 `store/workspace.ts` 文件头与 ADR 0028。
 */
import { create } from 'zustand'
import { cancelArtifactEntry } from '@/lib/artifactValidation'
import { emitActivity } from '@/lib/activity'
import { useDocumentStore } from '@/store/documentStore'
import { useViewportStore } from '@/store/viewportStore'

export type WorkspaceMode = 'fast_edit' | 'layout'

interface WorkspaceState {
  mode: WorkspaceMode
  /**
   * 快速编辑正在编辑哪个**面板对象**（画布对象 id，不是素材 id）。
   *
   * 不变式：`mode === 'fast_edit'` ⟺ `activePanelId !== null`。同一张素材
   * 可以在文档里有多个面板实例，"哪一个"必须说得出来——用素材 id 的话，
   * 用户放了两份的那张图会在两个实例之间随机跳。
   */
  activePanelId: string | null
  /**
   * 用户要求打开某张图做图内编辑，而**那一刻它还没有源脚本**（关联还在路上）
   * ——记下这个待办，脚本关系一建立就把那次进入补上（issue #267）。
   *
   * 为什么需要它：`openFastEdit` 的"能不能进图内编辑"是一次**一次性判断**，
   * 判完就没有人再问第二遍。素材→脚本的关联是异步到达的（后端扫描 / 试运行
   * → `assets.changed` → `panelSourceSync` 原地补 `script`），双击落在关联
   * 之前时，用户的意图就此丢失且**没有任何恢复路径**——界面停在排版态，
   * 「编辑图内元素」要他自己再点一次。机器慢一点就必然发生，快一点就永远
   * 看不到，所以它表现为"偶发"。
   *
   * 只补**这一个面板、这一次**：用户如果已经走开（换了面板 / 回了排版），
   * 补进去就是把界面从他手里抢走。
   */
  pendingElementEdit: string | null
  /**
   * 「编辑原图」这一次把图**加进了文档**（它此前不在）——记下是哪个面板，
   * 快速编辑浮动条据此常驻一行说明（UI 审计 T06）。
   *
   * 为什么不是一条状态 toast：进快速编辑紧接着就是「渲染完成」那条状态，
   * 单槽位的 toast 一秒之内就被盖掉（真浏览器实测），用户根本看不见。
   * 回到画布排版 / 换文档即清；撤销把面板撤掉时快速编辑自己会退出，同一条路。
   */
  addedForEdit: string | null
  /**
   * 加入那一刻的撤销栈深度：栈还是这个深度时，「撤销」正好撤的就是这一步，通知轨上才给
   * 「移除」这颗钮；用户在图内又改了别的之后，撤销撤的是别的，钮就收起来（说明句留着）
   */
  addedForEditDepth: number
  /** 进入快速编辑（对象必须已经在激活画布里） */
  enterFastEdit: (panelId: string) => void
  /** 设置 / 清除「等源脚本到了再进图内编辑」的待办 */
  setPendingElementEdit: (panelId: string | null) => void
  /** 回到画布排版 */
  exitToLayout: () => void
  /** 换文档 / 换项目：整个清掉，不留指向旧文档对象的 id */
  clear: () => void
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  mode: 'layout',
  activePanelId: null,
  pendingElementEdit: null,
  addedForEdit: null,
  addedForEditDepth: 0,
  enterFastEdit: (panelId) => {
    cancelArtifactEntry()
    const changed = get().mode !== 'fast_edit' || get().activePanelId !== panelId
    // 记下排版视口**在这里**，不在 `openFastEdit` 里：问题面板的定位
    // （`lib/issueFocus.ts`）也是从排版进快速编辑的，它调的是这个 action
    parkLayoutView()
    // 换了一张图：上一张的「刚加入」说明不跟过来
    set({ mode: 'fast_edit', activePanelId: panelId, ...(changed ? { addedForEdit: null } : {}) })
    if (changed) emitActivity({ kind: 'workspace.mode_changed', mode: 'fast_edit' })
  },
  setPendingElementEdit: (panelId) => set({ pendingElementEdit: panelId }),
  exitToLayout: () => {
    cancelArtifactEntry()
    const changed = get().mode !== 'layout'
    // 回排版 = 用户改了主意，那个待办跟着作废（迟到的关联不该把他拽回去）
    set({ mode: 'layout', activePanelId: null, pendingElementEdit: null, addedForEdit: null })
    if (changed) emitActivity({ kind: 'workspace.mode_changed', mode: 'layout' })
  },
  // 换文档 / 换项目的清理**不发信号**：那不是用户在表达「我要回排版」
  clear: () => {
    cancelArtifactEntry()
    // 第二道保险，**没有用例杀得掉它**：新文档的画布 id 是新生成的，
    // `takeParkedLayoutView()` 的画布判据已经把跨文档还原挡住了（变异反证过）。
    // 留着是为了不让模块变量一直挂着上一份文档的状态；别把它当成被看住的保证。
    parkedLayoutView = null
    set({ mode: 'layout', activePanelId: null, pendingElementEdit: null, addedForEdit: null })
  },
}))

/**
 * 快速编辑正在编辑的面板 id；排版里 null（不变式 `mode === 'fast_edit'` ⟺ `activePanelId !== null`）。
 * 快速编辑这一屏只画这一张（`CanvasLayers only=`）：作用于选区 / 取景的动作在这一屏上只认它——
 * 全选与删除（`store/actions`）、缩放到选中与「适应」（`store/zoomToSelection`）都读这一个判据。
 */
export const fastEditPanelOf = (s: { mode: string; activePanelId: string | null }): string | null =>
  s.mode === 'fast_edit' ? s.activePanelId : null

/* -------------------------- 排版视口的寄存处 ------------------------------ */

/**
 * 进快速编辑那一刻用户在画布排版上看的是哪一片（审计 T01：**切换模式时画布
 * 不意外移动**）。
 *
 * 为什么必须记：快速编辑把那张图单独摆出来、按它自己的图幅框住，这一步一定
 * 要动视口；回来时如果只会「把那张图挪到视口中央」，用户精心摆好的排版视角
 * 就被换成了以某一张图为中心的另一片——他没做任何缩放平移，画面却变了。
 * ADR 0028 的「布局不变靠根本没动过」说的是文档，视口这一侧此前没人管。
 *
 * **带上画布 id**：`openFastEdit` 会为了找到那张图切画布（`ensurePanel`），
 * 换了画布之后记下的那一片属于**上一张画布**，还回去就是把用户送到别处。
 * 这里的主语是「哪一张画布的、哪一刻的视口」，两者缺一不可。
 *
 * 另记两件回来时要比对的事（#706 评审 P2）：停放时的**页面尺寸**（快速编辑里从画布属性
 * 改了 W / H 再改回原值，比的是最终尺寸，改回来就原样还原）；快速编辑期间**新加进这张
 * 画布的图**（`added`，由 `frameAddedPanel` 记，回排版时再决定要不要取景它们）。
 */
export interface ParkedLayoutView {
  canvasId: string
  view: ViewTarget
  page: { w: number; h: number }
  added: string[]
}

let parkedLayoutView: ParkedLayoutView | null = null

export interface ViewTarget {
  zoom: number
  panX: number
  panY: number
}

/** 只在**从排版进入**快速编辑时记一次；已经在快速编辑里换图不覆盖 */
function parkLayoutView(): void {
  if (useWorkspaceStore.getState().mode === 'fast_edit') return
  const { zoom, panX, panY, viewW, viewH } = useViewportStore.getState()
  if (!viewW || !viewH) return
  const { doc, activeCanvasId } = useDocumentStore.getState()
  parkedLayoutView = {
    canvasId: activeCanvasId,
    view: { zoom, panX, panY },
    page: { w: doc.page.w, h: doc.page.h },
    added: [],
  }
}

/** 取出并清空；画布对不上就当没记过 */
export function takeParkedLayoutView(): ParkedLayoutView | null {
  const parked = parkedLayoutView
  parkedLayoutView = null
  if (!parked) return null
  return parked.canvasId === useDocumentStore.getState().activeCanvasId ? parked : null
}

/** 快速编辑期间加进停放那张画布的图：记下来，回排版时由 `workspace.layoutViewOnReturn` 一起判 */
export function noteAddedWhileParked(panelId: string): void {
  if (parkedLayoutView?.canvasId === useDocumentStore.getState().activeCanvasId) {
    parkedLayoutView.added.push(panelId)
  }
}
