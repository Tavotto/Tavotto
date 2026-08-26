/**
 * 前端诊断（ADR 0016）的**唯一对外入口**。
 *
 * 业务代码只 import 这个文件。三条纪律写在这里，因为这里是它们唯一能被
 * 集中看见的地方：
 *
 *   * **只观察**。诊断不参与任何业务判断，业务代码永远不从诊断读状态。
 *   * **不当真源**。快照是**读**业务 store 得来的；诊断自己不维护影子状态。
 *   * **自己出错不许伤到编辑**。记事件整体吞异常；导出失败只影响导出。
 */
export { recordDiagnosticEvent, recordIfChanged, readDiagnosticTrace, clearDiagnosticTrace, traceLength, sessionElapsedMs, RING_CAPACITY } from './store'
export { buildFrontendDiagnosticSnapshot, panelSnapshot } from './snapshot'
export { documentDigest } from './digest'
export {
  diagnosticHash,
  docHash,
  panelHash,
  fileHash,
  variantHash,
  variantHashOrNull,
  previewHash,
  versionHash,
  objectHash,
} from './hash'
export {
  readAuthority,
  readAuthorityById,
  authorityFields,
  recordInvariantViolation,
  verifyGeometryAuthority,
  sampleDisplayState,
  type AuthorityView,
  type AuthorityVerdict,
} from './authority'
export { serializeRecorded } from './sanitize'
export {
  BUNDLE_SCHEMA_VERSION,
  SNAPSHOT_SCHEMA_VERSION,
  TRACE_SCHEMA_VERSION,
  type DiagnosticEvent,
  type DiagnosticEventType,
  type DiagnosticPayload,
  type FrontendDiagnosticSnapshot,
  type RecordedEvent,
} from './types'

import { readDiagnosticTrace, recordDiagnosticEvent } from './store'
import { buildFrontendDiagnosticSnapshot } from './snapshot'
import { serializeRecorded } from './sanitize'
import type { DiagnosticPayload, RecordedEvent } from './types'

/**
 * 导出用的载荷。**取 trace 之前先记一条 `diagnostics.export`**——它是这次
 * 导出的时间锚点，读包的人一眼看得出「用户是在这一刻按的导出」，从而知道
 * 上面那些事件距离事故有多远。
 *
 * 每条记录再过一遍 `serializeRecorded`：环里那份本来就已经脱敏（写入即脱敏），
 * 这一遍是幂等的第二道——它挡住的是「有人绕过 recordDiagnosticEvent 直接往
 * 环里塞了东西」这种将来才可能出现的路径。
 */
export function buildDiagnosticPayload(): DiagnosticPayload {
  const snapshot = buildFrontendDiagnosticSnapshot()
  recordDiagnosticEvent({
    type: 'diagnostics.export',
    trace_count: readDiagnosticTrace().length,
    panel_count: snapshot.panels.length,
  })
  const trace = readDiagnosticTrace()
    .map(serializeRecorded)
    .filter((e): e is Record<string, unknown> => e != null) as unknown as RecordedEvent[]
  return { frontend_state: snapshot, interaction_trace: trace }
}

/**
 * 开发态调试入口。**只在开发构建挂**——生产版不暴露用户状态，哪怕它已经
 * 脱敏过：多一个全局读取口就多一条将来被别的代码顺手用起来的路径。
 */
export function installDiagnosticsDevHook(): void {
  if (!import.meta.env?.DEV) return
  if (typeof window === 'undefined') return
  ;(window as unknown as Record<string, unknown>).__TAVOTTO_DIAGNOSTICS__ = {
    getRecentEvents: () => readDiagnosticTrace(),
    getSnapshot: () => buildFrontendDiagnosticSnapshot(),
    clear: () => import('./store').then((m) => m.clearDiagnosticTrace()),
  }
}
