/** Read one v2 source page. It never changes a publication profile. */
export type FigureKind = 'unknown' | 'line_art' | 'halftone' | 'combination'
export type SubmissionStage = 'unknown' | 'submission' | 'revision' | 'accepted' | 'production'
export interface FactSource { url: string; retrieved: string; kind?: 'html' | 'pdf'; page?: number }
export interface FactRecord {
  id: string; subject: string; relation: string; value: unknown; unit?: string
  strength: string; stage: string; origin: string; source: string; quote: string
  conditions?: Record<string, unknown>; condition_text?: string; derivation?: string; anchor?: string
  context?: { source: string; quote: string }[]
}
export interface CoverageRecord { id: string; subject: string; result: 'not_stated'; scope: string; sources: string[] }
export interface FactsPage {
  journal_id: string; journal: { label: string; publisher_id: string; issn?: string; eissn?: string }; catalog_rev: number; next_offset: number | null
  sync_state: string; sync_code: string | null; facts: FactRecord[]; coverage: CoverageRecord[]
  sources: Record<string, FactSource>; catalog_completeness: 'unknown'; semantic_review: 'not_authenticated'
  inspection?: { status: string; facts: { id: string; structured_conditions: { status: string } }[] }
}
export type FactSelection = { object_label: string; figure_kind: FigureKind; stage: SubmissionStage }
export type FactsResponse = { ok: true; page: FactsPage } | { ok: false; code: string }

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validAnchor(value: unknown): boolean {
  const prefix = '#:~:text='
  if (typeof value !== 'string' || !value.startsWith(prefix) || [...value].length > 4096) return false
  const text = value.slice(prefix.length)
  return !!text && [...text].every((char) => {
    const code = char.codePointAt(0)!
    return code > 32 && code !== 127 && !(code >= 0xd800 && code <= 0xdfff)
  })
}

function validPageContents(page: FactsPage): boolean {
  if (!record(page.sources)) return false
  for (const source of Object.values(page.sources)) {
    if (!record(source) || typeof source.url !== 'string' || typeof source.retrieved !== 'string') return false
    if (source.kind !== undefined && source.kind !== 'html' && source.kind !== 'pdf') return false
    if (source.page !== undefined && (source.kind !== 'pdf' || typeof source.page !== 'number' || !Number.isSafeInteger(source.page) || source.page <= 0)) return false
  }
  for (const fact of page.facts) {
    if (!record(fact) || ['id', 'subject', 'relation', 'strength', 'stage', 'origin', 'source', 'quote'].some((key) => typeof fact[key] !== 'string') || !(fact.source in page.sources)) return false
    if (fact.context !== undefined && (!Array.isArray(fact.context) || fact.context.some((ctx) => !record(ctx) || typeof ctx.source !== 'string' || typeof ctx.quote !== 'string' || !(ctx.source in page.sources)))) return false
    if (fact.conditions !== undefined && !record(fact.conditions)) return false
    if (fact.condition_text !== undefined && typeof fact.condition_text !== 'string') return false
    if (fact.derivation !== undefined && typeof fact.derivation !== 'string') return false
    if (fact.anchor !== undefined && !validAnchor(fact.anchor)) return false
    if (fact.unit !== undefined && typeof fact.unit !== 'string') return false
  }
  for (const coverage of page.coverage) {
    if (!record(coverage) || typeof coverage.id !== 'string' || typeof coverage.subject !== 'string' || typeof coverage.scope !== 'string' || coverage.result !== 'not_stated' || !Array.isArray(coverage.sources) || coverage.sources.some((id) => typeof id !== 'string' || !(id in page.sources))) return false
  }
  if (page.inspection && (!record(page.inspection) || !Array.isArray(page.inspection.facts) || page.inspection.facts.some((item) => !record(item) || typeof item.id !== 'string' || !record(item.structured_conditions) || typeof item.structured_conditions.status !== 'string'))) return false
  return true
}

export async function readJournalFacts(
  journalId: string, offset: number, revision: number | null, selection: FactSelection | null, signal: AbortSignal,
): Promise<FactsResponse> {
  const body = {
    journal_id: journalId, offset, limit: 1,
    ...(revision !== null ? { expected_catalog_rev: revision } : {}),
    ...(selection ? { inspection: selection } : {}),
  }
  try {
    const response = await fetch('/api/journal-facts/journal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    })
    const data: unknown = await response.json()
    if (!response.ok) {
      const error = data as { code?: unknown } | null
      return { ok: false, code: typeof error?.code === 'string' ? error.code : 'unknown' }
    }
    const page = data as FactsPage | null
    if (!page || page.journal_id !== journalId || !page.journal || typeof page.journal.label !== 'string' || typeof page.journal.publisher_id !== 'string' ||
      (page.journal.issn !== undefined && typeof page.journal.issn !== 'string') ||
      (page.journal.eissn !== undefined && typeof page.journal.eissn !== 'string') ||
      !Number.isSafeInteger(page.catalog_rev) || page.catalog_rev < 1 ||
      (revision !== null && page.catalog_rev !== revision) ||
      (page.next_offset !== null && (!Number.isSafeInteger(page.next_offset) || page.next_offset <= offset)) ||
      !Array.isArray(page.facts) || !Array.isArray(page.coverage) || !page.sources ||
      page.catalog_completeness !== 'unknown' || page.semantic_review !== 'not_authenticated' ||
      (selection && (!page.inspection || page.inspection.status !== 'not_verifiable' || !Array.isArray(page.inspection.facts))) ||
      (!selection && page.inspection !== undefined) ||
      !validPageContents(page)) {
      return { ok: false, code: 'invalid_response' }
    }
    return { ok: true, page }
  } catch {
    return { ok: false, code: 'network' }
  }
}
