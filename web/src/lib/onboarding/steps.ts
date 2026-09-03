/**
 * 教程的九个真实步骤：每一步的**完成条件**与 **coachmark 挂哪**。
 *
 * ### 完成条件来自真实状态与真实信号，不来自「下一步」按钮
 *
 * 两类判据：
 *   * **状态可以说清的**直接读 store（进了哪张图的图内编辑、主选是哪个元素、
 *     两张图在不在画布上、导出面板开没开）——这也天然覆盖「用户提前做完了」：
 *     状态已经在那儿，步骤一到就完成；
 *   * **状态说不清的**（改过字号没有、定位成功过没有、导出面板里确认过原图
 *     没有）读 `StepSignals`——它由 `flow.ts` 按活动信号累计、按步骤消费。
 *
 * ### 目标对象按 metadata 现找，不记 id
 *
 * 「教程里要编辑的那张图」= `tutorial_meta.panels` 里带 `spec_issue` 的那一张
 * （第二张 Fig2：故意留了一条 7 pt 文字，问题面板才有东西可定位——问题是从
 * 渲染后的 manifest 算出来的，没进过编辑的图不会有图内问题）。对象靠
 * `findFigurePanel(file)` 现查，元素靠 manifest 的 role 现查；重置 / 重建之后
 * id 变了也连不错。
 *
 * ### 不做的事
 *
 * 这里**一个字都不写文档**、不发请求、不改用户偏好；`reveal()` 只做「把折叠的
 * 侧栏临时露出来」这一件事，且不经过 uiStore 的 persist。
 */
import type { ManifestElement, TutorialMetadata, TutorialPanelMeta } from '@/lib/api'
import { propertyPathOf } from '@/lib/typography'
import type { ValidationIssue } from '@/lib/validation'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useViewportStore } from '@/store/viewportStore'
import { findFigurePanel, useWorkspaceStore, type WorkspaceMode } from '@/store/workspace'
import type { PanelObject } from '@/types/document'
import { STEP_IDS, type StepId } from './stepIds'

/* ------------------------------- 信号累计 --------------------------------- */

/**
 * 由 `flow.ts` 累计的「发生过没有」。每一项都是计数，步骤完成时把它消费掉的
 * 那几项清零（`StepDef.consumes`），于是重放 / 重复的信号不会连着完成两步。
 */
export interface StepSignals {
  typographyChanged: number
  historyPushed: number
  /** 定位成功且落在教程面板上 */
  problemFocused: number
  /** 导出面板开着时输出范围是「原图」 */
  exportOriginalSeen: number
  /** 导出面板开着时输出范围是「画布」 */
  exportCanvasSeen: number
  /** 对齐动作成功，且那一刻选区里至少两张教程图 */
  alignedTutorialPanels: number
}

export const EMPTY_SIGNALS: StepSignals = {
  typographyChanged: 0,
  historyPushed: 0,
  problemFocused: 0,
  exportOriginalSeen: 0,
  exportCanvasSeen: 0,
  alignedTutorialPanels: 0,
}

/* -------------------------------- 上下文 ---------------------------------- */

export interface TutorialPanelRef {
  meta: TutorialPanelMeta
  panel: PanelObject
}

export interface StepContext {
  meta: TutorialMetadata | null
  /** 要编辑的那张（带 spec_issue 的）；文档里找不到就是 null */
  edit: TutorialPanelRef | null
  /** 另一张 */
  other: TutorialPanelRef | null
  /** 文档里现存的全部教程面板 id */
  tutorialPanelIds: Set<string>
  mode: WorkspaceMode
  activePanelId: string | null
  elementPanelId: string | null
  selectedGids: string[]
  selectionIds: string[]
  /** 要编辑那张图的 manifest 元素（渲染过才有） */
  elements: ManifestElement[] | null
  problemsOpen: boolean
  issues: ValidationIssue[]
  validationReady: boolean
  exportOpen: boolean
  signals: StepSignals
}

/** 文字类 role：教程 Step 2 认的目标 */
export const TEXT_ROLES: ReadonlySet<string> = new Set(['title', 'legend_text', 'axis_label', 'text'])

/** Step 3 认的图内文字排版属性（manifest 的 prop 名，与 `lib/typography` 同源） */
export const TYPOGRAPHY_PROPS_FIGURE: ReadonlySet<string> = new Set(
  (['fontFamily', 'sizePt', 'weight', 'style', 'color'] as const)
    .map((p) => propertyPathOf('figureText', p))
    .filter((v): v is string => !!v),
)

