import { svgPreviewCoordinates } from './svgPreviewCoordinates'

export interface SvgPreviewTargets {
  mapped: boolean
  nodes: Element[]
}

type TargetEntry = { transform: string[]; style: string[] }
const MIRROR = 'data-tavotto-svg-targets'
const parsed = new WeakMap<SVGSVGElement, { raw: string; map: Record<string, TargetEntry> | null }>()
const validId = (id: unknown): id is string => typeof id === 'string' && id.length > 0
  && [...id].every(char => char.charCodeAt(0) >= 32)

function targetMap(svg: SVGSVGElement, raw: string): Record<string, TargetEntry> | null {
  const cached = parsed.get(svg)
  if (cached?.raw === raw) return cached.map
  let map: Record<string, TargetEntry> | null = null
  try {
    const value: unknown = JSON.parse(raw)
    if (value && typeof value === 'object' && !Array.isArray(value)
      && Object.keys(value).every(validId)
      && Object.values(value).every(entry => entry && typeof entry === 'object' && !Array.isArray(entry)
        && Object.keys(entry).length === 2 && Object.hasOwn(entry, 'transform') && Object.hasOwn(entry, 'style')
        && ['transform', 'style'].every(kind => {
          const ids = (entry as Record<string, unknown>)[kind]
          return Array.isArray(ids) && ids.every(validId)
        }))) map = value as Record<string, TargetEntry>
  } catch { /* 显式镜像坏了就拒绝，不能退回源场景里碰巧同名的 gid。 */ }
  parsed.set(svg, { raw, map })
  return map
}

const escaped = (id: string) => typeof CSS !== 'undefined' && CSS.escape
  ? CSS.escape(id) : id.replace(/["\\]/g, '\\$&')

/**
 * 只用于当前挂载 SVG 的临时 DOM 预览，不产生可写入文档的几何。
 * 镜像缺席才走旧单 gid；有镜像时缺项、坏目标、非 pt wrapper 或非 antichain 均拒绝。
 */
export function svgPreviewTargets(
  svg: SVGSVGElement | null,
  gid: string,
  kind: 'transform' | 'style',
): SvgPreviewTargets | null {
  if (!svg) return { mapped: false, nodes: [] }
  const raw = svg.getAttribute(MIRROR)
  try {
    if (raw === null) {
      const node = svg.querySelector(`[id="${escaped(gid)}"]`)
      return { mapped: false, nodes: node ? [node] : [] }
    }
    const map = targetMap(svg, raw)
    if (!map || !Object.hasOwn(map, gid)) return null
    const resolved: Record<'transform' | 'style', Element[]> = { transform: [], style: [] }
    for (const operation of ['transform', 'style'] as const) {
      const ids = map[gid][operation]
      if (new Set(ids).size !== ids.length) return null
      for (const id of ids) {
        const matches = svg.querySelectorAll(`[id="${escaped(id)}"]`)
        if (matches.length !== 1) return null
        const node = matches[0]
        // wrapper 本身的父空间不是它声明的 pt 空间；只接受 wrapper 内的真实绘制节点。
        if (node.hasAttribute('data-tavotto-native-viewport-pt')
          || svgPreviewCoordinates(svg, node.parentElement)?.space !== 'native-pt') return null
        if (resolved[operation].some(previous => previous.contains(node) || node.contains(previous))) return null
        const previous = resolved[operation].at(-1)
        if (previous && !(previous.compareDocumentPosition(node) & 4)) return null
        resolved[operation].push(node)
      }
    }
    return { mapped: true, nodes: resolved[kind] }
  } catch { return null }
}
