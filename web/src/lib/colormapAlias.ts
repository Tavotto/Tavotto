import type { Manifest, ManifestElement } from './api'

/**
 * 与这个元素**共用同一份色图状态**的全部 gid：色条 ↔ 它的 mappable（引擎
 * `overrides.ALIAS_GROUPS` 里 `("colorbar", "cmap")` 那一对）。
 *
 * 「回到脚本原样的色图」要清的是 override 落在哪儿——用户从图像那边换的，
 * override 在图像 gid 上；从色条那边换的，在色条 gid 上。两边界面都摆着同一格
 * 「脚本原样」，所以清的时候两边一起清。判据只认 manifest 的 `mappable_gid`
 * （引擎从 `cb.mappable` 反查得来），不猜「两边 cmap 名字相同」。
 */
export function colormapAliasGids(
  manifest: Manifest | null | undefined,
  el: ManifestElement,
): string[] {
  const out = [el.gid]
  if (el.mappable_gid) out.push(el.mappable_gid)
  // 色阶兄弟（`scale_gids`，共用 norm 对象的那几块）与 mappable 同一组：色条的
  // 色图落在它们身上，回到脚本原样也要一起清
  out.push(...(el.scale_gids ?? []))
  for (const other of manifest?.elements ?? []) {
    if (other.role === 'colorbar' && colorbarCovers(other, el.gid)) out.push(other.gid)
  }
  return [...new Set(out)]
}

/** 这条色条给 `gid` 上色吗：它的 mappable，或与之共用色阶的兄弟 */
export function colorbarCovers(colorbar: ManifestElement, gid: string): boolean {
  return colorbar.mappable_gid === gid || (colorbar.scale_gids ?? []).includes(gid)
}
