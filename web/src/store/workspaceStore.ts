/**
 * 工作区模式的状态本体（`useWorkspaceStore`）与模式的唯一写入点 `settleMode`——从 `store/workspace.ts` 拆出的叶子模块。
 *
 * 为什么单独一个文件：`store/actions` 要读「快速编辑正在编辑哪张图」（`fastEditPanelOf`：全选 / 删除 / 创建副本
 * 只认那张图，Codex #833），而 `store/workspace` 的工作流动作（`openFastEdit` / `addFigureToLayout`…）要调
 * `actions.addPanel` 等。两者互相 import 就是一个环（`importArchitecture.test.ts`）。这里**不许** import
 * `store/actions` 或 `store/workspace`；工作流动作、持久化与对外 API 仍在 `store/workspace.ts`（它原样再导出本文件的
 * 公开名字，既有调用方不用改）。模式规则全文见 `store/workspace.ts` 文件头与 ADR 0028。
 *
 * **模式是画布上图数推出来的派生值**（`settleMode`）：激活画布上恰好 1 张看得见的图 = `fast_edit`（单图编辑），
 * 其余（0 张 / ≥2 张）= `layout`。`mode` / `activePanelId` 仍是存下来的字段（既有消费者都读它们），但**写它们的只有
 * `settleMode`**（外加换文档的 `clear`）——入口不许各自去 `set`。
 */
import { create } from 'zustand'
import { cancelArtifactEntry } from '@/lib/artifactValidation'
import { emitActivity } from '@/lib/activity'
import { useDocumentStore } from '@/store/documentStore'
import type { CanvasObject, PanelObject } from '@/types/document'

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
  /**
   * 进入快速编辑（对象必须已经在激活画布里）。**只有 `settleMode` 调它**——模式由图数推出，
   * 业务入口不许直接进（直接进会造出「两张图却在单图编辑」的不一致态）
   */
  enterFastEdit: (panelId: string) => void
  /** 设置 / 清除「等源脚本到了再进图内编辑」的待办 */
  setPendingElementEdit: (panelId: string | null) => void
  /** 离开快速编辑（回排版态）。**只有 `settleMode` / `clear` 调它** */
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
    set({ mode: 'layout', activePanelId: null, pendingElementEdit: null, addedForEdit: null })
  },
}))

/**
 * 一张画布上「算数」的图：面板（figure）。**判据只有这一处**——runtime 图与磁盘图同为 `panel`、同等计；
 * 标注 / 文字 / 形状 / 箭头不是图，不计；**隐藏的图也计**（2026-10-21 用户拍板：隐藏是排版操作，不改模式、不发通知）。
 */
export function figuresOf(objects: readonly CanvasObject[]): PanelObject[] {
  return objects.filter((o): o is PanelObject => o.type === 'panel')
}

/**
 * **模式的唯一写入点**：按激活画布上的图数把 `mode` / `activePanelId` 对到该有的值——恰好 1 张 = 单图编辑
 * （`fast_edit`，对着那一张），0 张或 ≥2 张 = 排版。幂等：已经对了就什么都不做，所以入口（`openFastEdit`…）
 * 与订阅（`workspace.startWorkspaceModeSync`）可以都调它而不会打架。多画布各自判：它读的是激活画布的 `doc.objects`。
 */
export function settleMode(): void {
  const figs = figuresOf(useDocumentStore.getState().doc.objects)
  const ws = useWorkspaceStore.getState()
  if (figs.length === 1) {
    if (ws.mode !== 'fast_edit' || ws.activePanelId !== figs[0].id) ws.enterFastEdit(figs[0].id)
  } else if (ws.mode === 'fast_edit') {
    ws.exitToLayout()
  }
}

/**
 * 快速编辑正在编辑的面板 id；排版里 null（不变式 `mode === 'fast_edit'` ⟺ `activePanelId !== null`）。
 * 快速编辑这一屏只画这一张（`CanvasLayers only=`）：作用于选区 / 取景的动作在这一屏上只认它——
 * 全选与删除（`store/actions`）、缩放到选中与「适应」（`store/zoomToSelection`）都读这一个判据。
 */
export const fastEditPanelOf = (s: { mode: string; activePanelId: string | null }): string | null =>
  s.mode === 'fast_edit' ? s.activePanelId : null
