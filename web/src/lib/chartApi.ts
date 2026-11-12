// SVG 计算图表的服务调用（ADR 0181，SG10）。判据在后端（chartmodel/chartbind/
// svgcompose）；这里只做形状化的请求/响应与稳定错误码透传，不做第二份判定。
import type { ApiError, Manifest } from './api'
import type { ComputedRenderFrame } from '@/lib/chartFrame'
import { apiUrl, withProject } from '@/lib/session'
import type { ChartBinding, ChartModel } from '../types/chartModel'
import type { ChartSourceRef, PanelChartState, PanelOverride } from '../types/document'

export interface ChartComputeOptions {
  render_profile: 'native-manifest/1' | 'native-manifest/2'
  overrides: PanelOverride[]
  /** 重新绑定时由后端核对编辑的科学所有权；前端不按 gid 猜迁移。 */
  previous_chart?: PanelChartState | null
  chart_source?: ChartSourceRef
}

export interface SceneInventory {
  scene_digest: string
  w_pt: number
  h_pt: number
  view_box: number[] | null
  nodes: { id: string, tag: string, text?: string, fill?: string, stroke?: string }[]
  truncated: boolean
  /** 后端已验证完整冷重放的自包含 SVG；只预填草稿，确认前不写排版。 */
  restored_chart?: PanelChartState
  /** 仅 v2 完整编辑包提供，模型仍与渲染编辑状态分开。 */
  restored_render?: Pick<ChartComputeOptions, 'render_profile' | 'overrides'>
}

export interface ChartAxisEvidence {
  p1_user: number
  p1_value: number
  p2_user: number
  p2_value: number
  scale: string
}

export interface ChartBindLegendEntryRequest {
  index: number
  label: string
  /** 跟随系列的场景节点（后端翻译成 series id）；缺席 = custom。 */
  series_node?: string
}

export interface ChartBindLegendRequest {
  id: string
  node: string
  rect: [number, number, number, number]
  entries: ChartBindLegendEntryRequest[]
}

interface ChartBindBaseRequest {
  file: string
  chart_source?: ChartSourceRef
  axes_id: string
  rect: [number, number, number, number]
  axes_nodes?: string[]
  y?: ChartAxisEvidence
  series: {
    node: string
    x_col?: string
    y_col?: string
    yerr_col?: string
    cbar_node?: string
    kind?: string
    style?: Record<string, string | number>
  }[]
  dataset_text?: string
  grid_text?: string
  completeness?: string
  /** N4 图例合同：有才发（缺省请求身份逐字节不变）。 */
  legend?: ChartBindLegendRequest[]
}

export type ChartBindRequest = ChartBindBaseRequest & (
  | { x: ChartAxisEvidence, x_kind?: never, categories_col?: never }
  | { x?: never, x_kind: 'categorical', categories_col: string }
)

export interface ChartComputeResult {
  svg: string
  facts: {
    series_points?: Record<string, { x: number, y: number }[]>
    ticks?: unknown
  }
  scene_digest: string
  /** 完整渲染身份（来源+模型；后端 panel_identity） */
  render_revision: string
  model_digest: string
  binding_digest: string
  engine: { matplotlib: string, numpy: string, tavotto: string }
  /** native-manifest/2 才有：这次实际 draw 的外框声明（含在 manifest 内，顶层是稳定出口）。 */
  render_frame?: ComputedRenderFrame
  /** 引擎算出 manifest 时就有（native-manifest/1、/2 都发）；老引擎不发。 */
  manifest?: Manifest
}

/** 稳定错误码从响应体透传（后端 ERROR_CODES / BindError / SceneError 闭集）。 */
async function readError(res: Response): Promise<ApiError & { code?: string }> {
  let payload: Record<string, unknown> = {}
  try {
    payload = (await res.json()) as Record<string, unknown>
  } catch {
    /* 非 JSON 错误体，保留状态码 */
  }
  const message = typeof payload?.error === 'string' ? payload.error : `HTTP ${res.status}`
  return Object.assign(new Error(message), {
    status: res.status,
    code: typeof payload?.code === 'string' ? payload.code : undefined,
  }) as ApiError & { code?: string }
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(apiUrl(url), withProject({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }))
  if (!res.ok) throw await readError(res)
  return res.json() as Promise<T>
}

/** 只读场景盘点（同一份字节重新校验 + 稳定节点清单）。 */
export async function chartInspect(file: string, source?: ChartSourceRef): Promise<SceneInventory> {
  const ref = source === undefined ? '' : `&chart_source=${encodeURIComponent(JSON.stringify(source))}`
  const res = await fetch(
    apiUrl(`/api/svg/chart/inspect?id=${encodeURIComponent(file)}${ref}`),
    withProject(),
  )
  if (!res.ok) throw await readError(res)
  return res.json() as Promise<SceneInventory>
}

/** 显式建模确认：校准证据 + 系列选择 → 模型 + 绑定（后端判据，未落盘）。 */
export function chartBind(request: ChartBindRequest): Promise<PanelChartState> {
  return postJson('/api/svg/chart/bind', request)
}

/** 按模型重算绑定区域 → 组合后的场景 SVG + 事实。 */
export function chartCompute(
  file: string,
  chart: PanelChartState,
  opts?: ChartComputeOptions,
): Promise<ChartComputeResult> {
  return postJson('/api/svg/chart/compute', {
    file,
    model: chart.model as unknown as ChartModel,
    binding: chart.binding as unknown as ChartBinding,
    ...(opts ? { render_profile: opts.render_profile, overrides: opts.overrides,
      ...(opts.previous_chart !== undefined ? { previous_chart: opts.previous_chart } : {}),
      ...(opts.chart_source !== undefined ? { chart_source: structuredClone(opts.chart_source) } : {}),
    } : {}),
  })
}
