import type { Manifest, ManifestElement } from './api'
import { DEFAULT_SEVERITY, severityOf, type PublicationProfile, type Severity } from './profile'

/**
 * 真实渲染几何上的干涉检测（文字互相重叠 / 文字压到别的子图 / 图例压住数据）——
 * `src/tavotto/engine/interference.py` 的 `detect` 的**逐行移植**（V-011，`docs/sciviz/validation-spec.md`）。
 *
 * 判据只有 Python 那一份；浏览器里跑不了 Python，画布与导出对话框的预检又只在前端求值
 * （`lib/preflight.ts`），所以这里是同一套算法的第二个求值器，不是第二套口径。两份靠
 * `tests/golden/interference_vectors.json`（逐对输出）与 `tests/golden/preflight_vectors.json`
 * 的 `interference-*`（折进预检后的聚合投影）对拍：pytest 与 vitest 各跑一遍同一份向量。
 * 改任一侧先改 Python，再 `python scripts/gen_interference_vectors.py --write`。
 *
 * 只认 manifest 里已经有的事实：`bbox` / `clip_bbox`（figure 分数、y 向下）、`geometry`
 * （真画出来的路径）、`editable` 里的 `visible` / `position`。「确定 / 风险」原样保留：
 * 拿得到 geometry 的数据按线段判（`detail.certain = true`），只有包围盒的只标风险。
 *
 * 数值格式化与取整按 Python 的规则（`pyFixed` / `pyRound`：精确值上的四舍六入五成双）——
 * JS 的 `toFixed` 在恰好可表示的半值上进位（1.25 → "1.3"），Python 是 "1.2"。
 */

/** 两个文字框要在两个方向上都至少咬进这么多 mm 才算重叠（`interference.MIN_OVERLAP_MM`）。 */
export const MIN_OVERLAP_MM = 0.25

export const DECORATION_ROLES = ['title', 'axis_label', 'ticklabel', 'legend_text']
export const TEXT_ROLES = [...DECORATION_ROLES, 'text', 'legend']
export const DATA_ROLES = [
  'line',
  'errorbar',
  'scatter',
  'fill',
  'patch',
  'collection',
  'linecoll',
  'image',
  'bar',
  'bar_series',
  'stem_series',
]
/** 有 geometry 也按包围盒（只标风险）判的角色——`interference.BBOX_BASIS_ROLES`（现在为空：误差棒 / 茎叶按真实几何判） */
export const BBOX_BASIS_ROLES: string[] = []
/** 包围盒就是画出来的那块的角色（图像）——`interference.RECT_BASIS_ROLES`，相交即「确定」 */
export const RECT_BASIS_ROLES = ['image']

export const CHECK_TEXT_OVERLAP = 'text-overlap'
export const CHECK_TEXT_OVER_AXES = 'text-over-axes'
export const CHECK_LEGEND_OVER_DATA = 'legend-over-data'
export const INTERFERENCE_CHECK_IDS = [CHECK_TEXT_OVERLAP, CHECK_TEXT_OVER_AXES, CHECK_LEGEND_OVER_DATA]

type Rect = [number, number, number, number] // x0, y0, x1, y1

/** `detect` 的一条输出（与 Python 同形，少了只给没有文案表的宿主看的 `text`）。 */
export interface InterferenceIssue {
  id: string
  severity: Severity
  message: { key: string; params: Record<string, string> }
  object_ids: string[]
  gids: string[]
  detail: Record<string, unknown>
}

/* ----------------------------- Python 数值口径 ----------------------------- */

/**
 * Python `format(x, f".{nd}f")`：按 x 的**精确二进制值**取整、恰好一半时取偶。
 * `toFixed` 的规则是一半时取大，在 0.25 / 1.25 这种恰好可表示的半值上与 Python 不同。
 */
