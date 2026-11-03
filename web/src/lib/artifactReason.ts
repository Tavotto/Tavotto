/**
 * PNG 采用 / 已采用面板的失败原因：**闭集与分类只有这一处**（ADR 0139 PR1）。
 *
 * 后端 409 `artifact_source_unavailable` 的 `params.reason`（`ArtifactSourceUnavailable` /
 * `ArtifactContextError` 抛出）加上前端本地发的几条，全部在 `ArtifactReason`；每一条都有
 * 一个可恢复性类别与一条文案 key。`lib/api.ts` 的 `artifactErrorMsg`、`admitPngEntry` 的失败出口、
 * 元素树空态读的是同一张表——不要在别处再写一份 `reason === '…'` 的分支。
 *
 * 叶子模块：不 import 任何 store / api，调用方把错误对象（`EngineError` / `ArtifactValidationError`
 * 都满足 `ArtifactFailureLike`）交进来。与后端 reason 全集的同源对拍：`tests/test_artifact_reason_pair.py`。
 */
export type ArtifactReason =
  // 后端发
  | 'unsupported_render_state'
  | 'source_mismatch'
  // PR2 后端新增（来源驱动采用，实验开关后面）
  | 'artist_profile'
  | 'policy_disabled'
  | 'confirmation_required'
  | 'reconstruction_changed'
  | 'adoption_store_full'
  | 'capture_incomplete'
  | 'save_ambiguous'
  | 'destination_unresolved'
  | 'validation_budget_exceeded'
  | 'layout_history_required'
  | 'source_unreadable'
  | 'source_changed'
  | 'background_visibility_required'
  | 'not_untouched'
  // 前端本地发
  | 'frame_changed'
  | 'invalid_guard'
  | 'writeback_not_supported'
  | 'unsupported_response'

/**
 * - `permanent`：这张图的这次状态进不了编辑，重试无用；
 * - `source_mismatch`：像素与原件不一致（多半是 matplotlib 版本 / 运行环境不同），重试不会变好；
 * - `retry`：可能因并发 / 环境波动而成功，才允许显示「再试一次」；
 * - `source_conflict`：已采用面板的保存数据问题，沿用原文案与规则；
 * - `user_choice`：等用户选（背景可见性），由 `BackgroundChoice` 承接；
 * - `needs_review`：来源驱动采用的确认失效（重画与确认过的那份不同）：编辑保留、不自动换基，
 *   出路是「重新对照确认」。
 */
export type ArtifactRecoverability =
  | 'permanent'
  | 'source_mismatch'
  | 'retry'
  | 'source_conflict'
  | 'user_choice'
  | 'needs_review'

export interface ArtifactFailure {
  reason: ArtifactReason | 'unknown'
  recoverability: ArtifactRecoverability
  /** `errors` 命名空间里的文案 key */
  messageKey: string
}

interface Row {
  recoverability: ArtifactRecoverability
  messageKey: string
}

const TABLE: Record<ArtifactReason, Row> = {
  unsupported_render_state: { recoverability: 'permanent', messageKey: 'artifact.unsupportedRenderState' },
  artist_profile: { recoverability: 'permanent', messageKey: 'artifact.artistProfile' },
  policy_disabled: { recoverability: 'permanent', messageKey: 'artifact.policyDisabled' },
  confirmation_required: { recoverability: 'needs_review', messageKey: 'artifact.confirmationRequired' },
  reconstruction_changed: { recoverability: 'needs_review', messageKey: 'artifact.reconstructionChanged' },
  adoption_store_full: { recoverability: 'permanent', messageKey: 'artifact.adoptionStoreFull' },
  source_mismatch: { recoverability: 'source_mismatch', messageKey: 'artifact.sourceMismatch' },
  capture_incomplete: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  save_ambiguous: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  destination_unresolved: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  validation_budget_exceeded: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  layout_history_required: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  unsupported_response: { recoverability: 'permanent', messageKey: 'artifact.unsupported' },
  writeback_not_supported: { recoverability: 'permanent', messageKey: 'artifact.writebackUnsupported' },
  not_untouched: { recoverability: 'permanent', messageKey: 'artifact.legacyEdits' },
  source_unreadable: { recoverability: 'retry', messageKey: 'artifact.unsupported' },
  source_changed: { recoverability: 'source_conflict', messageKey: 'artifact.sourceChanged' },
  frame_changed: { recoverability: 'source_conflict', messageKey: 'artifact.frameChanged' },
  invalid_guard: { recoverability: 'source_conflict', messageKey: 'artifact.frameChanged' },
  background_visibility_required: { recoverability: 'user_choice', messageKey: 'artifact.backgroundVisibilityRequired' },
}

/**
 * 严格策略被拒后，哪些 reason 可以转来源驱动（ADR 0139 §四，与后端同口径）：像素证据拿不到
 * （`source_mismatch`）与仅由白名单触发的 `artist_profile`。其余（保存不唯一、自定义绘制、来源不完整……）
 * 都是「可编辑对应证明不了」，不转。
 */
export const sourceBackedEligible = (reason: ArtifactFailure['reason']): boolean =>
  reason === 'artist_profile' || reason === 'source_mismatch'

/** 表里的全部 reason（给测试与同源对拍用；顺序无意义） */
export const ARTIFACT_REASONS = Object.keys(TABLE) as ArtifactReason[]

/** 这些类别是「明确拒绝」：不是意外故障，不该用红色错误色，也不该每次点击都重跑脚本 */
export function isDefiniteRefusal(r: ArtifactRecoverability): boolean {
  return r === 'permanent' || r === 'source_mismatch'
}

/** `EngineError` / `ArtifactValidationError` 的公共形状（鸭子类型，保持本模块是叶子） */
export interface ArtifactFailureLike {
  code?: unknown
  /** `ArtifactValidationError.reason` */
  reason?: unknown
  /** `EngineError.params.reason` */
  params?: { reason?: unknown } | null
  retryable?: unknown
}

export function classifyArtifactReason(reason: unknown, retryable = false): ArtifactFailure {
  if (typeof reason === 'string' && Object.hasOwn(TABLE, reason)) {
    const r = reason as ArtifactReason
    // 后端明说可重试（`EngineError.retryable`）的永久类 reason 升成 retry；source_mismatch / 冲突类 / 等用户选的不动
    if (retryable && TABLE[r].recoverability === 'permanent') return { reason: r, ...TABLE[r], recoverability: 'retry' }
    return { reason: r, ...TABLE[r] }
  }
  // 没见过的 reason（新后端 / 缺 params）：后端明说可重试才给「再试」，否则按永久处理（旧兜底文案）
  return {
    reason: 'unknown',
    recoverability: retryable ? 'retry' : 'permanent',
    messageKey: 'artifact.unsupported',
  }
}

export function classifyArtifactFailure(err: unknown): ArtifactFailure {
  const e = (err ?? {}) as ArtifactFailureLike
  const reason = e.reason !== undefined ? e.reason : e.params?.reason
  return classifyArtifactReason(reason, e.retryable === true)
}
