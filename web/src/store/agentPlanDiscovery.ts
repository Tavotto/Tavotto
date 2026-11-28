/**
 * 窗口里的 Agent 待办卡（ADR 0217 §五、0214 3b）：插件里绑定了排版子图的 Agent 会话出的建议方案，窗口里开着对得上的那张图时，
 * 在助手面板出同一张建议待办卡。不推送（打包版桌面只认受限令牌、不收插件主动发来的请求），只轮询：与「并进来」同一个节奏
 * （窗口回到前台 + 每 `AGENT_WATCH_INTERVAL_MS`）。
 *
 * 对得上 = 方案的 `target`（桌面口径的 `fileId`）是当前画布上的一张可改的图，且这张图此刻的修改与方案的基线一致
 * （`samePatches`，与卡片「图已改过」同一个判据）——对不上的方案不出卡（Agent 那边会话读到的是另一份底子）。
 */
import { EngineError, getChangePlan, listAgentChangePlans, type AgentPlanRef, type ChangePlan } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { refuseArtifactOperation } from '@/lib/artifactValidation'
import { AGENT_WATCH_INTERVAL_MS } from '@/lib/agentMerge'
import { samePatches } from '@/lib/changePlanApply'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { apiUrlFor } from '@/lib/session'
import { newRequestId } from './aiStore'
import { isAgentPlanHidden, sourceOf, useChangePlanStore, type PlanEntry } from './changePlanStore'
import { activeProjectFile, useDocumentStore } from './documentStore'

/** 落盘后的方案不会再变（只会被落定）：按 id 留着，免得每分钟重读一遍对不上的 */
const planCache = new Map<string, ChangePlan>()
/** 换项目 / 单测时清掉读过的方案 */
export const resetAgentPlanCache = (): void => planCache.clear()

/** 窗口里对得上这份方案的图：当前画布上 `fileId` 相同、可改、子图 id 就是方案记的那张（再加排版名对得上） */
function matchPanel(ref: AgentPlanRef): PanelObject | null {
  const found = useDocumentStore
    .getState()
    .doc.objects.filter((o): o is PanelObject => o.type === 'panel' && o.fileId === ref.target)
    .filter((p) => {
      try {
        refuseArtifactOperation(p)
        return true
      } catch {
        return false
      }
    })
  // 只认方案记的那张子图；开着的排版名能确定时也要对得上（同一张图在别的排版里不出卡）。排版名认不出就只比子图 id
  const open = activeProjectFile()?.name
  if (ref.layout && open && ref.layout !== open) return null
  return found.find((p) => p.id === ref.panel) ?? null
}

/** 问一次：对得上的 Agent 建议入列成卡。→ 这次新出的张数。出错（没连上、项目换了）静默当没有。 */
export async function discoverAgentPlans(): Promise<number> {
  if (!useDocumentStore.getState().doc.objects.some((o) => o.type === 'panel')) return 0
  const guard = captureProjectEpoch()
  let refs: AgentPlanRef[]
  try {
    refs = await listAgentChangePlans()
  } catch {
    return 0
  }
  if (!guard.still()) return 0
  const ids = new Set(refs.map((r) => r.id))
  for (const id of [...planCache.keys()]) if (!ids.has(id)) planCache.delete(id)
  let added = 0
  for (const ref of refs) {
    if (isAgentPlanHidden(ref.id)) continue
    const ds = useDocumentStore.getState()
    if (!matchPanel(ref)) continue
    let plan = planCache.get(ref.id)
    if (!plan) {
      try {
        const view = await getChangePlan(ref.id)
        if (view.state !== 'ready' || !view.plan.todos?.length) continue
        plan = view.plan
        planCache.set(ref.id, plan)
      } catch (err) {
        if (err instanceof EngineError) planCache.delete(ref.id)
        continue
      }
    }
    // 取方案期间换了项目 / 文档 / 画布：这份回答属于别人，不入
    const now = useDocumentStore.getState()
    if (!guard.still() || now.documentId !== ds.documentId || now.loadSeq !== ds.loadSeq || now.activeCanvasId !== ds.activeCanvasId) return added
    const panel = matchPanel(ref)
    if (!panel || !samePatches(panel.overrides, plan.base_patches)) continue
    const src = (p: string) => apiUrlFor(p, guard.pj)
    const entry: PlanEntry = {
      key: newRequestId(),
      from: 'assistant',
      agent: { host: ref.host ?? plan.agent?.host },
      pj: guard.pj,
      panelId: panel.id,
      fileId: panel.fileId,
      source: sourceOf(panel),
      documentId: now.documentId,
      loadSeq: now.loadSeq,
      canvasId: now.activeCanvasId,
      createdAt: Date.now(),
      status: 'ready',
      planId: plan.id,
      plan,
      versions: [{ planId: plan.id, plan, beforeSrc: src(plan.preview.before), afterSrc: src(plan.preview.after), unchecked: {} }],
      beforeSrc: src(plan.preview.before),
      afterSrc: src(plan.preview.after),
      unchecked: {},
      drafts: {},
      request: {},
      comments: [],
      commentCount: 0,
    }
    if (useChangePlanStore.getState().addAgentEntry(entry)) added++
  }
  return added
}

/** 窗口回到前台时、以及开着期间每隔一段时间问一次（与 `watchAgentAdditions` 同一个节奏）。返回停止函数（App 卸载时调）。 */
export function watchAgentPlans(intervalMs = AGENT_WATCH_INTERVAL_MS): () => void {
  let busy = false
  const check = async () => {
    if (busy || document.visibilityState === 'hidden') return
    busy = true
    try {
      await discoverAgentPlans()
    } finally {
      busy = false
    }
  }
  const onFocus = () => void check()
  window.addEventListener('focus', onFocus)
  const timer = window.setInterval(() => void check(), intervalMs)
  void check()
  return () => {
    window.removeEventListener('focus', onFocus)
    window.clearInterval(timer)
  }
}
