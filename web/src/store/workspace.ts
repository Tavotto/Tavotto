/**
 * 两条工作流：**单图编辑**（代码里叫 `fast_edit`）与 **多图排版**（Prompt 09，ADR 0028）。
 *
 * ```text
 * 单图编辑：画布上恰好 1 张图 → 修改 → 按原图规格导出
 * 多图排版：画布上 ≥2 张图 → 排列 → 按画布规格导出
 * ```
 *
 * ### 模式由图数推出，不是「怎么进来的」
 *
 * 激活画布上恰好 1 张（看得见的）图就是单图编辑，无论它是从素材库打开、拖入、新建项目、会话恢复，还是撤销 /
 * 删除把第二张去掉的；第二张图进来（拖放、「加到画布」、撤销一次删除……）就是多图排版，取景「页面 ∪ 图」并给一次
 * 「已进入多图排版」的通知。0 张 = 空画布（排版）。多个画布各自判（切标签即重判）。页面尺寸 / 规范 / 序号这些
 * 排版设置留在文档里，删到一张只是不画页面，再加第二张原样回来。
 *
 * **写模式的只有一处**：`workspaceStore.settleMode`（订阅 `startWorkspaceModeSync` 在每次文档变化后调它）。
 * 入口（`openFastEdit` / `addFigureToLayout`…）只改文档，不各自去 set 模式。排版里双击某张图进图内编辑仍是排版的
 * 子状态（`uiStore.elementPanelId`，「返回画布」只退出图内编辑），不是单图编辑。
 *
 * ### 一个文档，不是两套应用
 *
 * 这里**没有**第二个 documentStore、第二个 override writer、第二套对象模型。
 * 一张图在文档里只有一个面板对象，两种模式看的是**同一个对象**：
 *
 * * 单图编辑 = 把那个对象单独摆出来，按它自己的图幅显示，页面/网格/参考线
 *   /其它对象全部让开；
 * * 多图排版 = 它在页面上的落位。
 *
 * 因此「加入画布」不会复制出一份失联的图（根本没有复制这一步——对象一直是
 * 那一个，`fileId`、稳定对象 id、overrides 全程不变），而「单图编辑变多图排版」
 * 也必然保住位置与尺寸：**单图编辑一个字都不写 x/y/w/h**。
 *
 * ### 模式是工作区状态，不是文档
 *
 * `mode` / `activePanelId` 不进 `.tavotto`、不进撤销历史、不置 dirty
 * （`UX_CONTRACTS.md` §3 的数据所有权表），**也不落本机**：它是文档的纯函数，
 * 重开 / 恢复出来的文档图数是几，模式就是几——存一档只会给「存的与推的对不上」留出口。
 */
