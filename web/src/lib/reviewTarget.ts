import { fetchPanelStem, type ReviewTarget } from '@/lib/api'

/**
 * 「分享审阅」此刻分享的是什么（ADR 0187）：单图编辑 = 那张图（引擎认图的 stem），否则 = 当前这份排版（画布 id）。
 * 空画布没有东西可分享 → null。stem 要问引擎（与导出同一条 `/api/panel-stem`），所以是异步的。
 */
export async function resolveReviewTarget(s: {
  /** 单图编辑时那张图（面板的 fileId 与 runtime 来源的 stem）；排版给 null */
  panel: { fileId: string; stem?: string } | null
  hasObjects: boolean
  canvasId: string
}): Promise<ReviewTarget | null> {
  if (s.panel) {
    const stem = s.panel.stem ?? (await fetchPanelStem(s.panel.fileId).then((r) => r.stem).catch(() => null))
    return stem ? { kind: 'figure', id: stem } : null
  }
  return s.hasObjects ? { kind: 'layout', id: s.canvasId } : null
}

export const sameTarget = (a: ReviewTarget | null, b: ReviewTarget | null): boolean =>
  a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id)
