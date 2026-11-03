import { describe, expect, it } from 'vitest'
import { ApiError, EngineError, backendErrorMsg, engineErrorMsg } from './api'
import { ARTIFACT_REASONS, classifyArtifactFailure, classifyArtifactReason, isDefiniteRefusal, type ArtifactReason } from './artifactReason'
import { ArtifactValidationError } from './artifactValidation'
import { formatMessage, i18n } from '@/i18n'

const ENGINE = (reason: unknown, retryable?: boolean) =>
  new EngineError('x', '', 'artifact_source_unavailable', '', undefined, undefined, { params: { reason }, retryable })

/** reason → [可恢复性, 文案 key] 的全集（改表必须同时改这里） */
const EXPECTED: Record<ArtifactReason, [string, string]> = {
  unsupported_render_state: ['permanent', 'artifact.unsupportedRenderState'],
  artist_profile: ['permanent', 'artifact.artistProfile'],
  policy_disabled: ['permanent', 'artifact.policyDisabled'],
  confirmation_required: ['needs_review', 'artifact.confirmationRequired'],
  reconstruction_changed: ['needs_review', 'artifact.reconstructionChanged'],
  adoption_store_full: ['permanent', 'artifact.adoptionStoreFull'],
  source_mismatch: ['source_mismatch', 'artifact.sourceMismatch'],
  capture_incomplete: ['permanent', 'artifact.unsupported'],
  save_ambiguous: ['permanent', 'artifact.unsupported'],
  destination_unresolved: ['permanent', 'artifact.unsupported'],
  validation_budget_exceeded: ['permanent', 'artifact.unsupported'],
  layout_history_required: ['permanent', 'artifact.unsupported'],
  unsupported_response: ['permanent', 'artifact.unsupported'],
  writeback_not_supported: ['permanent', 'artifact.writebackUnsupported'],
  not_untouched: ['permanent', 'artifact.legacyEdits'],
  source_unreadable: ['retry', 'artifact.unsupported'],
  source_changed: ['source_conflict', 'artifact.sourceChanged'],
  frame_changed: ['source_conflict', 'artifact.frameChanged'],
  invalid_guard: ['source_conflict', 'artifact.frameChanged'],
  background_visibility_required: ['user_choice', 'artifact.backgroundVisibilityRequired'],
}

describe('artifact failure classification (single source of truth)', () => {
  it('covers exactly the closed reason set, each with its recoverability and message key', () => {
    expect([...ARTIFACT_REASONS].sort()).toEqual(Object.keys(EXPECTED).sort())
    for (const reason of ARTIFACT_REASONS) {
      const f = classifyArtifactFailure(ENGINE(reason))
      expect([reason, f.recoverability, f.messageKey]).toEqual([reason, ...EXPECTED[reason]])
      expect(classifyArtifactFailure(new ArtifactValidationError(reason))).toEqual(f)
    }
  })
  it('every message key resolves to a one-sentence text in both languages, without placeholders', () => {
    const keys = new Set(ARTIFACT_REASONS.map((r) => classifyArtifactReason(r).messageKey))
    for (const lng of ['zh-CN', 'en-US']) {
      for (const key of keys) {
        const text = String(i18n.getResource(lng, 'errors', key))
        expect(text, `${lng} ${key}`).not.toBe('undefined')
        expect(text).not.toMatch(/\{\{|文档/)
      }
    }
  })
  it('only retry-class failures may offer Try again; definite refusals are permanent or source_mismatch', () => {
    const retry = ARTIFACT_REASONS.filter((r) => classifyArtifactReason(r).recoverability === 'retry')
    expect(retry).toEqual(['source_unreadable'])
    for (const r of ARTIFACT_REASONS) {
      const k = classifyArtifactReason(r).recoverability
      expect(isDefiniteRefusal(k)).toBe(k === 'permanent' || k === 'source_mismatch')
      // 确认失效不是「被拒绝」：不记忆、不当永久失败，出路是重新对照确认
      if (k === 'needs_review') expect(['confirmation_required', 'reconstruction_changed']).toContain(r)
    }
  })
  it('a backend retryable flag upgrades only permanent reasons, and unknown reasons fall back to the old wording', () => {
    expect(classifyArtifactFailure(ENGINE('unsupported_render_state', true)).recoverability).toBe('retry')
    expect(classifyArtifactFailure(ENGINE('source_mismatch', true)).recoverability).toBe('source_mismatch')
    expect(classifyArtifactFailure(ENGINE('source_changed', true)).recoverability).toBe('source_conflict')
    expect(classifyArtifactFailure(ENGINE('brand_new_reason'))).toEqual({
      reason: 'unknown', recoverability: 'permanent', messageKey: 'artifact.unsupported',
    })
    expect(classifyArtifactFailure(ENGINE(undefined, true)).recoverability).toBe('retry')
    expect(classifyArtifactFailure(new Error('x')).recoverability).toBe('permanent')
  })
  it('the API message translators read the same table, including the new sentences', () => {
    for (const [reason, [, key]] of Object.entries(EXPECTED)) {
      const params = { reason }
      expect(engineErrorMsg(ENGINE(reason)).key).toBe(key)
      expect(backendErrorMsg(new ApiError('x', 409, { code: 'artifact_source_unavailable', params })).key).toBe(key)
    }
    expect(formatMessage(engineErrorMsg(ENGINE('artist_profile')))).toMatch(/图例|legend/)
    expect(formatMessage(engineErrorMsg(ENGINE('unsupported_render_state')))).not.toMatch(/图例|legend/)
    expect(formatMessage(engineErrorMsg(ENGINE('source_mismatch')))).toMatch(/matplotlib/)
  })
})
