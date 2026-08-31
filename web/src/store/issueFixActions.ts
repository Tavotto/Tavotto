/**
 * 安全自动修复的**落地**（ADR 0030）。计划怎么算在 `lib/issueFix.ts`（纯计算）。
 *
 * 三条纪律：
 *
 * * **一个修复一个事务，一批修复一个批事务**——⌘Z 一次撤回，不是撤五次。
 * * **跨画布先切过去**：`commit()` 只写激活画布，不切的话「修复」要么什么都
 *   不做，要么改到另一张画布的同名对象上。
 * * **只走 documentStore**：dirty、undo、autosave 全部照常，与用户手改一模一样。
 */
import { msg, type UiMessage } from '@/i18n'
import { requestRender } from '@/hooks/useEngineSync'
import { fixOptions, planFix, type FixChoice, type FixPlan } from '@/lib/issueFix'
import type { PublicationProfile } from '@/lib/profile'
import type { ValidationIssue } from '@/lib/validation'
import { activateCanvas } from './canvasSession'
import { useDocumentStore } from './documentStore'

export type FixOutcome =
  | { ok: true; applied: number }
  | { ok: false; reason: 'no_plan' | 'canvas_missing' | 'object_missing' | 'needs_choice' }

const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')

/**
 * 修一条。跨画布时**先切过去**——问题面板列的是整个项目的问题，而
 * `commit()` 只写激活画布；不切的话「修复」会静默改到另一张画布的同名对象上，
 * 或者什么都不做。
 */
export function applyIssueFix(
  issue: ValidationIssue,
  profile: PublicationProfile,
  choice?: FixChoice,
): FixOutcome {
  if (!ensureCanvas(issue)) return { ok: false, reason: 'canvas_missing' }
  const doc = useDocumentStore.getState().doc
  if (issue.objectRef.objectId && !doc.objects.some((o) => o.id === issue.objectRef.objectId)) {
    return { ok: false, reason: 'object_missing' }
  }
  const entry = fixOptions(issue, profile)
  if (entry.length && !choice) return { ok: false, reason: 'needs_choice' }
  const plan = planFix(issue, profile, doc, choice)
  if (!plan) return { ok: false, reason: 'no_plan' }
  applyFixPlans([plan], hist('fixIssue'))
  return { ok: true, applied: 1 }
}

/**
 * 批量修。**一个批事务**——⌘Z 一次全部撤回。
 *
 * 只处理**当前激活画布**上的问题：撤销栈是按画布换入换出的
 * （`documentStore.switchCanvas`），跨画布的一次 commit 在这套模型里不存在，
 * 硬拼出来的结果是「撤销要按三次，而且顺序不定」。界面据此只在本画布上给
 * 「全部修复」，别的画布上的那几条仍然一条一条修（每条各自一个事务）。
 */
export function applyIssueFixes(
  issues: ValidationIssue[],
  profile: PublicationProfile,
): FixOutcome {
  const s = useDocumentStore.getState()
  const doc = s.doc
  const here = issues.filter(
    (i) => i.objectRef.canvasId === s.activeCanvasId && i.fixKind === 'safe_auto',
  )
  const plans: FixPlan[] = []
  for (const i of here) {
    const plan = planFix(i, profile, doc)
    if (plan) plans.push(plan)
  }
  if (!plans.length) return { ok: false, reason: 'no_plan' }
  applyFixPlans(plans, hist('fixIssues', { count: plans.length }))
  return { ok: true, applied: plans.length }
}

/** 切到问题所在的画布；已经在那儿就什么都不做。 */
function ensureCanvas(issue: ValidationIssue): boolean {
  const s = useDocumentStore.getState()
  const target = issue.objectRef.canvasId
  if (!target || target === s.activeCanvasId) return true
  if (!s.canvases.some((c) => c.id === target)) return false
  activateCanvas(target)
  return useDocumentStore.getState().activeCanvasId === target
}

/**
 * 一批计划 → **一条历史**。写完统一触发重渲染（预检要按新的 manifest 再算
 * 一遍，那一步由 validation store 的订阅负责，这里不自己调）。
 */
export function applyFixPlans(plans: FixPlan[], label: UiMessage): void {
  const touched = new Set<string>()
  useDocumentStore.getState().commit(label, (d) => {
    for (const plan of plans) {
      if (plan.kind === 'pageWidth') {
        d.page.w = plan.widthMm
        continue
      }
      const obj = d.objects.find((o) => o.id === plan.objectId)
      if (!obj) continue
      if (plan.kind === 'textSize') {
        if (obj.type === 'text') obj.sizePt = plan.sizePt
        continue
      }
      if (obj.type !== 'panel') continue
      for (const p of plan.patches) {
        obj.overrides = obj.overrides.filter((x) => !(x.gid === p.gid && x.prop === p.prop))
        obj.overrides.push({ gid: p.gid, prop: p.prop, value: p.value })
      }
      touched.add(obj.id)
    }
  })
  for (const id of touched) {
    const next = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
    if (next?.type === 'panel') requestRender(next, true)
  }
}