/** 教程里「要编辑的那张」：带 spec_issue 的，没有就第一张 */
export const editPanelMeta = (meta: TutorialMetadata): TutorialPanelMeta | undefined =>
  meta.panels.find((p) => p.spec_issue) ?? meta.panels[0]

const refOf = (pm: TutorialPanelMeta | undefined): TutorialPanelRef | null => {
  if (!pm) return null
  const hit = findFigurePanel(pm.file)
  return hit ? { meta: pm, panel: hit.panel } : null
}

export function buildContext(meta: TutorialMetadata | null, signals: StepSignals): StepContext {
  const ws = useWorkspaceStore.getState()
  const ui = useUiStore.getState()
  const val = useValidationStore.getState()
  const editMeta = meta ? editPanelMeta(meta) : undefined
  const edit = refOf(editMeta)
  const other = meta ? refOf(meta.panels.find((p) => p !== editMeta)) : null
  const tutorialPanelIds = new Set<string>()
  if (meta) {
    for (const pm of meta.panels) {
      const hit = findFigurePanel(pm.file)
      if (hit) tutorialPanelIds.add(hit.panel.id)
    }
  }
  // 元素表按**激活画布上的对象**取：findFigurePanel 可能回别的画布上的面板，
  // 而 panelRender 的键只看 fileId + overrides，两处一致
  const elements = edit ? (panelRender(useRenderStore.getState(), edit.panel)?.manifest?.elements ?? null) : null
  return {
    meta,
    edit,
    other,
    tutorialPanelIds,
    mode: ws.mode,
    activePanelId: ws.activePanelId,
    elementPanelId: ui.elementPanelId,
    selectedGids: ui.selectedGids,
    selectionIds: useSelectionStore.getState().ids,
    elements,
    problemsOpen: ui.leftOpen && ui.leftTab === 'problems',
    issues: val.issues,
    validationReady: val.ready,
    exportOpen: ui.exportOpen,
    signals,
  }
}

/* ------------------------------- 锚点描述 --------------------------------- */

/**
 * coachmark 挂哪。`selector` 是稳定的机器标识（`data-*` 属性），**不是**
 * aria-label / 文案 / class；`rect` 用于「图里的某个元素」这种没有自己 DOM
 * 节点的目标（按 manifest bbox 映射到 SVG 容器上）。
 */
export type AnchorSpec =
  | { kind: 'selector'; selector: string }
  | { kind: 'element'; panelId: string; bbox: [number, number, number, number] }
  | { kind: 'none' }

const sel = (selector: string): AnchorSpec => ({ kind: 'selector', selector })
const NONE: AnchorSpec = { kind: 'none' }

const esc = (v: string) => (typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(v) : v)

/** 教程里首选的文字元素（按 `editable_role_preferences` 的顺序） */
export function preferredTextElement(ctx: StepContext): ManifestElement | null {
  if (!ctx.elements || !ctx.meta || !ctx.edit) return null
  const allowed = new Set(ctx.edit.meta.editable_roles.filter((r) => TEXT_ROLES.has(r)))
  for (const role of ctx.meta.editable_role_preferences) {
    if (!allowed.has(role)) continue
    const el = ctx.elements.find((e) => e.role === role)
    if (el) return el
  }
  return ctx.elements.find((e) => allowed.has(e.role)) ?? null
}

/** 主选（末位）元素的 role */
function primaryElementRole(ctx: StepContext): string | null {
  const gid = ctx.selectedGids.at(-1)
  if (!gid || !ctx.elements) return null
  return ctx.elements.find((e) => e.gid === gid)?.role ?? null
}

/** 落在教程面板上的问题 */
export const tutorialIssues = (ctx: StepContext): ValidationIssue[] =>
  ctx.issues.filter((i) => !!i.objectRef.objectId && ctx.tutorialPanelIds.has(i.objectRef.objectId))

/** Step 4 的「已解决」出口：那张图渲染过、检查跑过、教程面板上一条问题都没有 */
export const problemsResolved = (ctx: StepContext): boolean =>
  !!ctx.elements && ctx.validationReady && tutorialIssues(ctx).length === 0

/** 两张教程图都在文档里 */
const bothOnCanvas = (ctx: StepContext): boolean =>
  !!ctx.meta && ctx.meta.panels.length >= 2 && ctx.tutorialPanelIds.size >= 2

/* -------------------------------- 步骤表 ---------------------------------- */

