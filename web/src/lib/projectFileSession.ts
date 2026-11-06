import type { DocumentFormat } from './documentFormat'
import type { ProjectFileBinding } from './projectFile'

export type ProjectFileAxis = { editor_id: string; sequence: number } | null
export interface ProjectFileEdit { axis: ProjectFileAxis; base_axis: ProjectFileAxis; base_revision: string | null }
export interface ProjectFileAddress { projectId: string; format: DocumentFormat; documentId: string }
export interface ProjectFileView {
  version: 1
  status: 'ok'
  binding: { sequence: number; name: string; file: string; revision: string | null; dirty: boolean } | null
  edit: { autosave_revision: string | null; axis: ProjectFileAxis; known: boolean }
  intent_sequence: number
}
export interface ProjectFileRecovery { revision: string | null; expected: ProjectFileView }
export type ProjectFileRead = ProjectFileView | { version: 1; status: 'unavailable' } | { status: 'unsupported' }
export interface ProjectFileTicket {
  sequence: number; binding_sequence: number; name: string; file: string
  autosave_revision: string | null; axis: ProjectFileAxis
}
export type ProjectFileResult = ({ state: 'bound' | 'accepted' } & ProjectFileView)
  | { state: 'superseded' | 'unconfirmed' | 'unavailable' | 'skipped' }
export type ProjectFileReservation = { state: 'reserved'; ticket: ProjectFileTicket }
  | { state: 'superseded' | 'unconfirmed' | 'unavailable' | 'unsupported' }
export interface ProjectFileCapture {
  address: ProjectFileAddress
  generation: number
  axis: ProjectFileAxis
  base_axis: ProjectFileAxis
}

const keyOf = (address: ProjectFileAddress) => JSON.stringify([address.projectId, address.format, address.documentId])
const sameAxis = (a: ProjectFileAxis, b: ProjectFileAxis) => a === null ? b === null
  : b !== null && a.editor_id === b.editor_id && a.sequence === b.sequence
const copy = <T>(value: T): T => structuredClone(value)

/** 一个活跃编辑器一个内存实例；不从共享 localStorage 取得 editor_id。 */
export function createProjectFileEditor(editorId = `editor_${crypto.randomUUID()}`) {
  type Row = {
    support: 'unknown' | 'supported' | 'unsupported' | 'unavailable'
    generation: number; axis: ProjectFileAxis; baseAxis: ProjectFileAxis
    view: ProjectFileView | null; intent: number; localSequence: number; editorId: string
  }
  const rows = new Map<string, Row>()
  const rowOf = (address: ProjectFileAddress) => {
    const key = keyOf(address)
    let row = rows.get(key)
    if (!row) {
      row = { support: 'unknown', generation: 0, axis: null, baseAxis: null, view: null, intent: 0, localSequence: 0, editorId }
      rows.set(key, row)
    }
    return row
  }
  return {
    beginInstall(address: ProjectFileAddress): void {
      const row = rowOf(address)
      row.support = 'unknown'; row.generation = 0; row.axis = null; row.baseAxis = null; row.view = null
    },
    capture(address: ProjectFileAddress): ProjectFileCapture {
      const row = rowOf(address)
      return copy({ address, generation: row.generation, axis: row.axis, base_axis: row.baseAxis })
    },
    newRecoveryBranch(address: ProjectFileAddress): void {
      const row = rowOf(address)
      row.editorId = `editor_${crypto.randomUUID()}`
      row.localSequence = 0
    },
    markUserEdit(address: ProjectFileAddress): void {
      const row = rowOf(address)
      row.generation += 1
      row.localSequence = Math.max(row.localSequence, row.axis?.editor_id === row.editorId ? row.axis.sequence : 0) + 1
      row.axis = { editor_id: row.editorId, sequence: row.localSequence }
    },
    support(address: ProjectFileAddress) { return rowOf(address).support },
    view(address: ProjectFileAddress) { return copy(rowOf(address).view) },
    observe(address: ProjectFileAddress, read: ProjectFileRead, expectedRevision?: string | null): boolean {
      const row = rowOf(address)
      if (read.status !== 'ok') {
        row.support = read.status
        return false
      }
      row.support = 'supported'
      if (expectedRevision !== undefined && read.edit.autosave_revision !== expectedRevision) return false
      if (read.intent_sequence < row.intent) return false
      row.view = copy(read)
      row.intent = read.intent_sequence
      if (row.generation === 0) row.axis = copy(read.edit.known ? read.edit.axis : null)
      row.baseAxis = copy(read.edit.known ? read.edit.axis : null)
      return true
    },
    accepted(captured: ProjectFileCapture, result: ProjectFileResult): boolean {
      const row = rowOf(captured.address)
      if (result.state !== 'accepted' && result.state !== 'bound') return false
      row.support = 'supported'
      // 在途后又编辑：新轴保留；这次成功轴仅成为下一次写的祖先。
      if (sameAxis(row.axis, captured.axis)) row.axis = copy(result.edit.axis)
      row.baseAxis = copy(result.edit.axis)
      if (result.intent_sequence < row.intent) return false
      row.view = copy(result)
      row.intent = result.intent_sequence
      return true
    },
    reserved(address: ProjectFileAddress, ticket: ProjectFileTicket): void {
      const row = rowOf(address)
      if (ticket.sequence > row.intent) row.intent = ticket.sequence
    },
    isLatest(address: ProjectFileAddress, sequence: number): boolean { return rowOf(address).intent === sequence },
    clear() { rows.clear() },
  }
}

export const projectFileEditor = createProjectFileEditor()

export function bindingFromProjectFile(address: ProjectFileAddress, view: ProjectFileView): ProjectFileBinding | null {
  return view.binding ? { ...copy(view.binding), projectId: address.projectId } : null
}
