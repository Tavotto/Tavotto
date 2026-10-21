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
  return JSON.stringify([input.scope, formats, facts])
}

interface RouteState {
  key: string | null
  route: ExportRoute | null
  /** 当前取回的 route 若与 `key` 一致才可用 */
  routeFor: (key: string) => ExportRoute | null
  refresh: (input: ExportRequestInput) => Promise<void>
  clear: () => void
}

let seq = 0

export const useExportRouteStore = create<RouteState>((set, get) => ({
  key: null,
  route: null,
  routeFor: (key) => (get().key === key ? get().route : null),
  refresh: async (input) => {
    const key = routeKeyOf(input)
    const mine = ++seq
    try {
      const built = buildExportRequest({ ...input, filename: 'route', route: null })
      // route 要回答「用户勾的格式各自怎么走」，包括此刻因 EPS 不可用而被请求构造器丢掉的那一个
      const formats = FORMAT_ORDER.filter((f) => input.formats.includes(f))
      const checked = await validateExport({ ...built.request, formats } as typeof built.request)
      if (mine !== seq) return
      set({ key, route: checked.ok && checked.route ? checked.route : null })
    } catch {
      // 拿不到答案 ≠ 有答案：清掉旧的，消费者走退路
      if (mine === seq) set({ key, route: null })
    }
  },
  clear: () => {
    seq++
    set({ key: null, route: null })
  },
}))
