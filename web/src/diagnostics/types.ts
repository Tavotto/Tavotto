/**
 * 诊断事件的**可辨识联合**与三个 schema 版本（ADR 0016 §3 / §20）。
 *
 * 这个文件是隐私的**类型层防线**：每种事件只声明自己合法的字段，于是
 *
 * ```ts
 * recordDiagnosticEvent({ type: 'align.commit', text: element.label })
 * //                                            ^^^^ TS 直接报错
 * ```
 *
 * 有人哪天顺手想把图内文字带进事件，编译期就红，不用等到代码评审。运行期
 * 还有 `sanitize.ts` 的字段 allowlist 兜第二层——两道防线各有判据，
 * 类型挡住「写代码时的手滑」，allowlist 挡住「类型被 as 掉之后的一切」。
 *
 * 命名一律 snake_case：这些字段直接落进 `interaction-trace.jsonl`，与
 * `report.json` 同一套读法，中间不再有一层驼峰转下划线的映射（多一层映射
 * 就多一个「新字段忘了登记」的地方）。
 */

/** 诊断包整体的 schema（老三件 + 新三件那一版是 2） */
export const BUNDLE_SCHEMA_VERSION = 2
/** frontend-state.json 的 schema */
export const SNAPSHOT_SCHEMA_VERSION = 1
/** interaction-trace.jsonl 每一行的 schema */
export const TRACE_SCHEMA_VERSION = 1

/* -------------------------------------------------------------------------- */
/*  公共零件                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 一条 patch 的**结构身份**。**永远没有 value**——value 装的就是用户的内容
 * （图内文字、颜色、数据点）。想知道「改的是什么属性」够用了；想知道
 * 「改成了什么」不在诊断的职责范围内。
 */
export interface PatchRef {
  /** 图内元素的技术 gid（axes_0.title）。画布对象类改动没有 gid，用 domain */
  gid?: string
  /** 非元素改动的归属域（panel_override / canvas_object / page …） */
  domain?: string
  /** matplotlib 属性名 / 文档字段名（fontsize、pos_frac、x） */
  prop: string
}

/** 一个几何目标：**只有数字和技术 gid**，没有 label、没有文字 */
export interface GeomRef {
  gid: string
  /** figure 分数坐标 [x, y, w, h]，y 向下 */
  bbox?: readonly number[]
  /** 锚点 [x, y] */
  anchor?: readonly number[]
}

export type SelectionKind = 'none' | 'element' | 'object' | 'mixed'
export type RenderStatusLabel = 'idle' | 'rendering' | 'ready' | 'error'
export type HistoryModeLabel = 'gesture' | 'granular'

/** 几何权威不变式的种类（ADR 0016 §6） */
export type InvariantKind =
  | 'geometry_authority_mismatch'
  | 'selected_gid_missing_from_exact_manifest'
  | 'standalone_action_inside_unrelated_transaction'
  | 'document_display_variant_diverged'
  | 'undo_complete_but_authority_stale'
  | 'preview_session_survived_commit'

/** 对齐被拒的原因。闭集——不是自由文本 */
export type AlignBlockedReason =
  | 'authority_unavailable'
  | 'authority_stale'
  | 'no_manifest'
  | 'panel_missing'

export type AlignNoopReason = 'empty_selection' | 'no_geometry_change' | 'nothing_to_write'

export type PreviewEndReason =
  | 'pointer_cancel'
  | 'committed'
  | 'authority_swapped'
  | 'authority_failed'
  | 'reset'

/* -------------------------------------------------------------------------- */
/*  事件基底                                                                    */
/* -------------------------------------------------------------------------- */

export interface DiagnosticEventBase {
  /** 单调自增，从 1 开始。环形缓冲丢掉旧事件后 seq **不重排**——
   *  两条相邻记录之间 seq 跳了号，就说明中间有事件被环挤掉了。 */
  seq: number
  /** 绝对时间（epoch ms）。导出时才转成带时区的 ISO 串 */
  ts: number
  /** 相对本次会话开始的毫秒数 */
  t_ms: number
  type: DiagnosticEventType
}

/* -------------------------------------------------------------------------- */
/*  Document / History                                                         */
/* -------------------------------------------------------------------------- */

export interface DocumentCommitEvent {
  type: 'document.commit'
  /** 历史标签的**内部 key**（`alignMode.left`），不是翻译后的文案 */
  label_key: string
  patch_count: number
  past_count: number
  future_count: number
  txn_open: boolean
  document_hash_before: string
  document_hash_after: string
  patches?: PatchRef[]
}

export interface TransactionBeginEvent {
  type: 'transaction.begin'
  label_key: string
  /** 开事务时上一个事务还开着（应当先被隐式收尾）——混合事务的线索 */
  replaced_open_txn: boolean
}

export interface TransactionEndEvent {
  type: 'transaction.end'
  label_key: string
  patch_count: number
  past_count: number
  document_hash_after: string
}

