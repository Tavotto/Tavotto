/**
 * 「这一条 override 是样式写的」的登记（ADR 0081 §十三，`FigureDocument.style.owned`）。
 *
 * 样式写的 override 在脚本改过那一项之后让位（用户 2026-09-25 裁决：重跑后脚本赢）；用户手改的
 * 永远保留。两者在 override 上长得一模一样，所以样式每写一条就在这里登记一条，连同写入那一刻
 * 脚本的原生值（基线）。
 *
 * 这里只放**纯函数**（`store/actions` 与 `store/styleBinding` 两边都要用，放在任一边都会成环）。
 */
import { sameReading } from '@/lib/stylePresets'
import type { FigureDocument, PanelObject, StyleOwnedOverride } from '@/types/document'

/** 登记的第一层键：面板 id + 素材（与 styleBinding 会话记账同一个身份——换素材 = 换一张图） */
export const figKey = (p: Pick<PanelObject, 'id' | 'fileId'>): string => `${p.id}@${p.fileId}`

/**
 * 这一条 override 此刻**还是**样式写的吗：登记过，且文档里那条 override 的值仍是样式写下的值。
 * 用户在属性页里把它改成别的值、清掉它、整张重置，它就不再是样式的——不论那条写入路径记没记得注销。
 */
export function ownedLive(
  doc: FigureDocument,
  p: PanelObject,
  gid: string,
  prop: string,
): StyleOwnedOverride | null {
  const e = doc.style?.owned?.[figKey(p)]?.[gid]?.[prop]
  if (!e) return null
  const o = p.overrides.find((x) => x.gid === gid && x.prop === prop)
  return o && sameReading(o.value, e.value) ? e : null
}

/**
 * 用户（不是样式）写了这几条 override：注销它们的登记——从此归用户，脚本重跑不让位。
 * 在 commit 的 recipe 里调（`d` 是草稿），与写 override 同一次 commit、同一次撤销。
 */
export function releaseOwned(d: FigureDocument, panel: PanelObject, keys: { gid: string; prop: string }[]): void {
  const owned = d.style?.owned
  const byGid = owned?.[figKey(panel)]
  if (!owned || !byGid) return
  for (const { gid, prop } of keys) {
    if (!byGid[gid]?.[prop]) continue
    delete byGid[gid][prop]
    if (!Object.keys(byGid[gid]).length) delete byGid[gid]
  }
  if (!Object.keys(byGid).length) delete owned[figKey(panel)]
  if (!Object.keys(owned).length) delete d.style!.owned
}
