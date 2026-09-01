/**
 * 真实定位（ADR 0030）。**跨模块唯一的一个 focus 动作**。
 *
 * ```text
 * issue → 画布 → 工作流模式 → 对象 → 视口 → 选中 → Inspector → 属性字段
 * ```
 *
 * 问题面板、就绪度、QuickEdit、onboarding 与将来的搜索都调这一个；
 * 各写一遍的后果是同一句「定位」在不同入口跳到不同的地方，而且其中几处
 * 会在对象已删、模式不对、画布不是当前这张时**静默什么都不做**。
 *
 * ### 定位失败必须有反馈
 *
 * 返回结构化原因，绝不 `return` 一个 `false` 让调用方自己编话。四种成因各有
 * 各的下一步：对象已删（刷新问题）、画布没了（回项目）、图内编辑进不去
 * （连接源脚本 / 看项目状态）、文档还没载入（等一下再点）。
 *
 * ### 不做的事
 *
 * 定位**一个字都不写文档**：不建对象、不改属性、不置 dirty、不进撤销历史。
 * 视口、选中、模式、面板开合全是会话状态（`UX_CONTRACTS.md` §3）。
 */
import { msg } from '@/i18n'
import { enterElementEdit } from '@/store/actions'
import { activateCanvas } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import type { Severity } from './profile'
import type { ObjectRef, ValidationIssue } from './validation'
import type { CanvasObject } from '@/types/document'

/** 定位失败的成因。**闭集**——界面按它查下一步的说法，不按自由文本。 */
export type FocusFailure =
  | 'canvas_missing'
  | 'object_deleted'
  | 'not_editable'
  | 'document_not_loaded'

/**
 * 属性字段这一步的结果。**三档，不是一个 boolean**：
 *
 *   none      —— 这条问题说不出是哪个字段（页宽、重叠这类）
 *   focused   —— 锚点当场就在，焦点已经落上去了
 *   requested —— 锚点还没进 DOM（属性页正在重排），已排在下一帧再试一次
 *
 * 旧实现回的是 `focusedField: boolean`，而那个 boolean **恒为 true**——只要
 * `propertyPath` 非空就回 true，真正的查找排在 rAF 里、结果没人看得见。
 * 注释还写着「找不到就如实回 false」。判据修对了不等于它说的话对
 * （`docs/adr/0030`）；这里把说法改成它做得到的那一句。
 */
export type FieldFocus = 'none' | 'focused' | 'requested'

export type FocusOutcome =
  | { ok: true; mode: 'layout' | 'fast_edit'; field: FieldFocus }
  | { ok: false; reason: FocusFailure }

/** 高亮持续多久（ms）。够看见，短到不碍事。 */
export const HIGHLIGHT_MS = 1400

let clearTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 定位到一个对象引用。
 *
 * `gid` 非空 = 目标在某张图**里面**：先进快速编辑（那一屏就是为看一张图设计
 * 的），再进图内元素编辑，再选中那个元素。没有源脚本进不去图内编辑——那不是
 * 崩溃，是一个说得出原因的失败（`not_editable`）。
 */
export function focusObject(ref: ObjectRef, propertyPath?: string | null): FocusOutcome {
  const s0 = useDocumentStore.getState()
  if (!s0.documentId) return { ok: false, reason: 'document_not_loaded' }
  // **多项目隔离**：对象 id 在两个项目里可以相同（都是 `o_…` 形状）。不比一次
  // documentId 的话，一条上一份项目残留的问题会照着 id 在**这个**项目里选中
  // 一个毫不相干的对象——而界面看起来一切正常。
  if (ref.documentId && ref.documentId !== s0.documentId) {
    return { ok: false, reason: 'document_not_loaded' }
  }
  if (ref.canvasId && ref.canvasId !== s0.activeCanvasId) {
    // **只判一次：切完之后到了没有。** 曾经这里先查一遍"这张画布还在不在"、
    // 切完再查一遍"到了没有"，两句话说的是同一件事——于是把前一句改成恒真
    // 也没有任何用例会红（变异反证里它活了下来）。`activateCanvas` 对不存在
    // 的 id 本来就是 no-op（`switchCanvas` 自己查成员），所以合成一处不放松
    // 任何东西。**冗余的保证杀不死，处置是合并，不是造个输入去覆盖它。**
    activateCanvas(ref.canvasId)
    if (useDocumentStore.getState().activeCanvasId !== ref.canvasId) {
      return { ok: false, reason: 'canvas_missing' }
    }
  }
  const ui = useUiStore.getState()
  if (!ref.objectId) {
    // 页面级问题（页宽、比例）：没有对象可选，把属性页切到「画布」那一栏
    ui.setRightTab('canvas')
    useWorkspaceStore.getState().exitToLayout()
    return { ok: true, mode: 'layout', field: 'none' }
  }
  const obj = useDocumentStore.getState().doc.objects.find((o) => o.id === ref.objectId)
  if (!obj) return { ok: false, reason: 'object_deleted' }

  if (ref.gid) {
    if (obj.type !== 'panel' || !obj.script) return { ok: false, reason: 'not_editable' }
    // 图内元素：快速编辑那一屏才是"看一张图"的工作流（ADR 0028）
    useWorkspaceStore.getState().enterFastEdit(obj.id)
    ui.setTool('select')
    useSelectionStore.getState().set([obj.id])
    reveal(obj)
    enterElementEdit(obj.id)
    useUiStore.getState().setSelectedGid(ref.gid)
    useUiStore.getState().setRightTab('properties')
    flash(ref)
    return { ok: true, mode: 'fast_edit', field: focusField(propertyPath) }
  }

  // 画布对象：排版模式（越界、重叠、页边距这些只在版面上说得清）
  useWorkspaceStore.getState().exitToLayout()
  ui.setElementPanel(null)
  ui.setTool('select')
  useSelectionStore.getState().set([obj.id])
  reveal(obj)
  useUiStore.getState().setRightTab('properties')
  flash(ref)
  return { ok: true, mode: 'layout', field: focusField(propertyPath) }
}

