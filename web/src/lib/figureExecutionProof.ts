import type { FigureExecutionOwner } from '@/types/document'

/**
 * Positive evidence that one specific render produced a figure.
 *
 * This is deliberately **not** a source of truth and is never persisted. It is
 * a short-lived, per-render fact used for exactly one decision: may the first
 * edit freeze the pre-edit snapshot as a historical input? It is keyed by the
 * project, source id and the exact ordered override list, so a later render of
 * the same name can never sign an older snapshot.
 */
export interface FigureExecutionProof {
  version: 1
  sourceId: string
  receiptId: string
  executionOwner: FigureExecutionOwner
  patchHash: string
}

interface StoredProof {
  proof: FigureExecutionProof
  overrides: string
}

/** Bounded: proofs only matter until the next edit; older renders are noise. */
const MAX_PROOFS = 64
const proofs = new Map<string, StoredProof>()

function proofKey(pj: string | null, fileId: string, overrides: readonly unknown[]): string {
  return JSON.stringify([pj, fileId, overrides])
}

function isOwner(value: unknown): value is FigureExecutionOwner {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const owner = value as Record<string, unknown>
  return typeof owner.script === 'string' && owner.script.length > 0 &&
    (owner.run_config === null || (typeof owner.run_config === 'string' && owner.run_config.length > 0))
}

function parseProof(value: unknown): FigureExecutionProof | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const fact = value as Record<string, unknown>
  const nonempty = (item: unknown): item is string => typeof item === 'string' && item.length > 0
  if (fact.version !== 1 || !nonempty(fact.sourceId) || !nonempty(fact.receiptId) ||
      !nonempty(fact.patchHash) || !isOwner(fact.executionOwner)) return undefined
  return {
    version: 1,
    sourceId: fact.sourceId,
    receiptId: fact.receiptId,
    executionOwner: { script: fact.executionOwner.script, run_config: fact.executionOwner.run_config },
    patchHash: fact.patchHash,
  }
}

export function recordFigureExecutionProof(
  value: unknown,
  context: { pj: string | null; fileId: string; overrides: readonly unknown[] },
): void {
  const proof = parseProof(value)
  if (proof === undefined || proof.sourceId !== context.fileId) return
  const key = proofKey(context.pj, context.fileId, context.overrides)
  proofs.delete(key)
  proofs.set(key, { proof, overrides: JSON.stringify(context.overrides) })
  while (proofs.size > MAX_PROOFS) {
    const oldest = proofs.keys().next().value
    if (oldest === undefined) break
    proofs.delete(oldest)
  }
}

export function figureExecutionProofFor(
  pj: string | null,
  fileId: string,
  overrides: readonly unknown[],
): FigureExecutionProof | undefined {
  const entry = proofs.get(proofKey(pj, fileId, overrides))
  if (entry === undefined || entry.overrides !== JSON.stringify(overrides)) return undefined
  return entry.proof
}

export function clearFigureExecutionProofs(): void {
  proofs.clear()
}
