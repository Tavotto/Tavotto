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
 * ### 前置状态先验，缺了就给一条真实行动（审计 T36）
 *
 * 用户跳过前面的步骤是常事。第 2 步的目标是图内的标题，用户还在画布模式里时它
 * **根本不在 DOM 里**——以前的表现是卡片一直「正在等待目标出现…」。现在每步先
 * `precondition(ctx)`：不满足就说清缺什么，并给一颗只调稳定动作的按钮
 * （`openFastEdit` / `addFigureToLayout` / `returnToLayout` / `setSelectedGid`），
 * 「跳过此步」照旧。「等待」只允许出现在前置满足、目标正在渲染 / 重排的短暂窗口。
 *
 * ### 不做的事
 *
 * 这里**一个字都不写文档**、不发请求、不改用户偏好；`reveal()` 只做「把折叠的
 * 侧栏临时露出来」这一件事，且不经过 uiStore 的 persist。
 */
import type { ManifestElement, TutorialMetadata, TutorialPanelMeta } from '@/lib/api'
import { propertyPathOf } from '@/lib/typography'
import type { ValidationIssue } from '@/lib/validation'
import { useOnboardingStore } from '@/store/onboardingStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useViewportStore } from '@/store/viewportStore'
import {
  addFigureToLayout,
  findFigurePanel,
  openFastEdit,
  returnToLayout,
  useWorkspaceStore,
  type WorkspaceMode,
} from '@/store/workspace'
import type { PanelObject } from '@/types/document'
import { STEP_IDS, tallyOutcomes, type StepId, type StepOutcomes } from './stepIds'

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
  /** 这一轮到目前为止「完成 / 跳过」各几步（结束页按它措辞） */
  outcomes: StepOutcomes
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
  const ob = useOnboardingStore.getState()
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
    outcomes: tallyOutcomes(ob.completedSteps, ob.skippedSteps),
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

/**
 * 元数据里有、文档里却没有的教程图（用户删掉了、或这份画布本来就只放了一张）。
 * 第 6 / 7 步按它说话：**说「两张图都在画布上」之前先数一数**。
 */
export function missingTutorialPanels(ctx: StepContext): TutorialPanelMeta[] {
  if (!ctx.meta) return []
  return ctx.meta.panels.filter((pm) => !findFigurePanel(pm.file))
}

/* -------------------------------- 步骤表 ---------------------------------- */

/**
 * coachmark 上的一颗真实行动按钮。`run` **只调稳定动作**（工作流出口 / store 的
 * 既有 action），不在这里拼第二份逻辑；文案 key 在 `dialogs:onboarding.action.<key>`。
 */
export interface StepAction {
  key: string
  values?: Record<string, unknown>
  run: () => void
}

/**
 * 前置状态的结论。不满足时 `reason` 是 `dialogs:onboarding.precondition.<reason>`
 * 的 key（说清缺什么），`action` 是补上它的那一颗按钮，`anchor` 是此刻该指着谁
 * （不给就居中）。**元数据还没到时一律算满足**——那是层里「等待」的合法窗口。
 */
export type Precondition =
  | { ok: true }
  | {
      ok: false
      reason: string
      values?: Record<string, unknown>
      action?: StepAction
      anchor?: AnchorSpec
    }

const OK: Precondition = { ok: true }

