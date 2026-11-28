import type { Manifest, ManifestElement } from './api'
import { effectiveOverrideIndex } from './effectiveOverride'

/**
 * 「一个量只留一个 override 地址」的计划（引擎 `ALIAS_GROUPS` 的前端镜像，事实全来自 manifest 元素上的
 * `alias_covers`，不在前端另抄一张表）。
 *
 * 引擎里同一个量可以有两个 override 地址：广播端（图例的「标题字号」`(legend, title_fontsize)`）与窄端
 * （图例标题元素的 `(legend.title, fontsize)`），而窄端永远排在广播之后应用。两条同时存在时，改广播端
 * 画面不变（窄端盖着）、控件却读自己那条 override 显示新值——「改了、图不变、控件说已改」。所以写入口在
 * 同一次 commit 里清掉另一个地址：
 *
 * - 写广播端 `(g, p)`：删掉 `alias_covers[p]` 列出的全部窄端 override（不删，这次写就不生效）；
 * - 写窄端 `(g', p')`，且某个**纯别名**（`alias_pure`）的广播端**只**盖着这一个窄端（1:1，如图例标题）：删掉那条
 *   广播 override——窄端本来就赢，但广播端控件会读着旧 override 显示过期值；1:多（图例整体字号 vs 单条）时保留广播，
 *   它还管着别的成员；不纯的广播端（单条目图例上的整体字号：还改 `Legend._fontsize` 并重建布局）无论几个成员都保留。
 *
 * 同一批 patch 里一起写的两端都保留（样式预设 / 批量写入有意同时写，窄端赢是引擎既有语义）。
 */

type Key = { readonly gid: string; readonly prop: string }

const keyOf = (k: Key) => `${k.gid}\u0000${k.prop}`

type FindElement = (gid: string) => ManifestElement | undefined

function reciprocalPeers(findElement: FindElement, { gid, prop }: Key): string[] {
  const peers = [gid]
  const seen = new Set(peers)
  const links = (id: string) => {
    const element = findElement(id)
    return element?.alias_pure?.includes(prop) ? element.alias_covers?.[prop]?.[prop] : undefined
  }
  // Reciprocal pure links to the same property denote one value shared by a
  // group. Follow the links through its owner as well as its immediate peers.
  for (const member of peers) {
    for (const peer of links(member) ?? []) {
      if (seen.has(peer) || !links(peer)?.includes(member)) continue
      seen.add(peer)
      peers.push(peer)
    }
  }
  return peers
}

export function sharedOverrideAddresses(
  manifest: Pick<Manifest, 'elements'> | null | undefined,
  targets: readonly Key[],
): Key[] {
  if (!manifest) return [...targets]
  const byGid = new Map(manifest.elements.map((element) => [element.gid, element]))
  return targets.flatMap((target) =>
    reciprocalPeers((gid) => byGid.get(gid), target).map((gid) => ({ gid, prop: target.prop })),
  )
}

function coveredAliasAddresses(findElement: FindElement, key: Key): Key[] {
  const narrow = findElement(key.gid)?.alias_covers?.[key.prop]
  if (!narrow) return []
  const covered = new Map<string, Key>()
  for (const [prop, gids] of Object.entries(narrow)) {
    for (const gid of gids) covered.set(keyOf({ gid, prop }), { gid, prop })
  }
  for (const gid of reciprocalPeers(findElement, key).slice(1)) {
    covered.set(keyOf({ gid, prop: key.prop }), { gid, prop: key.prop })
  }
  return [...covered.values()]
}

/** 写广播端前查找可能被清掉的地址，沿用写入时的窄端和共享关系。 */
export function aliasCovered(
  manifest: Pick<Manifest, 'elements'> | null | undefined,
  gid: string,
  prop: string,
): Key[] {
  return coveredAliasAddresses((id) => manifest?.elements.find((e) => e.gid === id), { gid, prop })
}

/** 这一批 patch 要连带清掉的 (gid, prop)；没有可清的就是空数组。`manifest` 缺失 / 旧引擎没发字段 = 空 */
export function aliasClearPlan(
  overrides: readonly Key[],
  manifest: Pick<Manifest, 'elements'> | null | undefined,
  patches: readonly Key[],
): Key[] {
  if (!manifest || !patches.length || !overrides.length) return []
  const written = new Set(patches.map(keyOf))
  const out = new Map<string, Key>()
  const clear = (gid: string, prop: string) => {
    if (written.has(keyOf({ gid, prop }))) return
    if (effectiveOverrideIndex(overrides, gid, prop) < 0) return
    out.set(keyOf({ gid, prop }), { gid, prop })
  }
  // 广播端 → 窄端
  const byGid = new Map(manifest.elements.map((e) => [e.gid, e]))
  for (const patch of patches) {
    for (const { gid, prop } of coveredAliasAddresses((id) => byGid.get(id), patch)) clear(gid, prop)
  }
  // 窄端 → 只盖着它一个的广播端
  const narrowKeys = new Set(patches.map(keyOf))
  for (const e of manifest.elements) {
    if (!e.alias_covers) continue
    for (const [bprop, narrow] of Object.entries(e.alias_covers)) {
      // 只有引擎声明的纯别名才可反向清：不纯的广播端（图例整体字号会改 `_fontsize` 并重建布局）有窄端存不下的状态
      if (!e.alias_pure?.includes(bprop)) continue
      const entries = Object.entries(narrow).flatMap(([nprop, gids]) => gids.map((g) => ({ gid: g, prop: nprop })))
      if (entries.length === 1 && narrowKeys.has(keyOf(entries[0]))) clear(e.gid, bprop)
    }
  }
  return [...out.values()]
}
