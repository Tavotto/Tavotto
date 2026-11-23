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

const DECORATION_ROLES = ['title', 'axis_label', 'ticklabel', 'legend_text']
const TEXT_ROLES = [...DECORATION_ROLES, 'text', 'legend']
const DATA_ROLES = [
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
/** 有 geometry 也按包围盒（只标风险）判的角色——`interference.BBOX_BASIS_ROLES` */
const BBOX_BASIS_ROLES = ['errorbar', 'stem_series']

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
): InterferenceIssue[] {
  const size = Array.isArray(manifest.size_mm) ? manifest.size_mm : []
  const W = num(size[0]) || 0.0
  const H = num(size[1]) || 0.0
  if (W <= 0 || H <= 0) return []
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
  for (let i = 0; i < texts.length; i++) {
    const [ga, ra, a] = texts[i]
    for (let j = i + 1; j < texts.length; j++) {
      const [gb, rb, b] = texts[j]
      const hit = intersect(a, b)
      if (hit == null) continue
      if (sameLegendFamily(ga, fams[i], gb, fams[j])) continue
      const dxMm = (hit[2] - hit[0]) * W
      const dyMm = (hit[3] - hit[1]) * H
      if (dxMm < MIN_OVERLAP_MM || dyMm < MIN_OVERLAP_MM) continue
      // 斜排文字：布局框只是外接框，按真正的有向矩形复核一次，只能否决、不能新增
      const ta = turned[i]
      const tb = turned[j]
      if ((ta || tb) && !boxesOverlap(ta ?? axisBox(a, W, H), tb ?? axisBox(b, W, H))) continue
      found.push({
        id: CHECK_TEXT_OVERLAP,
        gids: [ga, gb],
        message: ['textOverlap', { a: ga, b: gb, w: pyFixed(dxMm, 1), h: pyFixed(dyMm, 1) }],
        detail: {
          overlap_mm: [pyRound(dxMm, 3), pyRound(dyMm, 3)],
          overlap_mm2: pyRound(dxMm * dyMm, 3),
          certain: true,
          basis: 'bbox',
          roles: [ra, rb],
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
      found.push({
        id: CHECK_TEXT_OVER_AXES,
        gids: [gid, axGid],
        message: ['textOverAxes', { a: gid, b: axGid, w: pyFixed(dxMm, 1), h: pyFixed(dyMm, 1) }],
        detail: {
          overlap_mm: [pyRound(dxMm, 3), pyRound(dyMm, 3)],
          overlap_mm2: pyRound(dxMm * dyMm, 3),
          certain: true,
          basis: 'bbox',
          roles: [role, 'axes'],
        },
      })
    }
  }

  // ---- 图例压住同一子图里的数据 ----
  for (const el of elements) {
    if (el.role !== 'legend') continue
    const legGid = gidOf(el)
    const legRect = visibleRect(el)
    if (legRect == null) continue
    const owner = ownerAxes(legGid)
    if (!owner) continue // figure 级图例：它压谁是排版决定，这里不猜
    const certainHits: string[] = []
    const riskHits: string[] = []
    for (const d of elements) {
      const dg = gidOf(d)
      if (!DATA_ROLES.includes(d.role) || ownerAxes(dg) !== owner) continue
      const dr = visibleRect(d)
      if (dr == null || intersect(dr, legRect) == null) continue
      let geom: unknown = d.geometry
      if (BBOX_BASIS_ROLES.includes(d.role)) geom = undefined
      const paths =
        geom && typeof geom === 'object' && !Array.isArray(geom) ? (geom as { paths?: unknown }).paths : undefined
      if (Array.isArray(paths)) {
        if (segmentsHitRect(paths, legRect)) certainHits.push(dg)
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
        legend_rect_mm: [
          pyRound(legRect[0] * W, 3),
          pyRound(legRect[1] * H, 3),
          pyRound((legRect[2] - legRect[0]) * W, 3),
          pyRound((legRect[3] - legRect[1]) * H, 3),
        ],
      },
    })
  }

  return found.map((it) => ({
    id: it.id,
    severity: profile ? severityOf(profile, it.id) : DEFAULT_SEVERITY,
    message: { key: it.message[0], params: { ...it.message[1] } },
    object_ids: [panelId],
    gids: [...it.gids],
    detail: { ...it.detail },
  }))
}

/**
 * 折进预检时「确定」压住数据的图例恒排在「只是风险」前面（`preflight.INTERFERENCE_CERTAIN_RANK`，同值）。
 * 排名 = 命中数 + 这一档；不加的话 3 个风险目标会压过 1 个确定目标，聚合项就说成「可能」。
 */
export const CERTAIN_RANK = 1_000_000
