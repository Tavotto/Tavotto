/**
 * 两条工作流：**快速编辑** 与 **画布排版**（Prompt 09，ADR 0028）。
 *
 * ```text
 * 快速编辑：打开一张图 → 修改 → 按原图规格导出
 * 画布排版：加入多张图 → 排列 → 按画布规格导出
 * ```
 *
 * ### 一个文档，不是两套应用
 *
 * 这里**没有**第二个 documentStore、第二个 override writer、第二套对象模型。
 * 一张图在文档里只有一个面板对象，两种模式看的是**同一个对象**：
 *
 * * 快速编辑 = 把那个对象单独摆出来，按它自己的图幅显示，页面/网格/参考线
 *   /其它对象全部让开；
 * * 画布排版 = 它在页面上的落位。
 *
 * 因此「加入画布」不会复制出一份失联的图（根本没有复制这一步——对象一直是
 * 那一个，`fileId`、稳定对象 id、overrides 全程不变），而「从画布进图内编辑
 * 再返回」也必然保住位置与尺寸：**快速编辑一个字都不写 x/y/w/h**。
 *
 * ### 模式是工作区状态，不是文档
 *
 * `mode` / `activePanelId` 不进 `.tavotto`、不进撤销历史、不置 dirty
 * （`UX_CONTRACTS.md` §3 的数据所有权表）。它按 documentId 存本机一档，
 * 与打开的画布标签同一条纪律：恢复时**先验对象还在不在**，不在就回排版模式
 * ——指着一个已经被删掉的对象的"快速编辑"是一个打不开的界面。
 */
import { create } from 'zustand'
import { msg } from '@/i18n'
import { addPanel, addRuntimePanel, enterElementEdit } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { activateCanvas } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import type { CanvasObject, PanelObject } from '@/types/document'

export type WorkspaceMode = 'fast_edit' | 'layout'

const KEY_PREFIX = 'tavotto.workspace.'

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
  /** 进入快速编辑（对象必须已经在激活画布里） */
  enterFastEdit: (panelId: string) => void
  /** 回到画布排版 */
  exitToLayout: () => void
  /** 换文档 / 换项目：整个清掉，不留指向旧文档对象的 id */
  clear: () => void
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  mode: 'layout',
  activePanelId: null,
  enterFastEdit: (panelId) => set({ mode: 'fast_edit', activePanelId: panelId }),
  exitToLayout: () => set({ mode: 'layout', activePanelId: null }),
  clear: () => set({ mode: 'layout', activePanelId: null }),
}))

/** 当前快速编辑的那个面板对象；不在激活画布里就回 null */
export function activeFigurePanel(): PanelObject | null {
  const id = useWorkspaceStore.getState().activePanelId
  if (!id) return null
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === id)
  return o?.type === 'panel' ? o : null
}

/* ------------------------------ 稳定动作 ---------------------------------- */
/*
 * 下面四个是 Prompt 11（定位）、12（导出）、18（QuickEdit）、21（onboarding）
 * 复用的入口。**它们是这条工作流的唯一出口**——别在界面里重新拼一遍
 * 「找对象 / 没有就添加 / 切画布 / 选中」，那正是同一件事有两份判据的开头。
 */

/**
 * 找文档里代表这张素材的面板：激活画布优先，其次别的画布。
 *
 * **这是"文档里有没有这张图"的唯一判据**——交接（`lib/openRequest.ts`）、
 * 原图规格（`lib/originalSpec.ts`）、这里的三个动作用的都是它。各写一遍的
 * 后果是"已经在画布上了"这句话在几个入口给出不同答案。
 */
export function findFigurePanel(
  figureId: string,
): { panel: PanelObject; canvasId: string } | null {
  const s = useDocumentStore.getState()
  const here = s.doc.objects.find(
    (o): o is PanelObject => o.type === 'panel' && o.fileId === figureId,
  )
  if (here) return { panel: here, canvasId: s.activeCanvasId }
  for (const c of s.canvases) {
    if (c.id === s.activeCanvasId) continue
    const o = c.objects.find(
      (x): x is PanelObject => x.type === 'panel' && x.fileId === figureId,
    )
    if (o) return { panel: o, canvasId: c.id }
  }
  return null
}

