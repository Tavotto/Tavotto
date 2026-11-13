/**
 * 导出来源（route）的前端持有者（#1209 / A2b）。
 *
 * 「这次导出这张图用哪份来源」的权威在后端（`engine/exportroute.py`），经 `/api/export/validate` 的
 * `route` 发布。来源计划按含formats的精确键读取；EPS offer只存how/reason的投影，
 * 按同一份完整来源事实（不含formats）读取，避免勾EPS反过来撤销其已确认offer。
 * 编辑/引用/范围/项目变更均作废对应答案；不把旧格式的整份计划伪装成新计划。
 *
 * 和 `exportStore` 分开放：那边是导出作业的生命周期，route 只是一条附加事实，失败不影响任何东西
 * （拿不到 = 没有 route，界面按退路，与引入本模块之前的行为一致）。
 */
import { create } from 'zustand'
import { validateExport } from '@/lib/api'
import { originalArtifactOverrides } from '@/lib/artifactValidation'
import { chartRequestIdentity } from '@/lib/chartSource'
import { buildExportRequest, type ExportRequestInput } from '@/lib/exportRequest'
import { exportOffersOf, type ExportOffers, type ExportRoute } from '@/lib/exportRoute'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId } from '@/lib/session'
import type { PanelObject } from '@/types/document'

const FORMAT_ORDER = ['pdf', 'png', 'eps', 'tiff', 'svg']

type SourceFactsInput = Pick<ExportRequestInput, 'scope' | 'doc' | 'panel' | 'figureId'>

/** 来源事实只有这一份提取，格式计划与offer分别消费它。 */
function sourceFactsOf(input: SourceFactsInput) {
  const panelFacts = (p: PanelObject, overrides: unknown, sendsChart = true) => {
    const facts = [p.fileId, overrides, p.hidden ? 1 : 0, p.figureSnapshot ?? null]
    const chart = sendsChart ? chartRequestIdentity(p.chart, p.chartSource, p.chartFrame?.renderProfile) : undefined
    // 无 chart/ref 时旧 key 逐字不变；复用请求身份，保留在场但畸形的 ref 让后端拒绝。
    if (chart !== undefined) facts.push(chart)
    return facts
  }
  let facts: unknown
  if (input.scope === 'canvas') {
    facts = input.doc.objects
      .filter((o): o is PanelObject => o.type === 'panel')
      .map((p) => panelFacts(p, p.overrides))
  } else {
    let sent: unknown = 'invalid'
    const panel = input.panel
    try {
      sent = panel && panel.fileId === input.figureId && panel.chart
        ? panel.overrides
        : panel ? originalArtifactOverrides(panel) : []
    } catch {
      /* 守卫无效：本次导出会被拒，键里记一个固定值 */
    }
    facts = [input.figureId ?? '', input.panel ? panelFacts(input.panel, sent, input.panel.fileId === input.figureId) : null]
  }
  // 项目 id 进键：同名文件、同编辑、同格式，在不同项目里脚本登记不同，route 不同
  return [currentProjectId(), input.scope, facts] as const
}

/** 逐格式来源计划：保留旧key字节，只能匹配同一组formats。 */
export function routeKeyOf(input: SourceFactsInput & Pick<ExportRequestInput, 'formats'>): string {
  const [project, scope, facts] = sourceFactsOf(input)
  const formats = FORMAT_ORDER.filter((f) => input.formats.includes(f))
  return JSON.stringify([project, scope, formats, facts])
}

/** EPS是否可尝试与勾选无关；scope/项目/完整来源事实变更仍使已知offer失效。 */
export function offerKeyOf(input: SourceFactsInput): string {
  return JSON.stringify(sourceFactsOf(input))
}

/**
 * 向后端要这次输入的 route（不落进 store 的那个单槽）。「每张图单独导出」冻结前要给每张图各取一份，
 * 不能用单槽——对话框自己的 `refresh` 会把在飞的请求作废（seq）。拿不到 / 失败 = `null`（消费者走退路）。
 */
export async function fetchExportRoute(input: ExportRequestInput): Promise<ExportRoute | null> {
  const built = buildExportRequest({ ...input, filename: 'route', route: null })
  // route 要回答「用户勾的格式各自怎么走」，包括此刻因 EPS 不可用而被请求构造器丢掉的那一个
  const formats = FORMAT_ORDER.filter((f) => input.formats.includes(f))
  const checked = await validateExport({ ...built.request, formats } as typeof built.request)
  return checked.ok && checked.route ? checked.route : null
}

interface RouteState {
  key: string | null
  route: ExportRoute | null
  offerKey: string | null
  offers: ExportOffers | null
  /** 当前取回的 route 若与 `key` 一致才可用 */
  routeFor: (key: string) => ExportRoute | null
  offersFor: (key: string) => ExportOffers | null
  refresh: (input: ExportRequestInput) => Promise<void>
  clear: () => void
  /** 作废当前答案并让在飞的请求回来不落地（重开对话框时：新答案到之前不显示旧答案） */
  invalidate: () => void
}

let seq = 0

export const useExportRouteStore = create<RouteState>((set, get) => ({
  key: null,
  route: null,
  offerKey: null,
  offers: null,
  routeFor: (key) => (get().key === key ? get().route : null),
  offersFor: (key) => (get().offerKey === key ? get().offers : null),
  refresh: async (input) => {
    const key = routeKeyOf(input)
    const offerKey = offerKeyOf(input)
    const mine = ++seq
    // 发起时的项目 / 代际：A 的迟到响应不落进 B（换项目时 `clear()` 也会推进 seq）
    const guard = captureProjectEpoch()
    try {
      const route = await fetchExportRoute(input)
      if (mine !== seq || !guard.still()) return
      set({ key, route, offerKey, offers: route ? exportOffersOf(route) : null })
    } catch {
      // 拿不到答案 ≠ 有答案：清掉旧的，消费者走退路
      if (mine === seq && guard.still()) set({ key, route: null, offerKey, offers: null })
    }
  },
  /** 换项目（`projectStore.resetForNewProject`）：丢掉旧项目的答案，在飞的请求回来不落地 */
  clear: () => {
    seq++
    set({ key: null, route: null, offerKey: null, offers: null })
  },
  invalidate: () => {
    seq++
    set({ key: null, route: null, offerKey: null, offers: null })
  },
}))