import { msg } from '@/i18n'
import { emitActivity } from '@/lib/activity'
import type { CapturedFigureDescriptor, PanelInfo } from '@/lib/api'
import { pageUnion } from '@/lib/panelPlacement'
import { addPanel, addRuntimePanel, enterElementEdit } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { activateCanvas, createCanvasAndActivate } from '@/store/canvasSession'
import { deferUserEntry } from '@/store/documentRestore'
import { findFigurePanel, useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { applyStageFit, stageFitFrame } from '@/store/zoomToSelection'
import { figuresOf, settleMode, useWorkspaceStore } from '@/store/workspaceStore'
import type { CanvasObject, PanelObject } from '@/types/document'

export {
  fastEditPanelOf,
  figuresOf,
  settleMode,
  useWorkspaceStore,
  type WorkspaceMode,
} from './workspaceStore'

type Box = { x: number; y: number; w: number; h: number }

/** 按落点 `view` 看，这块矩形（mm）是否整张落在此刻的舞台里 */
function boxInView(b: Box, view: { zoom: number; panX: number; panY: number }): boolean {
  const { viewW, viewH } = useViewportStore.getState()
  if (!viewW || !viewH) return false
  const left = mmToWorld(b.x) * view.zoom + view.panX
  const top = mmToWorld(b.y) * view.zoom + view.panY
  return (
    left >= 0 &&
    top >= 0 &&
    left + mmToWorld(b.w) * view.zoom <= viewW &&
    top + mmToWorld(b.h) * view.zoom <= viewH
  )
}

/**
 * 新加进画布的图怎么进视野——**只有这一处**判（#706 评审 P2：入口一多，逐个补必然
 * 漏，还会各自踩到工作区模式）。输入是工作区模式：
 *
 * - **多图排版上**：取景「页面 ∪ 这张图」并留在适应模式（`fitRectAnimated`）——软上限放置
 *   下稍大的图会伸出页面，那截也要看得见；之后抽屉收起 / 窗口缩放 / 切标签再切回仍按
 *   这块取景（2026-09-28 用户反馈）。拖放到一点是用户在屏幕上挑的位置：图整张已在视口
 *   里就不动视口。
 * - **单图编辑里**：这一张图的进入要么是画布上的第一张（单图，订阅已取景它本身），要么让画布变成两张
 *   （`onLeftSingle` 已在切换那一刻取过「页面 ∪ 所有图」，`framedBySwitch` 认账）——这里都不再动视口。
 */
function frameAddedPanel(panel: PanelObject, dropped: boolean): void {
  if (framedBySwitch.delete(panel.id)) return
  if (useWorkspaceStore.getState().mode === 'fast_edit') return
  const vp = useViewportStore.getState()
  if (dropped && boxInView(panel, vp)) return
  vp.fitRectAnimated(pageUnion(useDocumentStore.getState().doc.page, panel))
}

/** 刚被「单图 → 多图」切换取过景的图 id；同一调用栈里的 `frameAddedPanel` 认它跳过，下一个微任务清空 */
const framedBySwitch = new Set<string>()

/**
 * 界面上「把一张图加到画布」的入口（选图对话框、脚本库、接入状态对话框、`tavotto run`
 * 交接、舞台拖放）**一律走这两个**，不直接调 `actions.addPanel` / `addRuntimePanel`
 * （那两个只管文档）——视口怎么动由 `frameAddedPanel` 一处判。`addPanelEntry.test.ts`
 * 钉住没有别的模块直接调那两个 action。
 */
export function addPanelToCanvas(info: PanelInfo, atX?: number, atY?: number): PanelObject | null {
  // 恢复还在路上：排队到文档就绪再加（一次）；`null` = 这一刻没有加（排队 / 被拒），调用方别当成加成了。
  // **排队时丢掉落点**：舞台是按占位文档的页面 / 视口把落点换算成 mm 的，恢复出来的文档页面尺寸与取景都可能不同
  // （取景在恢复之后才适配，排队的入口先于它执行，没法保证换算对得上）——按旧页面算的坐标会让面板落在页外或别处。
  // 落地时按常规摆放规则（避让已有对象、装进页面）放，不带点
  if (deferUserEntry(() => void addPanelToCanvas(info)) !== 'run') return null
  return placePanelToCanvas(info, atX, atY)
}

/** 已过恢复门的内部版本（`createPanel` 在已经放行的入口里用它，不再排第二次队） */
function placePanelToCanvas(info: PanelInfo, atX?: number, atY?: number): PanelObject {
  const panel = addPanel(info, atX, atY)
  frameAddedPanel(panel, atX != null && atY != null)
  return panel
}

export function addRuntimePanelToCanvas(
  desc: CapturedFigureDescriptor,
  atX?: number,
  atY?: number,
  opts?: { guard?: () => boolean },
): PanelObject | null {
  // `guard` 同 `openFastEdit`：自动进入编辑的排队插入面板（连同取景）在执行前也要过守卫，切走后不留下没人要的面板
  if (
    deferUserEntry(() => {
      if (opts?.guard && !opts.guard()) return
      void addRuntimePanelToCanvas(desc)
    }) !== 'run'
  )
    return null
  return placeRuntimePanelToCanvas(desc, atX, atY)
}

function placeRuntimePanelToCanvas(desc: CapturedFigureDescriptor, atX?: number, atY?: number): PanelObject {
  const panel = addRuntimePanel(desc, atX, atY)
  frameAddedPanel(panel, atX != null && atY != null)
  return panel
}

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

export { findFigurePanel } from './documentStore'

/** 素材 id → 能加进画布的来源：磁盘图的 `PanelInfo`，或带描述符的 runtime 图 */
type FigureSource = { kind: 'file'; info: PanelInfo } | { kind: 'runtime'; desc: CapturedFigureDescriptor }

function figureSource(figureId: string): FigureSource | null {
  const info = useAssetStore.getState().byId[figureId]
  if (info) return { kind: 'file', info }
  const runtime = (useRuntimeAssetStore.getState().assets ?? []).find((a) => a.id === figureId)
  return runtime?.descriptor ? { kind: 'runtime', desc: runtime.descriptor } : null
}

/** 新画布的名字：图名（磁盘图的 `name`，runtime 图的 `stem`），不叫「新排版」 */
function figureCanvasName(src: FigureSource): string {
  return src.kind === 'file' ? src.info.name : src.desc.stem
}

/**
 * 新建面板用哪一层：`toCanvas` = `addPanelToCanvas` / `addRuntimePanelToCanvas`（连取景，
 * 「添加到画布」走它）；`docOnly` = 裸 action，只改文档、取景交给模式订阅；`newCanvas` = 只给 `openFastEdit`：
 * 先新建一张以图名命名的画布并切过去，再把图放进去（「打开」是换看那一张，不是往当前画布上加第二张，
 * 原画布一个字节不动）。
 */
function createPanel(src: FigureSource, layer: 'toCanvas' | 'docOnly' | 'newCanvas'): PanelObject {
  // 当前画布还是空的就直接用它（新项目第一次打开不留一张空画布）；有内容才新建
  if (layer === 'newCanvas' && useDocumentStore.getState().doc.objects.length > 0)
    createCanvasAndActivate(figureCanvasName(src))
  if (layer === 'toCanvas') {
    return src.kind === 'file' ? placePanelToCanvas(src.info) : placeRuntimePanelToCanvas(src.desc)
  }
  return src.kind === 'file' ? addPanel(src.info) : addRuntimePanel(src.desc)
}

/**
 * 素材 → 文档里的面板对象：有就用那一个（**绝不重复创建**），
 * 没有就按 `layer` 新建（见 `createPanel`）。runtime 素材走它自己那条添加路径。
 */
function ensurePanel(
  figureId: string,
  layer: 'toCanvas' | 'docOnly' | 'newCanvas',
): { panel: PanelObject; created: boolean } | null {
  const found = findFigurePanel(figureId)
  if (found) {
    if (found.canvasId !== useDocumentStore.getState().activeCanvasId) {
      activateCanvas(found.canvasId)
    }
    // 切画布会换掉 doc，对象引用要重新取（id 不变）
    const fresh = useDocumentStore.getState().doc.objects.find((o) => o.id === found.panel.id)
    return fresh?.type === 'panel' ? { panel: fresh, created: false } : null
  }
  const src = figureSource(figureId)
  return src ? { panel: createPanel(src, layer), created: true } : null
}

/** `deferred` = 上次的排版还在恢复，已排队、就绪后执行一次；`blocked` = 需要用户先处理恢复问题，没执行 */
export type OpenFastEditOutcome = 'editing' | 'layout_only' | 'missing' | 'deferred' | 'blocked'

/**
 * 打开一张图 = **换看那一张**（2026-10-21 用户拍板），不是往当前画布上再加一张：
 *
 * - 它已单独在某张画布上 → 切到那张画布（单图编辑）；
 * - 它在多图画布上 → 切到那张画布，选中它并对它进图内编辑（排版的子状态）；
 * - 它不在任何画布上 → 新建一张以图名命名的画布放它（单图编辑），原画布一个字节不动；当前画布本来就是空的则直接用它。
 *   新建画布不进撤销栈（`addCanvas` 全仓库都不可撤销），放图这一步是一条可撤销的历史：撤销把图撤掉，
 *   画布留着、是空的（排版），用户可以关掉它。
 * 「拖入 / 加到画布」才是加第二张（`addPanelToCanvas` / `addFigureToLayout`）。
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
export function openFastEdit(figureId: string, opts?: { guard?: () => boolean }): OpenFastEditOutcome {
  // `guard`（只有自动进入编辑传）：入口在恢复期间排队时，真正执行前再核一次「用户还要这个入口」——
  // 不满足就整个不执行（不替换他此刻的工作区）；用户手动的入口不传，行为不变
  const entry = deferUserEntry(() => {
    if (opts?.guard && !opts.guard()) return
    void openFastEdit(figureId)
  })
  if (entry !== 'run') return entry === 'deferred' ? 'deferred' : 'blocked'
  const got = ensurePanel(figureId, 'newCanvas')
  if (!got) {
    useUiStore
      .getState()
      .setStatus(msg('fastEdit.figureMissing', { name: figureId }, 'workspace'), 'error')
    return 'missing'
  }
  const { panel, created } = got
  // 模式由图数推出：新画布上放的这一张 = 1 张，订阅已经对过；这里再对一遍是幂等的，订阅没挂的调用方（单元测试）也一致
  settleMode()
  if (created) frameAddedPanel(panel, false)
  // 「编辑原图」把还不在文档里的图加了进来——这一步必须说出口（结构上躲不掉：
  // 单图编辑的对象只能是文档里的面板对象，见文件头；但用户点的是"编辑"，看到
  // 的却是版本预览多了一个对象、问题面板多了一批问题，UI 审计 T06）。它是一条
  // 可撤销的历史，撤销即移除。图**已经在文档里**时什么都不说——那一次零文档改动。
  useWorkspaceStore.setState({
    addedForEdit: created ? panel.id : null,
    addedForEditDepth: useDocumentStore.getState().past.length,
  })
  // 绘制工具画的是画布标注，单图编辑这一屏上根本没有它们的位置——
  // 停在「箭头」工具上进来，光标是十字而点下去什么也看不见
  useUiStore.getState().setTool('select')
  useSelectionStore.getState().set([panel.id])
  // 刚加进来的图已经取过景了（单图 = 取它本身的矩形，第二张 = 页面 ∪ 图，且留在适应模式）；
  // 再 `revealRect` 会退出适应模式。只有原本就在文档里的图才需要滚进视野
  if (!created) revealPanel(panel)
  if (panel.script) {
    useWorkspaceStore.getState().setPendingElementEdit(null)
    enterElementEdit(panel.id)
    emitActivity({ kind: 'figure.opened_fast_edit', outcome: 'editing' })
    return 'editing'
  }
  useUiStore.getState().setElementPanel(null)
  // **这一刻**没有源脚本，不代表这张图不可编辑：素材→脚本的关联是异步到达的
  // （后端扫描 / 试运行 → `assets.changed` → `panelSourceSync` 原地补
  // `script`）。把用户的意图记下来，关联建立时由 `liveSync` 补上那次进入
  // ——否则这条判断就是一次性的，双击落在关联之前时意图**永久丢失**
  // （issue #267：机器慢一点就必然发生，快一点就永远看不到）。
  useWorkspaceStore.getState().setPendingElementEdit(panel.id)
  emitActivity({ kind: 'figure.opened_fast_edit', outcome: 'layout_only' })
  return 'layout_only'
}

export type AddToLayoutOutcome = 'added' | 'focused' | 'missing' | 'deferred' | 'blocked'

/**
 * 「添加到画布」。**已经在文档里就只是聚焦它**——重复点不会叠出第二个面板，
 * 也不会把 overrides 复制到一个新对象上（那份复制品之后就与原件失联了）。
 */
export function addFigureToLayout(figureId: string): AddToLayoutOutcome {
  const entry = deferUserEntry(() => void addFigureToLayout(figureId))
  if (entry !== 'run') return entry === 'deferred' ? 'deferred' : 'blocked'
  const willCreate = !findFigurePanel(figureId)
  if (willCreate && !figureSource(figureId)) {
    useUiStore
      .getState()
      .setStatus(msg('fastEdit.figureMissing', { name: figureId }, 'workspace'), 'error')
    return 'missing'
  }
  // 要新建：经 `addPanelToCanvas` 加——第二张图进来时模式订阅切到多图排版并取景「页面 ∪ 图」，
  // 这里不再 `revealRect`：那会退出适应模式，抽屉一收页面就偏在一边（2026-09-28 用户反馈）。
  // 已在文档里的只是聚焦、滚进视野
  if (willCreate) useUiStore.getState().setElementPanel(null)
  const modeBefore = useWorkspaceStore.getState().mode
  const got = ensurePanel(figureId, 'toCanvas')
  if (!got) return 'missing' // 上面已判过来源；这里只防同步期间素材被清掉
  settleMode() // 幂等：订阅在场时它早已对过
  focusLayoutPanel(got.panel.id, { reveal: !got.created })
  const name = got.panel.name ?? got.panel.fileId
  // 这一下把单图编辑切成了多图排版：说话的是「已进入多图排版」那条通知，不用「已加入」把它盖掉（单槽位 toast）
  const switched = modeBefore === 'fast_edit' && useWorkspaceStore.getState().mode === 'layout'
  if (!switched)
    useUiStore
      .getState()
      .setStatus(msg(got.created ? 'fastEdit.added' : 'fastEdit.alreadyOnCanvas', { name }, 'workspace'), got.created ? 'done' : 'info')
  emitActivity({ kind: 'figure.added_to_layout', outcome: got.created ? 'added' : 'focused' })
  return got.created ? 'added' : 'focused'
}

/**
 * 定位到画布上的某个面板：切到它所在的画布、选中、滚进视野。
 * Prompt 11 的问题面板与 Prompt 12 的导出报告直接调它。
 */
export function focusLayoutPanel(panelId: string, opts?: { reveal?: boolean }): boolean {
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
  useUiStore.getState().setElementPanel(null)
  useSelectionStore.getState().set([panelId])
  if (opts?.reveal !== false) revealPanel(obj)
  return true
}

/** 把对象滚进视野。**只动视口，不动文档**——视口不是用户数据 */
function revealPanel(o: CanvasObject): void {
  useViewportStore.getState().revealRect({ x: o.x, y: o.y, w: o.w, h: o.h })
}

/* ------------------------- 模式订阅（图数 → 模式） ------------------------- */

/** 单图编辑 → 多图排版（`prevPanelId` 是刚才在编辑的那张）：取景「页面 ∪ 所有图」并留在适应模式，说一次 */
function onLeftSingle(prevPanelId: string | null): void {
  const { doc } = useDocumentStore.getState()
  const figs = figuresOf(doc.objects)
  useViewportStore.getState().fitRectAnimated(pageUnion(doc.page, ...figs))
  // `prevPanelId` 之外的都是这一次新进来的（或撤销 / 取消隐藏回来的）：同一调用栈里的入口不必再各取一遍景
  for (const f of figs) if (f.id !== prevPanelId) framedBySwitch.add(f.id)
  queueMicrotask(() => framedBySwitch.clear())
  useUiStore.getState().setStatus(msg('fastEdit.enteredLayout', undefined, 'workspace'), 'info')
  emitActivity({ kind: 'workspace.entered_layout' })
}

/**
 * 订阅：文档一变（对象增删 / 隐藏、撤销重做、切画布、换文档）就按图数对一次模式（`settleMode`），
 * 并按转换方向动视口。**挂一次**（App 里与其它同步器并列），启动时先对一遍。
 *
 * 视口只在「同一份文档、同一张画布」里图数变化时动；切画布 / 载入 / 换项目各有自己的适配点
 * （`canvasSession` / `afterSwitch` / 会话恢复），这里一律不抢着写（与 `startPageSizeFit` 同一条纪律）：
 * - 变成单图：取景那一张图本身（`applyStageFit`，与 ⌘1 同一个框）；
 * - 单图变多图：`onLeftSingle`；
 * - 单图变 0 张（唯一一张被删 / 隐藏）：回到按页面取景。
 */
export function startWorkspaceModeSync(): () => void {
  settleMode()
  return useDocumentStore.subscribe((state, prev) => {
    const switched =
      state.documentId !== prev.documentId ||
      state.loadSeq !== prev.loadSeq ||
      state.activeCanvasId !== prev.activeCanvasId
    if (!switched && state.doc.objects === prev.doc.objects) return
    const was = useWorkspaceStore.getState()
    const wasMode = was.mode
    const wasPanel = was.activePanelId
    settleMode()
    if (switched) return
    const now = useWorkspaceStore.getState()
    if (wasMode === 'fast_edit' && now.mode === 'layout') {
      if (figuresOf(state.doc.objects).length >= 2) {
        onLeftSingle(wasPanel)
      } else {
        useViewportStore.getState().fitAnimated(state.doc.page.w, state.doc.page.h)
      }
    } else if (now.mode === 'fast_edit' && (wasMode !== 'fast_edit' || wasPanel !== now.activePanelId)) {
      applyStageFit(stageFitFrame(state.doc.objects, state.doc.page, now.activePanelId), true)
    } else if (now.mode === 'fast_edit' && now.activePanelId === wasPanel) {
      // 同一张图的图幅被静默校正了（渲染回来按真实图幅校正 w / h）而视口还停在「适应」上（没平移 / 缩放过）：
      // 取景框按新图幅重算——否则放大取景是按占位图幅算的，校正之后图伸出舞台
      const was = prev.doc.objects.find((o) => o.id === wasPanel)
      const cur = state.doc.objects.find((o) => o.id === wasPanel)
      if (
        was && cur && (was.w !== cur.w || was.h !== cur.h) &&
        // 只认**没进历史**的静默改动（渲染回来的图幅校正走 `silent`）：用户的缩放 / 改图幅 / 采用外框都是
        // 一条历史，那些动作的画面该保持原位（标题落在屏幕上哪里不许因此挪）
        state.past.length === prev.past.length &&
        useViewportStore.getState().fitted &&
        useInteractionStore.getState().kind === 'none'
      ) {
        applyStageFit(stageFitFrame(state.doc.objects, state.doc.page, now.activePanelId), true)
      }
    }
  })
}