export interface StepDef {
  id: StepId
  /** 要用户点「开始 / 完成」的步骤（没有可自动识别的动作） */
  manual?: boolean
  /** 完成条件（manual 的步骤不会被自动判完） */
  done: (ctx: StepContext) => boolean
  /** 完成时清零哪些信号（防重放连着完成两步） */
  consumes?: (keyof StepSignals)[]
  anchor: (ctx: StepContext) => AnchorSpec
  /**
   * 文案变体：同一步在不同阶段说不同的话（多选前 / 多选后），返回 i18n 的
   * 子 key；不给就用步骤 id
   */
  variant?: (ctx: StepContext) => string
  /** 目标藏在折叠的侧栏 / 抽屉里时，把它临时露出来（**不写偏好**） */
  reveal?: (ctx: StepContext) => void
  /** Step 4 的「已解决」这类可由用户确认的替代出口 */
  altDone?: (ctx: StepContext) => boolean
}

/** 临时打开左侧某一页：直接 setState，不经 `setLeftTab`（那条会 persist 偏好） */
function peekLeft(tab: 'assets' | 'problems'): void {
  const ui = useUiStore.getState()
  if (ui.leftOpen && ui.leftTab === tab) return
  useUiStore.setState(
    ui.layout !== 'wide' ? { leftOpen: true, leftTab: tab, rightOpen: false } : { leftOpen: true, leftTab: tab },
  )
}

/** 临时打开右侧属性页（同上，不写偏好） */
function peekProperties(): void {
  const ui = useUiStore.getState()
  if (ui.rightOpen && ui.rightTab === 'properties') return
  useUiStore.setState(
    ui.layout !== 'wide'
      ? { rightOpen: true, rightTab: 'properties', leftOpen: false }
      : { rightOpen: true, rightTab: 'properties' },
  )
}

const objectAnchor = (id: string) => sel(`[data-object-id="${esc(id)}"]`)

/**
 * 把画布上的某个对象挪进视野。**只动视口**（与 `workspace.revealPanel` 同一条
 * 纪律）：不选中、不改文档——coachmark 指着谁不该顺手替用户选中谁。
 */
function revealObjectInViewport(panel: PanelObject | undefined): void {
  if (!panel) return
  useViewportStore.getState().revealRect({ x: panel.x, y: panel.y, w: panel.w, h: panel.h })
}

