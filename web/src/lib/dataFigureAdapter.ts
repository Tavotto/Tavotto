/**
 * 「从数据到图」前端与后端接口之间的薄适配层。
 *
 * 现在的后端一次响应（`propose`）里同时带回画法的文字与缩略图地址（缩略图在这次请求里已经渲染好落盘），
 * 列的角色（横轴 / 纵轴 / 分组）还没有。后端「文字先到、缩略图逐张补、返回列角色」的合同出来后，只换这个文件：
 * 候选的每张缩略图各自有 `pending` / `ready` / `failed`，列角色从响应里读。界面只认这里的形状。
 */
import { aiDataThumbnailUrl, type AiDataProposeItem, type AiDataProposeResponse } from '@/lib/api'

/** 一张缩略图的就绪状态：`pending` = 文字先到、图还没好（骨架）；`ready` = 有地址可以取；`failed` = 这张没渲出来 */
export type ThumbState = 'pending' | 'ready' | 'failed'

export interface Candidate extends AiDataProposeItem {
  /** 带项目标识的缩略图地址（`<img>` 发不了项目请求头）；`pending` / `failed` 时是 null */
  thumb: string | null
  thumbState: ThumbState
}

/** 响应里的候选 → 界面用的候选。现接口：地址在手 = 就绪（图本身由 `<img>` 加载，加载失败由缩略图组件自己兜） */
export function candidatesFromResponse(items: AiDataProposeItem[], pj: string | null): Candidate[] {
  return items.map((it) => ({ ...it, thumb: aiDataThumbnailUrl(it.thumbnail_url, pj), thumbState: 'ready' as const }))
}

/** 这份数据被当成哪几列画：后端合同到了之后从响应里读；现在没有，界面只显示文件名与行列数 */
export interface ColumnRoles {
  x: string | null
  y: string | null
  g: string | null
}
export function rolesFromResponse(_res: AiDataProposeResponse): ColumnRoles | null {
  return null
}