export interface TransactionCancelEvent {
  type: 'transaction.cancel'
  label_key: string
  patch_count: number
}

export interface UndoRedoRequestEvent {
  type: 'undo.request' | 'redo.request'
  past_count: number
  future_count: number
  txn_open: boolean
}

export interface UndoRedoCompleteEvent {
  type: 'undo.complete' | 'redo.complete'
  /** false = 栈空或补丁应用失败（那一条已被丢弃） */
  ok: boolean
  label_key: string
  past_count: number
  future_count: number
  document_hash_before: string
  document_hash_after: string
}

/* -------------------------------------------------------------------------- */
/*  Selection                                                                  */
/* -------------------------------------------------------------------------- */

export interface SelectionChangedEvent {
  type: 'selection.changed'
  panel: string | null
  selection_kind: SelectionKind
  selected_count: number
  /** 技术 gid（axes_0.title）。sanitize 会把不符合 gid 形状的换成 hash */
  selected_gids?: string[]
  /** 画布对象选区的条数（对象 id 不进来，只有计数） */
  object_count: number
}

/* -------------------------------------------------------------------------- */
/*  Render                                                                     */
/* -------------------------------------------------------------------------- */

export interface RenderRequestEvent {
  type: 'render.request'
  file: string
  variant: string
  policy: 'immediate' | 'defer' | 'none' | 'sync'
  /** 连续调整期间的降质预览 dpi；null = 定稿质量 */
  preview_dpi: number | null
}

export interface RenderSuccessEvent {
  type: 'render.success'
  file: string
  variant: string
  duration_ms: number
  /** manifest 摘要：只有计数与图幅尺寸，元素的 label 一个都不进来 */
  element_count: number
  size_mm?: readonly number[]
  warning_count: number
  rev: number
}

export interface RenderErrorEvent {
  type: 'render.error'
  file: string
  variant: string
  duration_ms: number
  /** 引擎给的**机器可读** code（no_worker_python / missing_dependency…），
   *  不是 traceback、不是报错原文 */
  code: string
}

export interface RenderStaleEvent {
  type: 'render.stale'
  file: string
  /** 这个文件下有几条变体被一起标脏 */
  variant_count: number
}

export interface DisplaySourceChangedEvent {
  type: 'display.source_changed'
  panel: string
  file: string
  document_variant: string
  display_variant: string | null
  authority_variant: string | null
  /** authority === document：几何写入此刻是否安全 */
  exact: boolean
  render_status: RenderStatusLabel
  stale: boolean
}

export interface AuthorityEvent {
  type: 'authority.ready' | 'authority.unavailable'
  panel: string
  document_variant: string
  authority_variant: string | null
}

/* -------------------------------------------------------------------------- */
/*  Element interaction（只记状态边界，**绝不记 mousemove**）                    */
/* -------------------------------------------------------------------------- */

export interface DragBeginEvent {
  type: 'element.drag.begin' | 'axes.drag.begin' | 'resize.begin'
  panel: string
  gid: string
  prop: string
  document_variant: string
  display_variant: string | null
  authority_variant: string | null
  exact_authority: boolean
  /** 基准锚点是从文档已有 override 取的（true），还是从 manifest 取的（false）。
   *  false + exact_authority=false = 用过期几何当基准，#131 那一族的形状 */
  anchor_from_document: boolean
}

export interface DragCommitEvent {
  type: 'element.drag.commit' | 'axes.drag.commit' | 'resize.commit'
  panel: string
  gid: string
  prop: string
  patch_count: number
  document_variant: string
  authority_variant: string | null
  exact_authority: boolean
}

export interface DragCancelEvent {
  type: 'element.drag.cancel'
  panel: string
  gid: string
  /** 被系统打断（pointercancel）还是没达到拖动阈值 */
  cancelled: boolean
}

/* -------------------------------------------------------------------------- */
/*  Alignment                                                                  */
/* -------------------------------------------------------------------------- */

/** 面板在这一刻可能已经不在文档里（被删 / 切了画布）：三个身份如实报 null */
export interface AlignRequestEvent {
  type: 'align.request'
  mode: string
  panel: string
  selected_count: number
  document_variant: string | null
  display_variant: string | null
  authority_variant: string | null
  exact_authority: boolean
  /** 只有数字与技术 gid */
  input_geometry?: GeomRef[]
}

export interface AlignBlockedEvent {
  type: 'align.blocked'
  mode: string
  panel: string
  reason: AlignBlockedReason
  document_variant: string | null
  display_variant: string | null
  authority_variant: string | null
}

export interface AlignCommitEvent {
  type: 'align.commit'
  mode: string
  panel: string
  selected_count: number
  document_variant: string | null
  display_variant: string | null
  authority_variant: string | null
  exact_authority: boolean
  input_geometry?: GeomRef[]
  output_geometry?: GeomRef[]
  patch_count: number
  move_count: number
}