export const STEPS: readonly StepDef[] = [
  {
    id: 'welcome',
    manual: true,
    done: () => false,
    anchor: () => NONE,
  },
  {
    id: 'open_fast_edit',
    // 真实状态：那张图的图内编辑态已经进入（只有 enterElementEdit 能产生它）
    done: (ctx) => !!ctx.edit && ctx.elementPanelId === ctx.edit.panel.id,
    anchor: (ctx) => {
      if (!ctx.edit) return sel('[data-rail="assets"]')
      const ui = useUiStore.getState()
      if (ui.leftOpen && ui.leftTab === 'assets') return sel(`[data-card="${esc(ctx.edit.meta.file)}"]`)
      if (ctx.mode === 'layout') return objectAnchor(ctx.edit.panel.id)
      return sel('[data-rail="assets"]')
    },
    variant: (ctx) =>
      ctx.edit && !(useUiStore.getState().leftOpen && useUiStore.getState().leftTab === 'assets') && ctx.mode === 'layout'
        ? 'open_fast_edit.canvas'
        : 'open_fast_edit',
    reveal: (ctx) => {
      // 指着画布上的那张图时，它可能被平移到了工作区外：只动视口把它挪回来
      const ui = useUiStore.getState()
      if (ctx.mode === 'layout' && !(ui.leftOpen && ui.leftTab === 'assets')) revealObjectInViewport(ctx.edit?.panel)
    },
  },
  {
    id: 'select_text',
    done: (ctx) => {
      if (!ctx.edit || ctx.elementPanelId !== ctx.edit.panel.id) return false
      const role = primaryElementRole(ctx)
      return !!role && TEXT_ROLES.has(role) && ctx.edit.meta.editable_roles.includes(role)
    },
    anchor: (ctx) => {
      if (!ctx.edit) return NONE
      const el = preferredTextElement(ctx)
      return el
        ? { kind: 'element', panelId: ctx.edit.panel.id, bbox: el.bbox }
        : sel(`[data-element-svg="${esc(ctx.edit.panel.id)}"]`)
    },
  },
  {
    id: 'change_typography',
    // 信号：改过排版属性 **且** 一条历史真的进了撤销栈（事务结束才算）
    done: (ctx) => ctx.signals.typographyChanged > 0 && ctx.signals.historyPushed > 0,
    consumes: ['typographyChanged', 'historyPushed'],
    anchor: () => sel(`[data-prop="${esc(propertyPathOf('figureText', 'sizePt') ?? 'fontsize')}"]`),
    reveal: () => peekProperties(),
  },
  {
    id: 'locate_problem',
    done: (ctx) => ctx.signals.problemFocused > 0,
    consumes: ['problemFocused'],
    altDone: (ctx) => problemsResolved(ctx),
    anchor: (ctx) => {
      if (!ctx.problemsOpen) return sel('[data-rail="problems"]')
      const mine = tutorialIssues(ctx)
      const code = ctx.edit?.meta.spec_issue?.code
      const target = mine.find((i) => i.ruleCode === code) ?? mine[0]
      if (!target) return sel('[data-rail="problems"]')
      const obj = target.objectRef.objectId ?? ''
      return sel(`[data-issue-row][data-issue-rule="${esc(target.ruleCode)}"][data-issue-object="${esc(obj)}"]`)
    },
    variant: (ctx) => (ctx.problemsOpen ? 'locate_problem.row' : 'locate_problem'),
    reveal: (ctx) => {
      if (!ctx.problemsOpen) peekLeft('problems')
    },
  },
  {
    id: 'export_original',
    // 面板开着时确认过「原图」，然后关掉面板（关掉才能继续下一步——下一步的目标在面板后面）
    done: (ctx) => ctx.signals.exportOriginalSeen > 0 && !ctx.exportOpen,
    consumes: ['exportOriginalSeen', 'exportCanvasSeen'],
    anchor: (ctx) =>
      ctx.exportOpen ? sel('[data-onboarding-anchor="export-scope"]') : sel('[data-onboarding-anchor="export"]'),
    variant: (ctx) => (ctx.exportOpen ? 'export_original.scope' : 'export_original'),
  },
  {
    id: 'add_to_layout',
    // 两张图都在文档里且回到了版面。教程画布本来就摆好两张（ADR 0039），所以
    // 在画布模式到达这一步会直接完成；在快速编辑里则要用户按「加入画布」回去
    done: (ctx) => bothOnCanvas(ctx) && ctx.mode === 'layout',
    anchor: (ctx) => (ctx.mode === 'fast_edit' ? sel('[data-onboarding-anchor="add-to-layout"]') : NONE),
  },
  {
    id: 'multi_select_align',
    done: (ctx) => ctx.signals.alignedTutorialPanels > 0,
    consumes: ['alignedTutorialPanels'],
    anchor: (ctx) => {
      const selectedTutorial = ctx.selectionIds.filter((id) => ctx.tutorialPanelIds.has(id))
      if (selectedTutorial.length >= 2) return sel('[data-multi-selection-context-bar]')
      const target = ctx.other?.panel.id ?? ctx.edit?.panel.id
      if (!target) return NONE
      // 已经选了一张：指着**另一张**
      const first = selectedTutorial[0]
      const next = first === ctx.other?.panel.id ? ctx.edit?.panel.id : ctx.other?.panel.id
      return objectAnchor(next ?? target)
    },
    variant: (ctx) =>
      ctx.selectionIds.filter((id) => ctx.tutorialPanelIds.has(id)).length >= 2
        ? 'multi_select_align.bar'
        : 'multi_select_align',
    reveal: (ctx) => {
      const selectedTutorial = ctx.selectionIds.filter((id) => ctx.tutorialPanelIds.has(id))
      if (selectedTutorial.length >= 2) return
      const first = selectedTutorial[0]
      const next = first === ctx.other?.panel.id ? ctx.edit?.panel : ctx.other?.panel
      revealObjectInViewport(next ?? ctx.edit?.panel)
    },
  },
  {
    id: 'export_canvas',
    done: (ctx) => ctx.signals.exportCanvasSeen > 0 && !ctx.exportOpen,
    consumes: ['exportCanvasSeen', 'exportOriginalSeen'],
    anchor: (ctx) =>
      ctx.exportOpen ? sel('[data-onboarding-anchor="export-scope"]') : sel('[data-onboarding-anchor="export"]'),
    variant: (ctx) => (ctx.exportOpen ? 'export_canvas.scope' : 'export_canvas'),
  },
  {
    id: 'done',
    manual: true,
    done: () => false,
    anchor: () => NONE,
  },
]

export const stepById = (id: StepId): StepDef => STEPS.find((s) => s.id === id) ?? STEPS[0]

export const nextStepId = (id: StepId): StepId | null => {
  const i = STEP_IDS.indexOf(id)
  return i >= 0 && i + 1 < STEP_IDS.length ? STEP_IDS[i + 1] : null
}

/** 步骤表与 id 表必须一一对应（`steps.test.ts` 看护；这里只是给读者一个入口） */
export const STEP_TABLE_MATCHES_IDS = STEPS.length === STEP_IDS.length && STEPS.every((s, i) => s.id === STEP_IDS[i])
