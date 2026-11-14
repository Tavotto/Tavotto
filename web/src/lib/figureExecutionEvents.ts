import type { FigureSnapshotInvalidation } from '@/lib/api'
import type { FigureOrigin } from '@/types/document'

type Consumer = (execution: FigureSnapshotInvalidation, pj: string | null) => void
let consumer: Consumer | undefined
const early: { execution: FigureSnapshotInvalidation; pj: string | null }[] = []

type OriginAdoption = (sent: FigureOrigin, adopted: FigureOrigin, pj: string | null) => void
let adoptionConsumer: OriginAdoption | undefined

/** A store-free relay keeps renderStore outside the document coordinator's import graph. */
export function registerFigureExecutionConsumer(next: Consumer): void {
  consumer = next
  for (const event of early.splice(0)) next(event.execution, event.pj)
}

/** Verified cold-replay adoptions reach the document coordinator through the same relay. */
export function registerFigureOriginAdoptionConsumer(next: OriginAdoption): void {
  adoptionConsumer = next
}

export function observeFigureOriginAdoption(sent: FigureOrigin, adopted: FigureOrigin, pj: string | null): void {
  adoptionConsumer?.(sent, adopted, pj)
}

/** Missing/unknown execution evidence is never converted into a default owner or a guessed run. */
export function observeFigureExecution(value: unknown, pj: string | null): void {
  if (!value || typeof value !== 'object') return
  const fact = value as Record<string, unknown>
  const nonempty = (v: unknown): v is string => typeof v === 'string' && v.length > 0
  if ((fact.scope !== 'owner' && fact.scope !== 'sources') || !nonempty(fact.script) ||
      (fact.run_config !== null && !nonempty(fact.run_config)) || !nonempty(fact.execution_id) ||
      !Array.isArray(fact.source_ids) || !fact.source_ids.every(nonempty) ||
      (fact.receipt_ids !== undefined && (!Array.isArray(fact.receipt_ids) || !fact.receipt_ids.every(nonempty)))) return
  const execution = value as FigureSnapshotInvalidation
  if (consumer) consumer(execution, pj)
  else early.push({ execution, pj })
}
