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
  | {
      ok: true
      applied: number
      /** 同一个属性上互相矛盾、整组没修的条数（`applied` 里**不含**它们） */
      skipped?: number
    }
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
  const raw: FixPlan[] = []
  for (const i of here) {
    const plan = planFix(i, profile, doc)
    if (plan) raw.push(plan)
  }
  const { plans, skipped } = mergePlans(raw)
  if (!plans.length) return { ok: false, reason: 'no_plan' }
  applyFixPlans(plans, hist('fixIssues', { count: plans.length }))
  return { ok: true, applied: plans.length, skipped: skipped || undefined }
}

/** 一条计划写的是哪个属性。同一个键上有两条 = 后写的会盖掉先写的。 */
function targetKey(plan: FixPlan): string | null {
  if (plan.kind === 'textSize') return `${plan.objectId}|textSize`
  if (plan.kind === 'override' && plan.patches.length === 1) {
    const p = plan.patches[0]
    return `${plan.objectId}|${p.gid}|${p.prop}`
  }
  return null
}

function planValue(plan: FixPlan): number | null {
  if (plan.kind === 'textSize') return plan.sizePt
  if (plan.kind === 'override' && plan.patches.length === 1) {
    const v = plan.patches[0].value
    return typeof v === 'number' ? v : null
  }
  return null
}

function withValue(plan: FixPlan, value: number): FixPlan {
  if (plan.kind === 'textSize') return { ...plan, sizePt: value }
  if (plan.kind === 'override') {
    return { ...plan, patches: [{ ...plan.patches[0], value }] }
  }
  return plan
}

/**
 * **同一个属性上的多条计划要合并成一条，不能挨个写。**
 *
 * 一条计划算出的目标值只是"对我这条规则最省事的那个数"。两条规则各写一遍时
 * 后写的赢，而它可能违反前一条：默认规范上一条 6pt 图例文字同时命中
 * `font-below-absolute-floor`（算出 8.5）与 `legend-font-size`（算出 8.0），
 * 8.0 后写、把 8.5 盖掉，而 8.0 仍然过不了绝对下限（判据是 `eff <= floor`）
 * ——「全部修复」报了两条修好，问题面板里那条 error 还在（PR #214 第三轮评审）。
 *
 * 合并办法：取各条**可接受区间的交集**，再把提议值夹进去。由构造保证结果同时
 * 满足每一条规则。给不出区间、或者交集为空（两条规则互相矛盾）时**整组不修**
 * ——报一个修不了，比报"修好了"而它没好要诚实。
 */
export function mergePlans(raw: FixPlan[]): { plans: FixPlan[]; skipped: number } {
  const groups = new Map<string, FixPlan[]>()
  const out: FixPlan[] = []
  for (const plan of raw) {
    const key = targetKey(plan)
    if (key == null) {
      out.push(plan)
      continue
    }
    const list = groups.get(key)
    if (list) list.push(plan)
    else groups.set(key, [plan])
  }
  let skipped = 0
  for (const list of groups.values()) {
    if (list.length === 1) {
      out.push(list[0])
      continue
    }
    let lo = Number.NEGATIVE_INFINITY
    let hi = Number.POSITIVE_INFINITY
    let usable = true
    let best = Number.NEGATIVE_INFINITY
    for (const plan of list) {
      const bound = plan.kind === 'pageWidth' ? undefined : plan.bound
      const value = planValue(plan)
      if (!bound || value == null) {
        usable = false
        break
      }
      if (bound.min != null) lo = Math.max(lo, bound.min)
      if (bound.max != null) hi = Math.min(hi, bound.max)
      best = Math.max(best, value)
    }
    if (!usable || lo > hi) {
      skipped += list.length
      continue
    }
    const merged = Math.min(Math.max(best, lo), hi)
    out.push(withValue(list[0], merged))
    // 合并掉的那几条**不算白干**：它们与留下的这一条一起被这个值满足了
  }
  return { plans: out, skipped }
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