/** 定位到一条问题（面板整行点击与「定位」按钮共用）。 */
export function focusIssue(issue: ValidationIssue): FocusOutcome {
  return focusObject(issue.objectRef, issue.propertyPath)
}

/** 只动视口，不动文档。 */
function reveal(o: CanvasObject): void {
  useViewportStore.getState().revealRect({ x: o.x, y: o.y, w: o.w, h: o.h })
}

/** 短暂高亮；到点自己撤掉。连着定位同一个对象两次会重新播一遍（token 变了）。 */
function flash(ref: ObjectRef): void {
  useUiStore.getState().setIssueHighlight({ objectId: ref.objectId, gid: ref.gid })
  if (clearTimer) clearTimeout(clearTimer)
  clearTimer = setTimeout(() => {
    clearTimer = null
    useUiStore.getState().setIssueHighlight(null)
  }, HIGHLIGHT_MS)
}

/**
 * 把焦点落到属性页里对应的那个字段上。
 *
 * 选择器用 `data-prop`（属性名是稳定的机器标识），**不用 aria-label**
 * ——那是本地化文案，换个语言就选不中了（`focusRescue.ts` 踩过同一个坑）。
 * 锚点由属性能力层统一挂（`lib/typography.propertyPathOf()` →
 * `controls/TypographyControls` 的 `Anchor`），报字段名的和挂锚点的读同一张表。
 *
 * 当场找得到就当场聚焦并回 `focused`；找不到不是失败——切模式 / 进图内编辑
 * 会换掉整棵属性页，DOM 这时候还没重排完，所以再排一帧重试，回 `requested`。
 * **回 `requested` 不等于成功**：调用方不许拿它说「已定位到字段」。
 */
function focusField(propertyPath?: string | null): FieldFocus {
  if (!propertyPath || typeof document === 'undefined') return 'none'
  const run = (): boolean => {
    const host = document.querySelector<HTMLElement>(`[data-prop="${cssEscape(propertyPath)}"]`)
    if (!host) return false
    const control = host.querySelector<HTMLElement>('input, select, textarea, button')
    const target = control ?? host
    // 两个方法都可选调用：jsdom 没有 scrollIntoView，而"定位"这一步失手
    // 不该把整条动作炸掉——用户要的是焦点落到字段上，滚动只是顺带
    target.scrollIntoView?.({ block: 'nearest' })
    target.focus?.()
    return true
  }
  if (run()) return 'focused'
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run)
  return 'requested'
}

/** 属性名里只可能出现 `[A-Za-z0-9_.]`，但选择器仍然要转义（`.` 是类选择器）。 */
const cssEscape = (v: string): string => v.replace(/[^A-Za-z0-9_-]/g, (c) => `\\${c}`)

/**
 * 打开左侧「问题」面板（可带筛选）。**Prompt 12 的导出面板用它把用户
 * 交回问题清单**，而不是在弹窗里再列一遍。
 */
export function openProblems(filter?: { severities?: Severity[] }): void {
  useUiStore.getState().setProblemFilter(filter?.severities ?? null)
  useUiStore.getState().setLeftTab('problems')
}

/** 定位失败时说什么。四个成因各有各的下一步，不共用一句"定位失败"。 */
export const focusFailureMessage = (reason: FocusFailure) =>
  msg(`problems.focusFailed.${reason}`, undefined, 'workspace')
