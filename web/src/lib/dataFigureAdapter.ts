/**
 * 「从数据到图」前端与后端接口之间的薄适配层（后端合同：`propose(render="lazy")` + 取图时才画缩略图，ADR 0178 2026-11-16 修订）。
 *
 * 懒模式：`propose` 一返回就带着每张候选的名字 / 说明 / 推荐（文字先到），缩略图地址在手但图还没画——`<img>` 一取就由后端当场画，
 * 画好返回 200，画不出返回 422（`propose_thumbnail_failed`，不需要文案）。所以每张缩略图各自有就绪状态：
 * `pending`（骨架，`<img>` 在取）→ `ready`（到了，淡入）/ `failed`（这张静默去掉，推荐标记挪给剩下的第一张）。
 * 响应还说清这次用了什么：列角色（`roles`，含下拉的 `options`）、表头行（`header_row`）、非数字格（`mixed_cells`）——界面只认这里的形状。
 */
import { aiDataThumbnailUrl, type AiDataProposeItem, type AiDataProposeResponse } from '@/lib/api'

export type ThumbState = 'pending' | 'ready' | 'failed'

export interface Candidate extends AiDataProposeItem {
  /** 带项目标识的缩略图地址（`<img>` 发不了项目请求头） */
  thumb: string
}

/**
 * 一张候选的身份：`title_code` + `variant`（kind；`split` 还要比 `column`）。合并作图成对的两张 `title_code` / `why_code` 故意相同，
 * 光比 `title_code` 会把「只按文件比较」认成「再分开」。会话过期 / 数据变了、刷新候选后按画法找回「原来点的那张」时只许用它；
 * 单份流程没有 `variant`，与只比 `title_code` 等价。
 */
export function candidateIdentity(c: Pick<AiDataProposeItem, 'title_code' | 'variant'>): string {
  const v = c.variant
  return JSON.stringify([c.title_code, v?.kind ?? null, v?.kind === 'split' ? v.column : null])
}

/**
 * 两张候选**画的是不是同一张图**：同一画法身份之外，配方（族 / 横纵轴 / 分组 / 分面 / 个体 / 各层）与误差线（种类、来源、用哪几列）也逐项相同。
 * 会话过期 / 数据变了、刷新候选后静默接着画只许在这一条成立时：身份相同但列或分组换了（数据变了以后规则改选了别的列、
 * 合画成对的一边被剔除后另一边落到同一个身份上）就是另一张图，要回到候选让用户看过新的再点。只比数据之外的配方，
 * 数值跟着数据变本来就是脚本的事（脚本每次都现读数据）。
 */
export function sameDrawing(a: AiDataProposeItem, b: AiDataProposeItem): boolean {
  const of = (c: AiDataProposeItem) => JSON.stringify([candidateIdentity(c), c.values, c.error ?? null, c.error_source ?? null, c.error_column ?? null, c.error_columns ?? null])
  return of(a) === of(b)
}

/** 响应里的候选 → 界面用的候选（地址钉在发起那一刻的项目上；就绪状态在 store 里按张记） */
export function candidatesFromResponse(items: AiDataProposeItem[], pj: string | null): Candidate[] {
  return items.map((it) => ({ ...it, thumb: aiDataThumbnailUrl(it.thumbnail_url, pj) }))
}

/** 右栏三个下拉的数据：这次用了哪一列，以及每个角色可换成的列 */
export interface ColumnRoles {
  /** 有序横轴；null = 这次没有横轴 */
  x: string | null
  /** 纵轴的第一列（下拉的取值） */
  y: string | null
  /** 纵轴的全部列（后端给数组：宽表 2–3 列各一条线；`stack` 时是被当分组的 2–8 列），顺序有意义；没有 = 空数组 */
  yAll: string[]
  /** 额外有几列当纵轴 */
  yExtra: number
  /** 「把列当分组」：此时 `x` / `group` 为 null，`yAll` 是那几列 */
  stack: boolean
  /** 分组；null = 不分组 */
  group: string | null
  options: { x: string[]; y: string[]; group: string[] }
}
export function rolesFromResponse(res: AiDataProposeResponse): ColumnRoles | null {
  if (res.kind === 'ask') return null
  const r = res.roles
  if (!r) return null
  return {
    x: r.x ?? null,
    y: r.y?.[0] ?? null,
    yAll: [...(r.y ?? [])],
    stack: r.stack === true,
    yExtra: Math.max(0, (r.y?.length ?? 0) - 1),
    group: r.group ?? null,
    options: { x: r.options?.x ?? [], y: r.options?.y ?? [], group: r.options?.group ?? [] },
  }
}

/** 数值列里夹着文字的列（默认不画那几行）；没有 = 空数组 */
export interface MixedColumn {
  column: string
  nonNumeric: number
  mode: 'skip' | 'extract'
}
export function mixedFromResponse(res: AiDataProposeResponse): MixedColumn[] {
  if (res.kind === 'ask') return []
  return (res.mixed_cells ?? []).map((m) => ({ column: m.column, nonNumeric: m.non_numeric, mode: m.mode }))
}

/** 这次实际用的表头物理行号（XLSX / 没给 = null） */
export function headerRowFromResponse(res: AiDataProposeResponse): number | null {
  return res.kind === 'ask' ? null : (res.header_row ?? null)
}

export interface GroupSource {
  path: string
  sheet: string | null
  label: string
  headerRow: number | null
  unitsRow: boolean
}

/** 合画响应里每份文件的显示名与各自的表头行 / 单位行；单份 / 没给 = null（前端按路径自己取名） */
export function sourcesFromResponse(res: AiDataProposeResponse): GroupSource[] | null {
  if (res.kind === 'ask' || !Array.isArray(res.sources)) return null
  return res.sources.map((s) => ({ path: s.path, sheet: s.sheet ?? null, label: s.label, headerRow: s.header_row ?? null, unitsRow: s.units_row === true }))
}

/** 表头后紧跟的单位行（`header_row + 1` 那一行）已被后端切掉：数据条里把它显示成次要文字色、不参与数据行；字段缺失按 false */
export function unitsRowFromResponse(res: AiDataProposeResponse): boolean {
  return res.kind === 'ask' ? false : res.units_row === true
}

/**
 * 主图此刻预览的那一张：`previewKey`（画法身份）对得上、且图已到的那张；对不上（还没选过 / 重新出候选后这个画法没了 / 图没到）= 第一张。
 * 取不到图的（`failed`）不在候选里。没有候选 = null。
 */
export function previewOf(candidates: Candidate[], thumbs: Record<string, ThumbState>, previewKey: string | null): Candidate | null {
  const visible = candidates.filter((c) => thumbs[c.id] !== 'failed')
  const hit = previewKey ? visible.find((c) => candidateIdentity(c) === previewKey && thumbs[c.id] === 'ready') : undefined
  return hit ?? visible[0] ?? null
}
