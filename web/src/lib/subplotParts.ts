import type { Manifest, ManifestElement } from './api'

/**
 * 一张多子图的图里，一个**子图簇**：宿主 axes + 跟着它走的色条轴 / 孪生轴。
 *
 * 问题面板「按图」看时按它把一张组图拆成 (a)(b)(c) 几张卡片（2026-09-28
 * 问题面板卡片化），缩略图按 `bbox` 从成图里裁。**只是呈现层的分组**：不进
 * 指纹、不进跨语言合同、不影响修复与定位。
 */
export interface SubplotPart {
  /** 宿主 axes 的 gid（稳定分组键）。**绝不进用户可见文案** */
  key: string
  /** 同一张图里的阅读顺序（0 起） */
  order: number
  /** 图里写着的面板标签（「(a)」「b」）；没写就是 null */
  tag: string | null
  /** 引擎给宿主 axes 的标签（「子图 1」，中文散文）；显示前过 `engineLabel()` */
  label: string
  /**
   * 宿主 axes 在图里的包围盒（figure 分数、y 向下，与 manifest `bbox` 同一套），缩略图按它裁。
   * **不并上色条轴**：被两张图共用的色条挂在第一张图名下，并进来的话 (b) 的缩略图会连 (c) 一起框住
   */
  bbox: [number, number, number, number]
}

/** 这个 gid 归哪个子图簇；图里只有一个簇、或 gid 不在任何 axes 里时是 null */
export type SubplotLookup = (gid: string) => SubplotPart | null

const AXES_GID = /^axes_\d+$/

/**
 * 面板标签：单个字母，可带一对括号（`(a)` / `a)` / `（b）` / `[c]`）。
 * **只认字母**：数字标签和刻度、注释里的数混在一起分不清，宁可退回「子图 N」也不认错。
 */
const TAG_RE = /^\s*[(（[]?\s*([A-Za-z])\s*[)）\]]?\s*$/

const axesOf = (gid: string): string => gid.split('.')[0]

const textOf = (el: ManifestElement): string | null => {
  const v = el.editable?.find((f) => f.prop === 'text')?.value
  return typeof v === 'string' ? v : null
}

/**
 * 宿主判据：谁的 `follow_gids` 里有它（引擎裁决的共享关系，色条轴与孪生轴都在
 * 里面），否则色条轴认它那根色条的 `host_gid`。两条都是引擎发的事实，前端不
 * 按几何远近猜——猜错一次，用户点「修复 (b)」就改到了 (c)。
 */
function buildLookup(manifest: Manifest): SubplotLookup {
  const elements = manifest.elements ?? []
  const byGid = new Map(elements.map((e) => [e.gid, e]))
  const axes = elements.filter((e) => AXES_GID.test(e.gid))
  const parent = new Map<string, string>()
  for (const a of axes) {
    for (const f of a.follow_gids ?? []) if (f !== a.gid && byGid.has(f)) parent.set(f, a.gid)
  }
  for (const a of axes) {
    if (parent.has(a.gid) || !a.is_colorbar || !a.colorbar_gid) continue
    const host = byGid.get(a.colorbar_gid)?.host_gid
    if (host && host !== a.gid && AXES_GID.test(host)) parent.set(a.gid, host)
  }
  const rootOf = (gid: string): string => {
    let at = gid
    const seen = new Set<string>()
    while (parent.has(at) && !seen.has(at)) {
      seen.add(at)
      at = parent.get(at)!
    }
    return at
  }

  const members = new Map<string, ManifestElement[]>()
  for (const a of axes) {
    const r = rootOf(a.gid)
    members.set(r, [...(members.get(r) ?? []), a])
  }
  // 一个簇 = 整张图：拆不出东西来，按图看时就是一张「整张图」卡
  if (members.size < 2) return () => null

  const tagOf = (group: Set<string>): string | null => {
    const hits = elements
      .filter((e) => {
        const [ax, part] = e.gid.split('.')
        return group.has(ax) && !!part && (part.startsWith('texts_') || part === 'title')
      })
      .map((e) => ({ e, m: TAG_RE.exec(textOf(e) ?? '') }))
      .filter((x) => x.m)
      .sort((a, b) => a.e.bbox[1] - b.e.bbox[1] || a.e.bbox[0] - b.e.bbox[0])
    return hits[0]?.m?.[0].trim() ?? null
  }

  const parts = [...members.entries()].map(([root, list]) => {
    const group = new Set(list.map((e) => e.gid))
    return {
      key: root,
      tag: tagOf(group),
      label: byGid.get(root)?.label ?? root,
      bbox: byGid.get(root)?.bbox ?? list[0].bbox,
    }
  })
  // 顺序：每个簇都写了标签、且互不重复时按标签字母排（作者说了算）；否则按版面
  // 先行后列——行的判据是顶边落在同一条带里（半个最矮簇的高度）
  const letters = parts.map((p) => p.tag?.replace(/[^A-Za-z]/g, '').toLowerCase() ?? '')
  const byTag = letters.every((l) => l) && new Set(letters).size === letters.length
  const band = Math.min(...parts.map((p) => p.bbox[3])) / 2
  const ranked = parts
    .map((p, i) => ({ p, letter: letters[i] }))
    .sort((a, b) =>
      byTag
        ? a.letter.localeCompare(b.letter)
        : Math.abs(a.p.bbox[1] - b.p.bbox[1]) > band
          ? a.p.bbox[1] - b.p.bbox[1]
          : a.p.bbox[0] - b.p.bbox[0],
    )
  const byRoot = new Map<string, SubplotPart>(ranked.map(({ p }, order) => [p.key, { ...p, order }]))
  const byAxes = new Map<string, SubplotPart>()
  for (const a of axes) byAxes.set(a.gid, byRoot.get(rootOf(a.gid))!)
  return (gid) => byAxes.get(axesOf(gid)) ?? null
}

const cache = new WeakMap<Manifest, SubplotLookup>()

/** manifest → gid 归属查询。按 manifest 对象缓存：同一次渲染只建一次表 */
export function subplotLookup(manifest: Manifest): SubplotLookup {
  let hit = cache.get(manifest)
  if (!hit) {
    hit = buildLookup(manifest)
    cache.set(manifest, hit)
  }
  return hit
}