export interface StepDef {
  id: StepId
  /** 要用户点「开始 / 完成」的步骤（没有可自动识别的动作） */
  manual?: boolean
  /** 完成条件（manual 的步骤不会被自动判完） */
  done: (ctx: StepContext) => boolean
  /** 这一步能做的前提。不满足时卡片说原因 + 给行动，不去找锚点、不「等待」 */
  precondition?: (ctx: StepContext) => Precondition
  /** 前置满足时仍可提供的主动作（比如替用户把缺的那张图加进画布） */
  action?: (ctx: StepContext) => StepAction | null
  /** 文案插值（`{{name}}` / 结束页的计数） */
  values?: (ctx: StepContext) => Record<string, unknown>
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

/** 素材抽屉开着就指卡片，否则指左轨「素材」 */
function assetAnchor(file: string): AnchorSpec {
  const ui = useUiStore.getState()
  return ui.leftOpen && ui.leftTab === 'assets' ? sel(`[data-card="${esc(file)}"]`) : sel('[data-rail="assets"]')
}

/** 「打开要编辑的那张图」该指着谁（Step 1 与各步前置共用） */
function openFastEditAnchor(ctx: StepContext): AnchorSpec {
  if (!ctx.edit) return ctx.meta ? assetAnchor(editPanelMeta(ctx.meta)?.file ?? '') : sel('[data-rail="assets"]')
  const ui = useUiStore.getState()
  if (ui.leftOpen && ui.leftTab === 'assets') return sel(`[data-card="${esc(ctx.edit.meta.file)}"]`)
  if (ctx.mode === 'layout') return objectAnchor(ctx.edit.panel.id)
  return sel('[data-rail="assets"]')
}

/** 把要编辑的那张图打开进图内编辑——与素材卡双击、`tavotto open` 同一个出口 */
function openEditAction(meta: TutorialMetadata): StepAction | undefined {
  const pm = editPanelMeta(meta)
  if (!pm) return undefined
  return { key: 'openFigure', values: { name: pm.stem }, run: () => void openFastEdit(pm.file) }
}

/** 把缺的那张图加进画布——与快速编辑条的「添加到画布」同一个出口 */
function addMissingAction(missing: TutorialPanelMeta): StepAction {
  return { key: 'addToLayout', values: { name: missing.stem }, run: () => void addFigureToLayout(missing.file) }
}

/**
 * 第 2–4 步的共同前提：要编辑的那张图在文档里、并且此刻正处在它的图内编辑态。
 * 用户跳过第 1 步直接到这里时，目标（图内的标题 / 属性字段）根本不在 DOM 里；
 * 与其「等待」，不如说清并给一颗「打开 Fig2_correlation」。
 */
function requireElementEdit(ctx: StepContext): Precondition {
  if (!ctx.meta) return OK
  const pm = editPanelMeta(ctx.meta)
  if (!pm) return OK
  if (!ctx.edit) {
    return {
      ok: false,
      reason: 'editPanelMissing',
      values: { name: pm.stem },
      action: openEditAction(ctx.meta),
      anchor: assetAnchor(pm.file),
    }
  }
  if (ctx.elementPanelId !== ctx.edit.panel.id) {
    return {
      ok: false,
      reason: 'notInElementEdit',
      values: { name: pm.stem },
      action: openEditAction(ctx.meta),
      anchor: openFastEditAnchor(ctx),
    }
  }
  return OK
}

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
    anchor: openFastEditAnchor,
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
    precondition: requireElementEdit,
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
    // 字号那一行只在选中了文字元素时才在属性页里：没选中就不是「等」，是先选一个
    precondition: (ctx) => {
      const edit = requireElementEdit(ctx)
      if (!edit.ok || !ctx.edit) return edit
      const role = primaryElementRole(ctx)
      if (role && TEXT_ROLES.has(role)) return OK
      const el = preferredTextElement(ctx)
      return {
        ok: false,
        reason: 'noTextSelected',
        action: el ? { key: 'selectText', run: () => useUiStore.getState().setSelectedGid(el.gid) } : undefined,
        anchor: el ? { kind: 'element', panelId: ctx.edit.panel.id, bbox: el.bbox } : undefined,
      }
    },
    // 信号：改过排版属性 **且** 一条历史真的进了撤销栈（事务结束才算）
    done: (ctx) => ctx.signals.typographyChanged > 0 && ctx.signals.historyPushed > 0,
    consumes: ['typographyChanged', 'historyPushed'],
    anchor: () => sel(`[data-prop="${esc(propertyPathOf('figureText', 'sizePt') ?? 'fontsize')}"]`),
    reveal: () => peekProperties(),
  },
  {
    id: 'locate_problem',
    // 图内问题从渲染后的 manifest 算：那张图没渲染过（跳过了前面几步）问题面板里
    // 就不会有它的问题，这一步无从完成——先把它打开一次
    precondition: (ctx) => {
      if (!ctx.meta) return OK
      const pm = editPanelMeta(ctx.meta)
      if (!pm) return OK
      if (!ctx.edit) {
        return {
          ok: false,
          reason: 'editPanelMissing',
          values: { name: pm.stem },
          action: openEditAction(ctx.meta),
          anchor: assetAnchor(pm.file),
        }
      }
      if (!ctx.elements) {
        return {
          ok: false,
          reason: 'editPanelNotRendered',
          values: { name: pm.stem },
          action: openEditAction(ctx.meta),
          anchor: openFastEditAnchor(ctx),
        }
      }
      return OK
    },
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
    // 在画布模式到达这一步会直接完成；在快速编辑里则要用户按「加入画布」回去。
    // 文档里只剩一张时（用户删过 / 另存的画布）说的是**另一件事**：把缺的那张
    // 加进来——文案、锚点、按钮都按 `missingTutorialPanels` 现算，不许说
    // 「两张都已经在画布上」
    done: (ctx) => bothOnCanvas(ctx) && ctx.mode === 'layout',
    variant: (ctx) => (missingTutorialPanels(ctx).length ? 'add_to_layout.missing' : 'add_to_layout'),
    values: (ctx) => ({ name: missingTutorialPanels(ctx)[0]?.stem ?? '' }),
    action: (ctx) => {
      const missing = missingTutorialPanels(ctx)[0]
      return missing ? addMissingAction(missing) : null
    },
    anchor: (ctx) => {
      const missing = missingTutorialPanels(ctx)[0]
      if (missing) return assetAnchor(missing.file)
      return ctx.mode === 'fast_edit' ? sel('[data-onboarding-anchor="add-to-layout"]') : NONE
    },
    reveal: (ctx) => {
      if (missingTutorialPanels(ctx).length) peekLeft('assets')
    },
  },
  {
    id: 'multi_select_align',
    // 要两张图都在画布上、且人在版面里：快速编辑这一屏上没有第二张图可点
    precondition: (ctx) => {
      if (!ctx.meta) return OK
      const missing = missingTutorialPanels(ctx)[0]
      if (missing) {
        return {
          ok: false,
          reason: 'otherPanelMissing',
          values: { name: missing.stem },
          action: addMissingAction(missing),
          anchor: assetAnchor(missing.file),
        }
      }
      if (ctx.mode !== 'layout') {
        return {
          ok: false,
          reason: 'notInLayout',
          action: { key: 'returnToLayout', run: () => returnToLayout() },
          anchor: sel('[data-onboarding-anchor="to-layout"]'),
        }
      }
      return OK
    },
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
    // 结束页按实际的账说话：全做完才用「完成」；跳过了几步就说跳过了几步；
    // 一步没做的不许说「你已经走过……」
    variant: (ctx) =>
      ctx.outcomes.skipped === 0 ? 'done' : ctx.outcomes.done === 0 ? 'done.allSkipped' : 'done.partial',
    values: (ctx) => ({ ...ctx.outcomes }),
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