export interface AlignNoopEvent {
  type: 'align.noop'
  mode: string
  panel: string
  reason: AlignNoopReason
}

/* -------------------------------------------------------------------------- */
/*  Preview                                                                    */
/* -------------------------------------------------------------------------- */

export interface PreviewBeginEvent {
  type: 'preview.begin'
  session: string
  panel: string
  render_variant: string
  history_mode: HistoryModeLabel
}

export interface PreviewCommitEvent {
  type: 'preview.commit'
  session: string
  panel: string
  render_variant: string
  await_variant: string | null
  patch_count: number
}

export interface PreviewEndEvent {
  type: 'preview.cancel' | 'preview.retire'
  session: string
  panel: string
  reason: PreviewEndReason
  /** 会话从 begin 到这一刻的毫秒数 */
  duration_ms: number
}

/* -------------------------------------------------------------------------- */
/*  Layout version                                                             */
/* -------------------------------------------------------------------------- */

export interface LayoutVersionSaveEvent {
  type: 'layout_version.save'
  /** **只有 id 的 hash**。用户输入的版本名一个字都不取 */
  version: string | null
  document_hash: string
  auto: boolean
}

export interface LayoutVersionRestoreRequestEvent {
  type: 'layout_version.restore.request'
  version: string
  document_hash: string
  past_count: number
  future_count: number
}

export interface LayoutVersionRestoreCompleteEvent {
  type: 'layout_version.restore.complete'
  version: string
  document_hash_before: string
  document_hash_after: string
  auto_backup_created: boolean
  past_count: number
  future_count: number
}

/* -------------------------------------------------------------------------- */
/*  Diagnostics 自身                                                            */
/* -------------------------------------------------------------------------- */

export interface InvariantViolationEvent {
  type: 'invariant.violation'
  kind: InvariantKind
  /** 内部操作键（align.left / scale.group / element.drag），不是文案 */
  operation: string
  panel: string | null
  document_variant: string | null
  display_variant: string | null
  authority_variant: string | null
}

export interface DiagnosticsExportEvent {
  type: 'diagnostics.export'
  trace_count: number
  panel_count: number
}

/* -------------------------------------------------------------------------- */

export type DiagnosticEvent =
  | DocumentCommitEvent
  | TransactionBeginEvent
  | TransactionEndEvent
  | TransactionCancelEvent
  | UndoRedoRequestEvent
  | UndoRedoCompleteEvent
  | SelectionChangedEvent
  | RenderRequestEvent
  | RenderSuccessEvent
  | RenderErrorEvent
  | RenderStaleEvent
  | DisplaySourceChangedEvent
  | AuthorityEvent
  | DragBeginEvent
  | DragCommitEvent
  | DragCancelEvent
  | AlignRequestEvent
  | AlignBlockedEvent
  | AlignCommitEvent
  | AlignNoopEvent
  | PreviewBeginEvent
  | PreviewCommitEvent
  | PreviewEndEvent
  | LayoutVersionSaveEvent
  | LayoutVersionRestoreRequestEvent
  | LayoutVersionRestoreCompleteEvent
  | InvariantViolationEvent
  | DiagnosticsExportEvent

export type DiagnosticEventType = DiagnosticEvent['type']

/** 落进环形缓冲与 jsonl 的形状：基底 + 该事件自己的字段 */
export type RecordedEvent = DiagnosticEventBase & Record<string, unknown>

/* -------------------------------------------------------------------------- */
/*  frontend-state.json                                                        */
/* -------------------------------------------------------------------------- */

export interface PanelSnapshot {
  panel: string
  file: string
  /** 面板载体类型；**不含面板名、文件名、stem** */
  kind: 'matplotlib' | 'image' | 'runtime' | 'unknown'
  override_count: number
  document_variant: string
  display_variant: string | null
  authority_variant: string | null
  display_exact: boolean
  exact_manifest_available: boolean
  render_status: RenderStatusLabel
  stale: boolean
  element_count: number
}

export interface FrontendDiagnosticSnapshot {
  schema_version: number
  session_ms: number
  document: {
    document_hash: string
    object_count: number
    panel_count: number
    canvas_count: number
    history: {
      past: number
      future: number
      txn_open: boolean
      txn_label_key: string | null
    }
  }
  selection: {
    active_panel: string | null
    selection_kind: SelectionKind
    element_count: number
    element_gids: string[]
    object_count: number
  }
  preview: {
    active_sessions: number
    settled: boolean | null
    history_mode: HistoryModeLabel
  }
  panels: PanelSnapshot[]
}

/** POST /api/diagnostics/bundle 的请求体 */
export interface DiagnosticPayload {
  frontend_state: FrontendDiagnosticSnapshot
  interaction_trace: RecordedEvent[]
}