/**
 * 素材 → 文档里的面板对象：有就用那一个（**绝不重复创建**），
 * 没有就通过既有的统一 action 添加。runtime 素材走它自己那条添加路径。
 */
function ensurePanel(figureId: string): { panel: PanelObject; created: boolean } | null {
  const found = findFigurePanel(figureId)
  if (found) {
    if (found.canvasId !== useDocumentStore.getState().activeCanvasId) {
      activateCanvas(found.canvasId)
    }
    // 切画布会换掉 doc，对象引用要重新取（id 不变）
    const fresh = useDocumentStore.getState().doc.objects.find((o) => o.id === found.panel.id)
    return fresh?.type === 'panel' ? { panel: fresh, created: false } : null
  }
  const info = useAssetStore.getState().byId[figureId]
  if (info) return { panel: addPanel(info), created: true }
  const runtime = (useRuntimeAssetStore.getState().assets ?? []).find((a) => a.id === figureId)
  if (runtime?.descriptor) return { panel: addRuntimePanel(runtime.descriptor), created: true }
  return null
}

export type OpenFastEditOutcome = 'editing' | 'layout_only' | 'missing'

/**
 * 打开一张图 → 进快速编辑工作区。
 *
 * **这是普通打开行为**，不是一个需要先选模式的启动页：素材卡双击、
 * `tavotto open <stem>` 的交接、接入状态里的「打开」，走的都是它。
 *
 * 返回 `'layout_only'` 表示图进来了但进不了图内编辑（没有源脚本）——
 * 那不是失败，界面照实说明并给出「连接源脚本 / 继续排版」两条路。
 * 为什么这里用 `panel.script` 判：它就是既有的图内编辑判据
 * （`ObjectView` 双击、`enterElementEdit`）。**不在这里另起一份判据**
 * ——状态与措辞归 `lib/readinessText.ts`，两者不是一件事。
 */
export function openFastEdit(figureId: string): OpenFastEditOutcome {
  const got = ensurePanel(figureId)
  if (!got) {
    useUiStore
      .getState()
      .setStatus(msg('fastEdit.figureMissing', { name: figureId }, 'workspace'), 'error')
    return 'missing'
  }
  const { panel } = got
  useWorkspaceStore.getState().enterFastEdit(panel.id)
  // 绘制工具画的是画布标注，快速编辑这一屏上根本没有它们的位置——
  // 停在「箭头」工具上进来，光标是十字而点下去什么也看不见
  useUiStore.getState().setTool('select')
  useSelectionStore.getState().set([panel.id])
  revealPanel(panel)
  if (panel.script) {
    enterElementEdit(panel.id)
    return 'editing'
  }
  useUiStore.getState().setElementPanel(null)
  return 'layout_only'
}

export type AddToLayoutOutcome = 'added' | 'focused' | 'missing'

/**
 * 「添加到画布」。**已经在文档里就只是聚焦它**——重复点不会叠出第二个面板，
 * 也不会把 overrides 复制到一个新对象上（那份复制品之后就与原件失联了）。
 */
export function addFigureToLayout(figureId: string): AddToLayoutOutcome {
  const got = ensurePanel(figureId)
  if (!got) {
    useUiStore
      .getState()
      .setStatus(msg('fastEdit.figureMissing', { name: figureId }, 'workspace'), 'error')
    return 'missing'
  }
  focusLayoutPanel(got.panel.id)
  const name = got.panel.name ?? got.panel.fileId
  useUiStore
    .getState()
    .setStatus(msg(got.created ? 'fastEdit.added' : 'fastEdit.alreadyOnCanvas', { name }, 'workspace'))
  return got.created ? 'added' : 'focused'
}