export function pyFixed(x: number, nd: number): string {
  if (!Number.isFinite(x)) return String(x)
  // 快路：离「恰好一半」远的数，toFixed 与 Python 一样按精确值取最近（只在恰好的半值上分叉）。
  // 命中多的图（几百对互相压着的文字）每对要格式化四次，慢路的逐位运算占了大头
  const scaled = Math.abs(x) * 10 ** nd
  if (!Object.is(x, -0) && Math.abs(scaled - Math.floor(scaled) - 0.5) > 1e-6) return x.toFixed(nd)
  const neg = x < 0 || Object.is(x, -0)
  // 多取 30 位：double 在这里量得到的数值（≤ 1e6）与真正的半值至少差一个 ulp，远大于 1e-30
  const exact = Math.abs(x).toFixed(Math.min(100, nd + 30))
  const [ip, fp = ''] = exact.split('.')
  const digits = (ip + fp.slice(0, nd)).split('').map(Number)
  const rest = fp.slice(nd)
  let up: boolean
  if (rest[0] > '5') up = true
  else if (rest[0] < '5') up = false
  else if (/[1-9]/.test(rest.slice(1))) up = true
  else up = digits[digits.length - 1] % 2 === 1 // 恰好一半：取偶
  if (up) {
    let i = digits.length - 1
    while (i >= 0) {
      if (digits[i] === 9) {
        digits[i] = 0
        i--
      } else {
        digits[i] += 1
        break
      }
    }
    if (i < 0) digits.unshift(1)
  }
  const intLen = digits.length - nd
  const body = nd > 0 ? `${digits.slice(0, intLen).join('')}.${digits.slice(intLen).join('')}` : digits.join('')
  return (neg ? '-' : '') + body
}

/** Python `round(x, nd)`（同一套取偶规则；-0.0 保留符号，与 JSON 往返后的 Python 值一致）。 */
export const pyRound = (x: number, nd: number): number => Number(pyFixed(x, nd))

/** `interference._num`：只认有限的数（布尔、字符串、NaN、±∞ 一律不是数） */
function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function field(el: ManifestElement, prop: string): unknown {
  for (const f of el.editable ?? []) if (f && f.prop === prop) return f.value
  return undefined
}

/** `[x, y, w, h]` → `[x0, y0, x1, y1]`；不是四个有限数就 null */
function rect(v: unknown): Rect | null {
  if (!Array.isArray(v) || v.length !== 4) return null
  const n = v.map(num)
  if (n.some((x) => x == null)) return null
  const [x, y, w, h] = n as number[]
  return [x, y, x + w, y + h]
}

function intersect(a: Rect, b: Rect): Rect | null {
  const x0 = Math.max(a[0], b[0])
  const y0 = Math.max(a[1], b[1])
  const x1 = Math.min(a[2], b[2])
  const y1 = Math.min(a[3], b[3])
  if (x1 <= x0 || y1 <= y0) return null
  return [x0, y0, x1, y1]
}

const contains = (outer: Rect, inner: Rect): boolean =>
  outer[0] <= inner[0] && outer[1] <= inner[1] && outer[2] >= inner[2] && outer[3] >= inner[3]

const isVisible = (el: ManifestElement): boolean => field(el, 'visible') !== false

/** 画出来占的框：`bbox` 先折进 `clip_bbox`（`interference.visible_rect`） */
function visibleRect(el: ManifestElement): Rect | null {
  if (!isVisible(el)) return null
  let box = rect(el.bbox)
  if (box == null) return null
  const clip = rect(el.clip_bbox)
  if (clip != null) box = intersect(box, clip)
  return box
}

const gidOf = (el: ManifestElement): string => (typeof el.gid === 'string' ? el.gid : '')

/** `axes_3.legend.texts_0` → `axes_3`；figure 级元素回空串 */
export function ownerAxes(gid: string): string {
  if (!gid.startsWith('axes_')) return ''
  return gid.split('.')[0]
}

/** Python `str.rpartition('.')` */
function rpartition(gid: string): [string, string, string] {
  const i = gid.lastIndexOf('.')
  return i < 0 ? ['', '', gid] : [gid.slice(0, i), '.', gid.slice(i + 1)]
}

