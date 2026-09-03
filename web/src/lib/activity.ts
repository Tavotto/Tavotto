import type { AlignMode } from './geometry'

/**
 * 本地**活动信号**：某个真实的用户动作刚刚完成。
 *
 * 给谁用：新手教程（`lib/onboarding/flow.ts`）与一次性情境提示要知道「用户
 * 已经自己做过这件事了」——它们订阅这里，而不是 import 进 `store/actions`。
 * 反过来也一样：核心 action 只发一声，不 import 任何 onboarding / 提示模块。
 *
 * 它**不是遥测**：只在本进程的 `window` 上派发、不落盘、不出网、不带任何用户
 * 内容——detail 里只有闭集枚举与计数：没有对象 id、没有 gid、没有文字、没有
 * 文件名、没有路径。「是哪一个对象」由订阅方在收到信号那一刻自己去问 store
 * （选区、文档、manifest），信号只说「刚发生了哪一类事」。
 *
 * 三条纪律（`test/activity.test.ts` 看护 kind 闭集与 payload 形状）：
 *   ① **只在动作真的成功之后发**——失败 / 空操作不发（对齐全锁定、加入画布找
 *      不到素材、定位失败都要带 `ok:false` 或干脆不发）；
 *   ② 同一个动作只在一处发（一个 action 一个发射点，不在组件里再补一枪）；
 *   ③ 发送失败被吞掉：一条提示信号绝不能让业务动作失败。
 *
 * Prompt 22 的遥测映射只许从 `ACTIVITY_KINDS` 这张闭表挑粗粒度事件，且必须
 * 经过同意态与后端白名单——这里本身一个字节都不出网。
 */
export const ACTIVITY_EVENT = 'tavotto:activity'

export type ActivityDetail =
  /* ---------- Session 17 起就有的三条 ---------- */
  | { kind: 'selection.aligned'; mode: AlignMode; ref: 'selection' | 'page' | 'primary'; count: number }
  | { kind: 'selection.grouped'; count: number }
  | { kind: 'selection.ungrouped'; count: number }
  /* ---------- Session 21：教程与提示要听的那些 ---------- */
  /** 项目打开 / 切换成功（`projectStore.adoptOpenedProject`） */
  | { kind: 'project.opened'; tutorial: boolean }
  /** 工作区在快速编辑 / 画布排版之间切换 */
  | { kind: 'workspace.mode_changed'; mode: 'fast_edit' | 'layout' }
  /** `openFastEdit()` 真的把一张图开进了快速编辑（`layout_only` 也算打开，只是进不了图内） */
  | { kind: 'figure.opened_fast_edit'; outcome: 'editing' | 'layout_only' }
  /** 进入图内元素编辑态（快速编辑与画布排版两条路都经过它；此刻在哪条工作流问 workspace store） */
  | { kind: 'figure.element_edit_entered' }
  /** 画布对象选区变了；`count` 是选中数量 */
  | { kind: 'selection.changed'; count: number }
  /** 图内元素选区变了；`count` 是选中元素数量 */
  | { kind: 'element.selection_changed'; count: number }
  /** 一条图内元素 override 落进了文档（`prop` 是 matplotlib 属性名，开集但不是用户内容） */
  | { kind: 'element.property_changed'; prop: string }
  /** 一条历史记录被推进撤销栈（commit / endTxn 汇到的那一处）；`label` 是开发者写死的历史 key */
  | { kind: 'history.pushed'; label: string }
  /** 左侧「问题」抽屉被打开 */
  | { kind: 'problems.opened' }
  /** 一次定位（`issueFocus.focusObject`）的结果 */
  | { kind: 'problem.focused'; ok: boolean; mode?: 'layout' | 'fast_edit'; field?: 'none' | 'focused' | 'requested' }
  /** 导出面板打开 */
  | { kind: 'export.dialog_opened' }
  /** 导出面板里的输出范围（打开时的初值也发一次） */
  | { kind: 'export.scope_changed'; scope: 'original' | 'canvas' }
  /** 「加入画布」（`addFigureToLayout`）；`focused` = 本来就在画布上 */
  | { kind: 'figure.added_to_layout'; outcome: 'added' | 'focused' }
  /** 画布对象右键菜单打开；`menu` 是菜单种类 */
  | { kind: 'menu.opened'; menu: 'panel' | 'panel-layout-only' | 'text' | 'mark' | 'multi' }
  /** 文档写盘成功 */
  | { kind: 'document.saved' }

export type ActivityKind = ActivityDetail['kind']

/**
 * kind 的闭集。**新增一条 kind 必须同时进这张表**——`activity.test.ts` 用它
 * 反证 payload 里没有用户内容，Prompt 22 的遥测映射也只许从这里挑。
 */
export const ACTIVITY_KINDS: readonly ActivityKind[] = [
  'selection.aligned',
  'selection.grouped',
  'selection.ungrouped',
  'project.opened',
  'workspace.mode_changed',
  'figure.opened_fast_edit',
  'figure.element_edit_entered',
  'selection.changed',
  'element.selection_changed',
  'element.property_changed',
  'history.pushed',
  'problems.opened',
  'problem.focused',
  'export.dialog_opened',
  'export.scope_changed',
  'figure.added_to_layout',
  'menu.opened',
  'document.saved',
]

/**
 * payload 里允许出现的键（kind 之外）。**没有 id / gid / name / path / text**
 * ——这张表是隐私边界的结构性防线：新增字段要先进这里，进不了就说明它不该发。
 */
export const ACTIVITY_PAYLOAD_KEYS: ReadonlySet<string> = new Set([
  'kind',
  'mode',
  'ref',
  'count',
  'tutorial',
  'outcome',
  'prop',
  'label',
  'ok',
  'field',
  'scope',
  'menu',
])

export function emitActivity(detail: ActivityDetail): void {
  try {
    if (typeof window === 'undefined') return
    window.dispatchEvent(new CustomEvent<ActivityDetail>(ACTIVITY_EVENT, { detail }))
  } catch {
    /* 本地信号失败不影响业务动作 */
  }
}

/** 订阅活动信号；返回取消订阅 */
export function onActivity(listener: (detail: ActivityDetail) => void): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent<ActivityDetail>).detail
    if (detail && typeof detail.kind === 'string') listener(detail)
  }
  window.addEventListener(ACTIVITY_EVENT, handler)
  return () => window.removeEventListener(ACTIVITY_EVENT, handler)
}
