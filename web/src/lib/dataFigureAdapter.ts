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

/** 响应里的候选 → 界面用的候选（地址钉在发起那一刻的项目上；就绪状态在 store 里按张记） */
export function candidatesFromResponse(items: AiDataProposeItem[], pj: string | null): Candidate[] {
  return items.map((it) => ({ ...it, thumb: aiDataThumbnailUrl(it.thumbnail_url, pj) }))
}

/** 右栏三个下拉的数据：这次用了哪一列，以及每个角色可换成的列 */
export interface ColumnRoles {
  /** 有序横轴；null = 这次没有横轴 */
  x: string | null
  /** 纵轴（后端给数组：宽表可能把 2–3 列当作纵轴；界面先按第一个显示） */
  y: string | null
  /** 额外有几列当纵轴（界面不显示，只用来在汇报里说清） */
  yExtra: number
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