/** `axes_0.legend.texts_1` → `axes_0.legend`；不是图例条目回空串 */
export function legendOf(gid: string): string {
  const [head, sep, rest] = rpartition(gid)
  if (sep && rest.startsWith('texts_') && (head.endsWith('.legend') || head.includes('legend_'))) return head
  return ''
}

const isTopLevelAxes = (el: ManifestElement): boolean =>
  (el.role === 'axes' || el.role === 'axes3d') && field(el, 'position') != null

function hiddenLegends(elements: ManifestElement[]): Set<string> {
  const out = new Set<string>()
  for (const el of elements) if (el.role === 'legend' && field(el, 'visible') === false) out.add(gidOf(el))
  return out
}

/** 同一个图例里的两个条目、或条目与它自己的框（`la` / `lb` = 各自的 `legendOf`，调用方先算好） */
function sameLegendFamily(ga: string, la: string, gb: string, lb: string): boolean {
  if (la && lb && la === lb) return true
  return (!!la && la === gb) || (!!lb && lb === ga)
}

function segmentInRect(
  ax: number, ay: number, bx: number, by: number,
  x0: number, y0: number, x1: number, y1: number,
): boolean {
  const dx = bx - ax
  const dy = by - ay
  let t0 = 0.0
  let t1 = 1.0
  for (const [p, q] of [[-dx, ax - x0], [dx, x1 - ax], [-dy, ay - y0], [dy, y1 - ay]]) {
    if (p === 0) {
      if (q < 0) return false
      continue
    }
    const r = q / p
    if (p < 0) {
      if (r > t1) return false
      t0 = Math.max(t0, r)
    } else {
      if (r < t0) return false
      t1 = Math.min(t1, r)
    }
  }
  return t0 <= t1
}

/**
 * 图例一侧**真正挡住数据的东西**（V-011「图例一侧的取法（与数据出图摆放共用）」）——
 * `interference.legend_cover_rects` 的逐行移植：框画出来且背景不透明（`legend_cover.opaque`）取整块框
 * （`frame`）；无框或背景完全透明取每格示意（hard）与每条条目文字（soft，只能说「可能」）的框，各外扩
 * 1 个显示像素（`entries`）；量不了退回整块框。
 */
export function legendCoverRects(
  leg: ManifestElement,
  elements: ManifestElement[],
  W: number,
  H: number,
): { hard: Rect[]; soft: Rect[]; basis: 'frame' | 'entries' } {
  const whole = visibleRect(leg)
  const frame = { hard: whole != null ? [whole] : [], soft: [] as Rect[], basis: 'frame' as const }
  const cover: unknown = leg.legend_cover
  if (!cover || typeof cover !== 'object' || Array.isArray(cover)) return frame
  const c = cover as { status?: unknown; opaque?: unknown; px_mm?: unknown; handles?: unknown }
  if (c.status !== 'ok' || c.opaque !== false) return frame
  const px = num(c.px_mm)
  if (px == null || px < 0 || !Array.isArray(c.handles)) return frame
  const dx = px / W
  const dy = px / H
  const grow = (r: Rect): Rect => [r[0] - dx, r[1] - dy, r[2] + dx, r[3] + dy]
  const hard = c.handles.map(rect).filter((r): r is Rect => r != null).map(grow)
  const prefix = `${gidOf(leg)}.`
  const soft: Rect[] = []
  for (const t of elements) {
    if (t.role !== 'legend_text' || !gidOf(t).startsWith(prefix)) continue
    const r = visibleRect(t)
    if (r != null) soft.push(grow(r))
  }
  return { hard, soft, basis: 'entries' }
}

/** 有向矩形（mm，y 向下）：中心、阅读方向单位向量、沿阅读方向的半宽、半高（`interference._rotated_box` 同形） */
type Obb = [number, number, number, number, number, number]

