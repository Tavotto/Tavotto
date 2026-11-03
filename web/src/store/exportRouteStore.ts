/**
 * 导出来源（route）的前端持有者（#1209 / A2b）。
 *
 * 「这次导出这张图用哪份来源」的权威在后端（`engine/exportroute.py`），经 `/api/export/validate` 的
 * `route` 发布。这里只做三件事：按输入算一个**键**、去问后端、把答案连同它回答的键存下来。
 * 消费者拿「当前输入的键」来取——键对不上（编辑了 override、切了范围 / 格式）就当没有 route，
 * 走 `lib/exportRoute` 的退路，**绝不把上一次的答案当这一次的**。
 *
 * 和 `exportStore` 分开放：那边是导出作业的生命周期，route 只是一条附加事实，失败不影响任何东西
 * （拿不到 = 没有 route，界面按退路，与引入本模块之前的行为一致）。
 */
import { create } from 'zustand'
import { validateExport } from '@/lib/api'
import { originalArtifactOverrides } from '@/lib/artifactValidation'
import { buildExportRequest, type ExportRequestInput } from '@/lib/exportRequest'
import type { ExportRoute } from '@/lib/exportRoute'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId } from '@/lib/session'
import type { PanelObject } from '@/types/document'

const FORMAT_ORDER = ['pdf', 'png', 'eps', 'tiff', 'svg']

/** 这次输入下 route 的键：范围 + 用户勾的格式（含被禁用的 EPS）+ 影响来源的面板事实（编辑 / 快照）。 */
export function routeKeyOf(
  input: Pick<ExportRequestInput, 'scope' | 'formats' | 'doc' | 'panel' | 'figureId'>,
): string {
  const formats = FORMAT_ORDER.filter((f) => input.formats.includes(f))
  const panelFacts = (p: PanelObject, overrides: unknown) => [
    p.fileId,
    overrides,
    p.hidden ? 1 : 0,
    p.figureSnapshot ?? null,
  ]
  let facts: unknown
  if (input.scope === 'canvas') {
    facts = input.doc.objects
      .filter((o): o is PanelObject => o.type === 'panel')
      .map((p) => panelFacts(p, p.overrides))
  } else {
    let sent: unknown = 'invalid'
    try {
      sent = input.panel ? originalArtifactOverrides(input.panel) : []
    } catch {
      /* 守卫无效：本次导出会被拒，键里记一个固定值 */
    }
    facts = [input.figureId ?? '', input.panel ? panelFacts(input.panel, sent) : null]
  }
  // 项目 id 进键：同名文件、同编辑、同格式，在不同项目里脚本登记不同，route 不同
  return JSON.stringify([currentProjectId(), input.scope, formats, facts])
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
  /** 当前取回的 route 若与 `key` 一致才可用 */
  routeFor: (key: string) => ExportRoute | null
  refresh: (input: ExportRequestInput) => Promise<void>
  clear: () => void
  /** 作废当前答案并让在飞的请求回来不落地（重开对话框时：新答案到之前不显示旧答案） */
  invalidate: () => void
}

let seq = 0

export const useExportRouteStore = create<RouteState>((set, get) => ({
  key: null,
  route: null,
  routeFor: (key) => (get().key === key ? get().route : null),
  refresh: async (input) => {
    const key = routeKeyOf(input)
    const mine = ++seq
    // 发起时的项目 / 代际：A 的迟到响应不落进 B（换项目时 `clear()` 也会推进 seq）
    const guard = captureProjectEpoch()
    try {
      const route = await fetchExportRoute(input)
      if (mine !== seq || !guard.still()) return
      set({ key, route })
    } catch {
      // 拿不到答案 ≠ 有答案：清掉旧的，消费者走退路
      if (mine === seq && guard.still()) set({ key, route: null })
    }
  },
  /** 换项目（`projectStore.resetForNewProject`）：丢掉旧项目的答案，在飞的请求回来不落地 */
  clear: () => {
    seq++
    set({ key: null, route: null })
  },
  invalidate: () => {
    seq++
    set({ key: null, route: null })
  },
}))
