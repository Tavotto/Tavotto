import { exactPanelManifest, structuralPanelManifest, type RenderState } from '@/store/renderStore'
import type { CanvasObject, PanelObject } from '@/types/document'

/** `<对象id>::<manifest gid>` → manifest gid；其余原样 */
export const innerGid = (gid: string): string => (gid.includes('::') ? gid.slice(gid.indexOf('::') + 2) : gid)

/**
 * 审阅意见锚定的元素 `gid` 此刻在哪张图里（ADR 0187 §五）：先认面板自己（排版的「每个面板一个框」，gid = 面板对象 id），
 * 再认面板精确 / 结构 manifest 里的图内元素。找不到 = 这个元素在当前画布上已经没有了（→ 列表标「原位置已变化」）。
 */
export function owningPanel(
  objects: readonly CanvasObject[],
  render: Pick<RenderState, 'byKey' | 'latest'>,
  gid: string,
): PanelObject | null {
  // 排版里面板内元素的 gid = `<对象id>::<manifest gid>`（local-api 补充）；单图 / 面板框 = 原样
  const sep = gid.indexOf('::')
  const objId = sep >= 0 ? gid.slice(0, sep) : null
  const inner = sep >= 0 ? gid.slice(sep + 2) : gid
  for (const o of objects) {
    if (o.type !== 'panel') continue
    if (objId !== null && o.id !== objId) continue
    if (o.id === gid) return o
    const m = exactPanelManifest(render, o) ?? structuralPanelManifest(render, o)
    if (m?.elements.some((e) => e.gid === inner)) return o
  }
  return null
}