/** 斜排文字（`text_block.rotation` 不是 90° 的整数倍）真正占的有向矩形；其余回 null（照布局框判） */
function rotatedBox(el: ManifestElement, W: number, H: number): Obb | null {
  if (el.clip_bbox != null) return null
  const tb = (el as { text_block?: unknown }).text_block
  if (!tb || typeof tb !== 'object' || Array.isArray(tb)) return null
  const t = tb as { rotation?: unknown; center?: unknown; w?: unknown; h?: unknown }
  const rot = num(t.rotation)
  const center = t.center
  if (rot == null || Math.abs(rot) % 90 === 0) return null
  if (!Array.isArray(center) || center.length !== 2) return null
  const cx = num(center[0])
  const cy = num(center[1])
  const w = num(t.w)
  const h = num(t.h)
  if (cx == null || cy == null || w == null || h == null || w < 0 || h < 0) return null
  const th = rot * (Math.PI / 180) // 与 CPython 的 math.radians 同一个乘法
  // 逆时针为正、y 向下：阅读方向是 (cos θ, −sin θ)
  return [cx * W, cy * H, Math.cos(th), -Math.sin(th), (w * W) / 2, (h * H) / 2]
}

function axisBox(r: Rect, W: number, H: number): Obb {
  return [((r[0] + r[2]) / 2) * W, ((r[1] + r[3]) / 2) * H, 1.0, 0.0, ((r[2] - r[0]) * W) / 2, ((r[3] - r[1]) * H) / 2]
}

/** 四条边法线上的投影都重叠 ≥ `MIN_OVERLAP_MM`（`interference._boxes_overlap`）；只作否决，结果只是布尔 */
function boxesOverlap(a: Obb, b: Obb): boolean {
  for (const box of [a, b]) {
    const ux = box[2]
    const uy = box[3]
    for (const [nx, ny] of [[ux, uy], [-uy, ux]]) {
      const spans = [a, b].map(([cx, cy, bx, by, hw, hh]) => {
        const p = cx * nx + cy * ny
        const r = hw * Math.abs(bx * nx + by * ny) + hh * Math.abs(-by * nx + bx * ny)
        return [p - r, p + r]
      })
      if (Math.min(spans[0][1], spans[1][1]) - Math.max(spans[0][0], spans[1][0]) < MIN_OVERLAP_MM) return false
    }
  }
  return true
}

/** 折线的任一线段是否穿过矩形（Liang–Barsky），与 `interference._segments_hit_rect` 同源 */
function segmentsHitRect(paths: unknown[], r: Rect): boolean {
  const [x0, y0, x1, y1] = r
  for (const path of paths) {
    const isDict = !!path && typeof path === 'object' && !Array.isArray(path)
    const pts = isDict ? (path as { points?: unknown }).points : undefined
    if (!Array.isArray(pts)) continue
    const clean: [number, number][] = []
    for (const p of pts) {
      if (Array.isArray(p) && p.length === 2) {
        const px = num(p[0])
        const py = num(p[1])
        if (px != null && py != null) clean.push([px, py])
      }
    }
    if (clean.length === 1) {
      const [px, py] = clean[0]
      if (x0 <= px && px <= x1 && y0 <= py && py <= y1) return true
    }
    for (let i = 0; i + 1 < clean.length; i++) {
      const [ax, ay] = clean[i]
      const [bx, by] = clean[i + 1]
      if (segmentInRect(ax, ay, bx, by, x0, y0, x1, y1)) return true
    }
    if ((path as { closed?: unknown }).closed && clean.length > 2) {
      const [ax, ay] = clean[clean.length - 1]
      const [bx, by] = clean[0]
      if (segmentInRect(ax, ay, bx, by, x0, y0, x1, y1)) return true
    }
  }
  return false
}

interface Found {
  id: string
  gids: string[]
  message: [string, Record<string, string>]
  detail: Record<string, unknown>
}

/**
 * 横向区间**严格**重叠的全部下标对 `[i, j]`（i < j），按 `(i, j)` 字典序——与两重循环同一个顺序，只是按左边界
 * 排序后扫描、只看左边界落在当前区间里的那些。候选对仍要过 `intersect` 的完整判定。`interference._overlapping_pairs` 同源。
 */
