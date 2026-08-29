import type { LayoutVersionMeta } from '@/lib/api'
import type { CanvasData } from '@/types/document'

/**
 * 一个版本检查点该恢复到**哪一张画布**（R-03）。
 *
 * 检查点存的是激活画布的内容，却按 documentId（整个项目）归档。改造前恢复
 * 一律写进"恰好激活的那张"：在画布 B 上产生的检查点，在画布 A 上按一下恢复，
 * A 的内容和名字当场被 B 盖掉——而界面上没有任何地方说过这一版来自 B。
 *
 * 判据单独放在这里而不是揉在对话框里：它是一条**规则**，四条分支各自的后果
 * 差得很远（一条不动别的画布，两条会覆盖当前画布），值得被单独看护。
 */
export type RestoreTarget =
  /** 检查点就来自当前画布：直接恢复，最常见的那条路不该多一次点击 */
  | { kind: 'same' }
  /** 来自另一张**仍然存在**的画布：切过去写，当前画布一个字节不动 */
  | { kind: 'other'; canvasId: string; name: string }
  /** 原画布已被删除：只能写进当前画布，会覆盖它 */
  | { kind: 'missing'; from: string }
  /** 旧检查点没有画布身份：不知道来自哪张，只能写进当前画布，会覆盖它 */
  | { kind: 'unknown' }

export function resolveRestoreTarget(
  meta: Pick<LayoutVersionMeta, 'canvasId' | 'canvasName'>,
  state: { activeCanvasId: string; canvases: Pick<CanvasData, 'id' | 'name'>[] },
): RestoreTarget {
  // **缺席就是缺席**：旧检查点没有 canvasId，不要在这里补一个当前画布的 id
  // 当默认值——那等于替它编一个身份，而编出来的身份恰好总是"允许直接覆盖"。
  if (!meta.canvasId) return { kind: 'unknown' }
  if (meta.canvasId === state.activeCanvasId) return { kind: 'same' }
  const target = state.canvases.find((c) => c.id === meta.canvasId)
  if (!target) return { kind: 'missing', from: meta.canvasName || meta.canvasId }
  return { kind: 'other', canvasId: target.id, name: target.name }
}