/** 回到画布排版；当前那张图仍然选中，位置与尺寸一个字节没动过 */
export function returnToLayout(): void {
  const panel = activeFigurePanel()
  useWorkspaceStore.getState().exitToLayout()
  useUiStore.getState().setElementPanel(null)
  if (panel) {
    useSelectionStore.getState().set([panel.id])
    revealPanel(panel)
  } else {
    const page = useDocumentStore.getState().doc.page
    useViewportStore.getState().fit(page.w, page.h)
  }
}

/**
 * 定位到画布上的某个面板：切到它所在的画布、选中、滚进视野。
 * Prompt 11 的问题面板与 Prompt 12 的导出报告直接调它。
 */
export function focusLayoutPanel(panelId: string): boolean {
  const s = useDocumentStore.getState()
  const inActive = s.doc.objects.find((o) => o.id === panelId)
  if (!inActive) {
    const owner = s.canvases.find(
      (c) => c.id !== s.activeCanvasId && c.objects.some((o) => o.id === panelId),
    )
    if (!owner) return false
    activateCanvas(owner.id)
  }
  const obj = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (!obj) return false
  useWorkspaceStore.getState().exitToLayout()
  useUiStore.getState().setElementPanel(null)
  useSelectionStore.getState().set([panelId])
  revealPanel(obj)
  return true
}

/** 把对象滚进视野。**只动视口，不动文档**——视口不是用户数据 */
function revealPanel(o: CanvasObject): void {
  useViewportStore.getState().revealRect({ x: o.x, y: o.y, w: o.w, h: o.h })
}

/* ---------------------------- 本机持久化 ---------------------------------- */

interface Persisted {
  mode: WorkspaceMode
  panelId: string | null
}

/**
 * 本机那一档说「上次停在哪个面板上」。**只有一处判据**：模式不是
 * `fast_edit`、没有 panelId、blob 坏了，都是同一个答案——没有目标。
 *
 * 曾经这里先校验一遍 `mode` 的取值、`restoreWorkspace` 再判一次是不是
 * `fast_edit`，两句话说的是同一件事，于是把前一句改成恒真也没有任何用例会
 * 红（变异反证里它活了下来）。冗余的保证杀不死，处置是合成一处，不是造个
 * 输入去覆盖它。
 */
function readFastEditTarget(documentId: string): string | null {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + documentId)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<Persisted>
    return v.mode === 'fast_edit' && typeof v.panelId === 'string' ? v.panelId : null
  } catch {
    return null
  }
}

/**
 * 恢复上次的模式。**先验对象还在不在**：文档换过、那个面板被删掉、
 * 或者存的是另一个文档的 id 时，一律回排版模式——恢复出一个指向不存在对象
 * 的快速编辑，用户看到的是一个空工作区，而且退不出来。
 */
export function restoreWorkspace(documentId: string, objects: readonly CanvasObject[]): void {
  const store = useWorkspaceStore.getState()
  const panelId = readFastEditTarget(documentId)
  const o = panelId ? objects.find((x) => x.id === panelId) : undefined
  if (o?.type !== 'panel' || o.hidden) {
    store.clear()
    return
  }
  store.enterFastEdit(o.id)
}

/**
 * 订阅：文档换代就恢复一次，模式变了就存一次。**一个订阅，不是两个**
 * ——两个的话「载入时写回一次」与「恢复」会互相盖。
 */
export function startWorkspacePersistence(): () => void {
  let documentId = useDocumentStore.getState().documentId
  restoreWorkspace(documentId, useDocumentStore.getState().doc.objects)

  const stopDoc = useDocumentStore.subscribe((s) => {
    if (s.documentId === documentId) return
    documentId = s.documentId
    restoreWorkspace(documentId, s.doc.objects)
  })
  const stopMode = useWorkspaceStore.subscribe((s, prev) => {
    if (s.mode === prev.mode && s.activePanelId === prev.activePanelId) return
    try {
      localStorage.setItem(
        KEY_PREFIX + documentId,
        JSON.stringify({ mode: s.mode, panelId: s.activePanelId } satisfies Persisted),
      )
    } catch {
      /* 存不下只影响「下次打开还在这张图上」 */
    }
  })
  return () => {
    stopDoc()
    stopMode()
  }
}