function overlappingPairs(rects: Rect[]): Int32Array[] {
  // 返回每个 i 的伙伴表（j > i，升序）：外层按 i、内层按 j 走，就是两重循环的顺序
  const n = rects.length
  const order = Array.from({ length: n }, (_, k) => k).sort((p, q) => rects[p][0] - rects[q][0] || p - q)
  const adj: number[][] = Array.from({ length: n }, () => [])
  for (let a = 0; a < n; a++) {
    const p = order[a]
    const x1 = rects[p][2]
    for (let b = a + 1; b < n; b++) {
      const q = order[b]
      if (rects[q][0] >= x1) break
      if (p < q) adj[p].push(q)
      else adj[q].push(p)
    }
  }
  return adj.map((l) => Int32Array.from(l).sort())
}

/** Python 的 `sorted()` 对 str 的序：码点序（gid 是 ASCII，与 UTF-16 码元序相同） */
const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/**
 * 跑一遍干涉检测，返回逐对的问题清单——与 `interference.detect(manifest, profile, panel_id=…)` 逐条相同
 * （顺序也相同：文字重叠按 gid 排序在前，然后压到别的子图，最后图例压数据）。
 */
export function detectInterference(
  manifest: Manifest,
  profile: PublicationProfile | null,
  panelId = 'figure',
  /**
   * 文字重叠 / 压到别的子图的每一对在**造条目之前**先问一次，回 false 就不造（判据、顺序不变）。
   * 与 Python `detect(…, keep=)` 同一个口子；预检折叠用它跳过对聚合结果是空操作的那些对。
   */
  keep?: (checkId: string, a: string, b: string, overlapMm2: number) => boolean,
): InterferenceIssue[] {
  const size = Array.isArray(manifest.size_mm) ? manifest.size_mm : []
  const W = num(size[0]) || 0.0
  const H = num(size[1]) || 0.0
  if (W <= 0 || H <= 0) return []
  // 同一次检测里反复出现的数（同一种字号的标签互相压着时每对都一样）只格式化一次；结果与逐个算相同
  const fixedMemo = new Map<number, string>()
  const roundMemo = new Map<number, number>()
  const fixed1 = (x: number): string => {
    let v = fixedMemo.get(x)
    if (v == null) {
      v = pyFixed(x, 1)
      fixedMemo.set(x, v)
    }
    return v
  }
  const round3 = (x: number): number => {
    let v = roundMemo.get(x)
    if (v == null) {
      v = pyRound(x, 3)
      roundMemo.set(x, v)
    }
    return v
  }
  const elements = (manifest.elements ?? []).filter(
    (e) => !!e && typeof e === 'object' && !Array.isArray(e),
  )
  const byGid = new Map<string, ManifestElement>()
  for (const e of elements) byGid.set(gidOf(e), e)
  const found: Found[] = []

  // ---- 文字互相重叠 ----
  const hidden = hiddenLegends(elements)
  const texts: [string, string, Rect][] = []
  const turnedOf = new Map<number, Obb | null>()
  for (const el of elements) {
    const gid = gidOf(el)
    if (!TEXT_ROLES.includes(el.role)) continue
    if (el.role === 'legend_text' && hidden.has(rpartition(gid)[0])) continue
    const r = visibleRect(el)
    if (r == null) continue
    texts.push([gid, el.role, r])
    turnedOf.set(texts.length - 1, rotatedBox(el, W, H))
  }
  // Python 的 `sort(key=gid)` 是稳定排序，JS 的 Array.prototype.sort 也是
  const order = texts.map((_, k) => k).sort((p, q) => byCodePoint(texts[p][0], texts[q][0]))
  const turned = order.map((k) => turnedOf.get(k) ?? null)
  const sorted = order.map((k) => texts[k])
  texts.length = 0
  texts.push(...sorted)
  // 两两比较是文字数的平方：先做便宜的相交测试，图例家族只为真相交的那几对判（与 Python 同序，结论不变）
  const fams = texts.map((t) => legendOf(t[0]))
  const partners = overlappingPairs(texts.map((t) => t[2]))
  // 热循环（几十段文字压成一团时每面板几百对）：不解构、不分配，判据与 Python 逐行相同
  for (let i = 0; i < texts.length; i++) {
    const ti = texts[i]
    const ga = ti[0]
    const a = ti[2]
    const fa = fams[i]
    const pi = partners[i]
    for (let k = 0; k < pi.length; k++) {
      const j = pi[k]
      const tj = texts[j]
      const b = tj[2]
      const ix0 = a[0] > b[0] ? a[0] : b[0]
      const iy0 = a[1] > b[1] ? a[1] : b[1]
      const ix1 = a[2] < b[2] ? a[2] : b[2]
      const iy1 = a[3] < b[3] ? a[3] : b[3]
      if (ix1 <= ix0 || iy1 <= iy0) continue
      const gb = tj[0]
      if ((fa || fams[j]) && sameLegendFamily(ga, fa, gb, fams[j])) continue
      const dxMm = (ix1 - ix0) * W
      const dyMm = (iy1 - iy0) * H
      if (dxMm < MIN_OVERLAP_MM || dyMm < MIN_OVERLAP_MM) continue
      // 斜排文字：布局框只是外接框，按真正的有向矩形复核一次，只能否决、不能新增
      const ta = turned[i]
      const tb = turned[j]
      if ((ta || tb) && !boxesOverlap(ta ?? axisBox(a, W, H), tb ?? axisBox(b, W, H))) continue
      const area = round3(dxMm * dyMm)
      if (keep && !keep(CHECK_TEXT_OVERLAP, ga, gb, area)) continue
      found.push({
        id: CHECK_TEXT_OVERLAP,
        gids: [ga, gb],
        message: ['textOverlap', { a: ga, b: gb, w: fixed1(dxMm), h: fixed1(dyMm) }],
        detail: {
          overlap_mm: [round3(dxMm), round3(dyMm)],
          overlap_mm2: area,
          certain: true,
          basis: 'bbox',
          roles: [ti[1], tj[1]],
        },
      })
    }
  }

  // ---- 装饰文字压到别的子图的绘图区 ----
  const axesBoxes: [string, Rect][] = []
  for (const el of elements) {
    if (!isTopLevelAxes(el)) continue
    const r = visibleRect(el)
    if (r != null) axesBoxes.push([gidOf(el), r])
  }
  const allAxesRect = new Map<string, Rect | null>()
  for (const e of elements) {
    if (e.role === 'axes' || e.role === 'axes3d') allAxesRect.set(gidOf(e), rect(e.bbox))
  }
  for (const [gid, role, r] of texts) {
    if (!(DECORATION_ROLES.includes(role) || role === 'legend')) continue
    const owner = ownerAxes(gid)
    const ownerRect = allAxesRect.get(owner) ?? null
    for (const [axGid, box] of axesBoxes) {
      if (axGid === owner) continue
      // 自己的插图 / 自己被包在别人里：包含关系不是干涉
      if (ownerRect != null && (contains(box, ownerRect) || contains(ownerRect, box))) continue
      const hit = intersect(r, box)
      if (hit == null) continue
      const dxMm = (hit[2] - hit[0]) * W
      const dyMm = (hit[3] - hit[1]) * H
      if (dxMm < MIN_OVERLAP_MM || dyMm < MIN_OVERLAP_MM) continue
      const area = round3(dxMm * dyMm)
      if (keep && !keep(CHECK_TEXT_OVER_AXES, gid, axGid, area)) continue
      found.push({
        id: CHECK_TEXT_OVER_AXES,
        gids: [gid, axGid],
        message: ['textOverAxes', { a: gid, b: axGid, w: fixed1(dxMm), h: fixed1(dyMm) }],
        detail: {
          overlap_mm: [round3(dxMm), round3(dyMm)],
          overlap_mm2: area,
          certain: true,
          basis: 'bbox',
          roles: [role, 'axes'],
        },
      })
    }
  }

  // ---- 图例压住同一子图里的数据 ----
  // 先按所属子图把数据分好组、把条目文字挑出来（各一遍，原顺序不变）——与 Python 同
  const dataByOwner = new Map<string, ManifestElement[]>()
  for (const d of elements) {
    if (!DATA_ROLES.includes(d.role)) continue
    const o = ownerAxes(gidOf(d))
    const list = dataByOwner.get(o)
    if (list) list.push(d)
    else dataByOwner.set(o, [d])
  }
  const legendTexts = elements.filter((t) => t.role === 'legend_text')
  for (const el of elements) {
    if (el.role !== 'legend') continue
    const legGid = gidOf(el)
    const legRect = visibleRect(el)
    if (legRect == null) continue
    const owner = ownerAxes(legGid)
    if (!owner) continue // figure 级图例：它压谁是排版决定，这里不猜
    const ownerRect = allAxesRect.get(owner) ?? null
    if (ownerRect != null && intersect(legRect, ownerRect) == null) continue // 放在坐标区外：压不到数据
    const { hard, soft, basis: legendBasis } = legendCoverRects(el, legendTexts, W, H)
    const covers = [...hard, ...soft]
    const certainHits: string[] = []
    const riskHits: string[] = []
    for (const d of dataByOwner.get(owner) ?? []) {
      const dg = gidOf(d)
      const dr = visibleRect(d)
      if (dr == null || !covers.some((c) => intersect(dr, c) != null)) continue
      if (RECT_BASIS_ROLES.includes(d.role)) {
        // 画出来的就是这个矩形：压到框 / 示意即确定；只擦到条目文字的框是可能
        if (hard.some((c) => intersect(dr, c) != null)) certainHits.push(dg)
        else riskHits.push(dg)
        continue
      }
      let geom: unknown = d.geometry
      if (BBOX_BASIS_ROLES.includes(d.role)) geom = undefined
      const paths =
        geom && typeof geom === 'object' && !Array.isArray(geom) ? (geom as { paths?: unknown }).paths : undefined
      if (Array.isArray(paths)) {
        if (hard.some((c) => segmentsHitRect(paths, c))) certainHits.push(dg)
        else if (soft.some((c) => segmentsHitRect(paths, c))) riskHits.push(dg)
      } else {
        riskHits.push(dg)
      }
    }
    if (!certainHits.length && !riskHits.length) continue
    const hits = certainHits.length ? certainHits : riskHits
    const certain = certainHits.length > 0
    found.push({
      id: CHECK_LEGEND_OVER_DATA,
      gids: [legGid, ...hits],
      message: [
        certain ? 'legendOverData' : 'legendOverDataRisk',
        { legend: legGid, targets: hits.slice(0, 4).join('、'), count: String(hits.length) },
      ],
      detail: {
        certain,
        basis: certain ? 'geometry' : 'bbox',
        targets: hits,
        risk_only: certain ? riskHits : [],
        legend_basis: legendBasis,
        legend_rect_mm: [
          pyRound(legRect[0] * W, 3),
          pyRound(legRect[1] * H, 3),
          pyRound((legRect[2] - legRect[0]) * W, 3),
          pyRound((legRect[3] - legRect[1]) * H, 3),
        ],
      },
    })
  }

  // 每条都是这里新造的对象，直接交出去（不再整份复制一遍）；严重度按检查 id 只查一次
  const severity = new Map<string, Severity>()
  return found.map((it) => {
    let sev = severity.get(it.id)
    if (sev == null) {
      sev = profile ? severityOf(profile, it.id) : DEFAULT_SEVERITY
      severity.set(it.id, sev)
    }
    return {
      id: it.id,
      severity: sev,
      message: { key: it.message[0], params: it.message[1] },
      object_ids: [panelId],
      gids: it.gids,
      detail: it.detail,
    }
  })
}

/**
 * 折进预检时「确定」压住数据的图例恒排在「只是风险」前面（`preflight.INTERFERENCE_CERTAIN_RANK`，同值）。
 * 排名 = 命中数 + 这一档；不加的话 3 个风险目标会压过 1 个确定目标，聚合项就说成「可能」。
 */
export const CERTAIN_RANK = 1_000_000
