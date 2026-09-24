/**
 * 安全自动修复的**落地**（ADR 0030 / 0080）。画布层的计划在 `lib/issueFix.ts`，
 * 面板内部的计划、真实渲染与裁决在后端（`/api/engine/specfix`）。
 *
 * 四条纪律：
 *
 * * **修完真的过了才提交**：面板那一路由后端对真实渲染裁决——点名的问题一条不剩、
 *   没有新增 / 加重的问题、受保护的属性一个没变、字体真的落成了那张脸。不过就
 *   **整张图一个字不改**，并说出原因（闭集 `FixFailureReason`）。
 * * **一次修复一条历史**：所有面板的事务都回来之后才 commit，而且只 commit 一次
 *   ——⌘Z 一次全部撤回，不是撤五次。
 * * **等待期间文档被改过就丢弃结果**：事务开始时记下每张图的 override 列表与文档
 *   代次，回来时对不上就不写（不拿旧基准上的结果覆盖用户刚做的改动）。
 * * **只走 documentStore**：dirty、undo、autosave 全部照常，与用户手改一模一样。
 */
import { msg, type UiMessage } from '@/i18n'
import { engineSpecfix, type SpecFixResponse } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { fixOptions, fixRoute, planFix, type FixChoice, type FixPlan } from '@/lib/issueFix'
import { panelScale } from '@/lib/preflight'
import type { PublicationProfile } from '@/lib/profile'
import { resolveDocumentSpec } from '@/lib/specBinding'
import type { ValidationIssue } from '@/lib/validation'
import { requestRender } from '@/store/renderScheduler'
import type { PanelObject, PanelOverride } from '@/types/document'
import { activateCanvas } from './canvasSession'
import { useDocumentStore } from './documentStore'
import { useProfileStore } from './profileStore'

/**
 * 一条没修成的原因（闭集，文案在 `errors:problems.fixFailed.*`）。
 *
 * 后端的退出码在 `failureOf()` 里收成这几档：用户要知道的是「为什么没改」与
 * 「图动没动」（永远没动），不是事务内部走到了哪一步。
 */
export type FixFailureReason =
  | 'no_plan'
  | 'canvas_missing'
  | 'object_missing'
  | 'needs_choice'
  /** 规范要的字体这台机器上没装：字体那几条没改，其余照修 */
  | 'font_unavailable'
  /** 这样修会让别处变差（文字被挤出图幅、压进别的子图、冒出新问题……）：整张图没改 */
  | 'would_worsen'
  /** 改完真实渲染出来仍不合规：整张图没改 */
  | 'not_resolved'
  /** 出界的文字靠调整边距放不下（已到位移预算）：这一条没改，同批别的照修 */
  | 'no_fit'
  /** 修复期间这张图被改过：结果丢弃 */
  | 'stale'
  /** 渲染 / 后端出错：整张图没改 */
  | 'engine_failed'
  /** 上一次修复还没结束 */
  | 'busy'
  /** 这个宿主里没有后端事务（内嵌画布 / playground） */
  | 'unavailable'

export interface FixFailure {
  reason: FixFailureReason
  /** 没修成的条数 */
  count: number
  /** `font_unavailable` 时：规范要的那个字体 */
  font?: string
}

export type FixOutcome =
  | { ok: true; applied: number; failed: FixFailure[] }
  | { ok: false; reason: FixFailureReason; failed: FixFailure[] }

const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')

/** 比较用的规范化：同一份输入 = 同一个字符串（对象身份在 immer 下不可靠，内容才可靠）。 */
const same = (v: unknown): string => JSON.stringify(v ?? null)

/**
 * 这份文档**此刻生效的**规范全文（绑定 + 库里那条的内容 / 快照）。等后端期间要比的是
 * 它，不只是 `doc.profile` 这个绑定：在设置里改了绑定着的那套规范，绑定一个字没变，
 * 规则却换了（Codex #549 第二轮）。判据唯一出处仍是 `lib/specBinding`。
 */
function resolvedSpec(): string {
  const doc = useDocumentStore.getState().doc
  return same(resolveDocumentSpec(doc.profile, useProfileStore.getState().catalog()).profile)
}

/** 同一时刻只跑一轮修复：两轮交错时，后一轮的基准是前一轮还没提交的旧文档。 */
let inflight = false

/**
 * 修一条。跨画布时**先切过去**——问题面板列的是整个项目的问题，而
 * `commit()` 只写激活画布；不切的话「修复」会静默改到另一张画布的同名对象上，
 * 或者什么都不做。逐条点的修复连建议档也修（那是用户点名要的）。
 */
export async function applyIssueFix(
  issue: ValidationIssue,
  profile: PublicationProfile,
  choice?: FixChoice,
): Promise<FixOutcome> {
  if (inflight) return fail('busy', 1)
  if (!ensureCanvas(issue)) return fail('canvas_missing', 1)
  const doc = useDocumentStore.getState().doc
  if (issue.objectRef.objectId && !doc.objects.some((o) => o.id === issue.objectRef.objectId)) {
    return fail('object_missing', 1)
  }
  if (fixOptions(issue, profile).length && !choice) return fail('needs_choice', 1)
  return run([issue], profile, choice, hist('fixIssue'))
}

/**
 * 批量修（「全部处理」）。**一个批事务**——⌘Z 一次全部撤回。
 *
 * 只处理**当前激活画布**上的问题：撤销栈是按画布换入换出的
 * （`documentStore.switchCanvas`），跨画布的一次 commit 在这套模型里不存在。
 * **建议档不进批量**：轴标题加不加粗是口味，只在用户逐条点它时才改。
 */
export async function applyIssueFixes(
  issues: ValidationIssue[],
  profile: PublicationProfile,
  opts: BatchOptions = {},
): Promise<FixOutcome> {
  const s = useDocumentStore.getState()
  return run(batchable(issues, s.activeCanvasId, opts), profile, undefined, null)
}

export interface BatchOptions {
  /**
   * 连建议档一起修。只给**用户点名的那一组**用（组头的「全部修复」：那一组
   * 就是建议档，点它就是要修它）；「全部处理」永远不带。
   */
  includeSuggestions?: boolean
}

/** 批量会处理哪些——面板上的计数与真正执行的必须是同一个集合。 */
export function batchable(
  issues: ValidationIssue[],
  activeCanvasId: string,
  opts: BatchOptions = {},
): ValidationIssue[] {
  return issues.filter(
    (i) =>
      i.objectRef.canvasId === activeCanvasId &&
      i.fixKind === 'safe_auto' &&
      (opts.includeSuggestions || i.severity !== 'suggestion'),
  )
}

function fail(reason: FixFailureReason, count: number): FixOutcome {
  return { ok: false, reason, failed: [{ reason, count }] }
}

async function run(
  issues: ValidationIssue[],
  profile: PublicationProfile,
  choice: FixChoice | undefined,
  label: UiMessage | null,
): Promise<FixOutcome> {
  if (!issues.length) return fail('no_plan', 0)
  if (inflight) return fail('busy', issues.length)
  inflight = true
  try {
    return await runLocked(issues, profile, choice, label)
  } finally {
    inflight = false
  }
}

async function runLocked(
  issues: ValidationIssue[],
  profile: PublicationProfile,
  choice: FixChoice | undefined,
  label: UiMessage | null,
): Promise<FixOutcome> {
  const start = useDocumentStore.getState()
  const docAtStart = start.doc
  const loadSeq = start.loadSeq
  const canvasId = start.activeCanvasId
  const specAtStart = resolvedSpec()
  const failed: FixFailure[] = []

  // ---- 画布层：同步算完 ----
  const raw: FixPlan[] = []
  const byPanel = new Map<string, ValidationIssue[]>()
  for (const i of issues) {
    const route = fixRoute(i, docAtStart)
    if (route === 'canvas') {
      const plan = planFix(i, profile, docAtStart, choice)
      if (plan) raw.push(plan)
      else addFailure(failed, 'no_plan', 1)
    } else if (route === 'engine') {
      const id = i.objectRef.objectId!
      const list = byPanel.get(id)
      if (list) list.push(i)
      else byPanel.set(id, [i])
    } else addFailure(failed, 'no_plan', 1)
  }
  const merged = mergePlans(raw)
  let plans = merged.plans
  if (merged.skipped) addFailure(failed, 'no_plan', merged.skipped)

  // ---- 面板：一张一张地跑后端事务 ----
  // 串行而不是并发：同一个脚本的几张图共用一个 worker，交错的事务会让它的热态
  // 在别人的候选与基准之间来回跳（每条响应本身仍然正确，但没有必要去赌）
  const next = new Map<string, { overrides: PanelOverride[]; applied: number }>()
  if (byPanel.size && engineTransport()) {
    for (const list of byPanel.values()) addFailure(failed, 'unavailable', list.length)
    byPanel.clear()
  }
  for (const [id, list] of byPanel) {
    const panel = docAtStart.objects.find(
      (o): o is PanelObject => o.id === id && o.type === 'panel',
    )
    if (!panel) {
      addFailure(failed, 'object_missing', list.length)
      continue
    }
    let res: SpecFixResponse
    try {
      res = await engineSpecfix(
        panel.fileId,
        panel.overrides,
        panelScale(panel),
        profile,
        list.map((i) => ({ rule: i.ruleCode, gid: i.objectRef.gid ?? '' })),
      )
    } catch {
      addFailure(failed, 'engine_failed', list.length)
      continue
    }
    const out = settle(res, list)
    for (const f of out.failed) addFailure(failed, f.reason, f.count, fontOf(profile))
    if (res.ok && out.applied > 0) {
      next.set(id, { overrides: res.patches.map((p) => ({ ...p })), applied: out.applied })
    }
  }

  // ---- 回来之后：这次用到的每一样输入都还是出发时那一份，才写 ----
  // 不只比 override 列表（Codex #549 P1）：面板的尺寸 / 裁剪 / 旋转决定了发出去的
  // `panelScale()`，文档绑定的规范决定了后端按哪套判；画布层计划写的是标注字号与页宽，
  // 用户在这几秒里改了它们，旧结果就会盖掉新改动。所以整个对象比、页面比、规范绑定比
  const now = useDocumentStore.getState()
  const moved =
    now.loadSeq !== loadSeq ||
    now.activeCanvasId !== canvasId ||
    same(now.doc.profile) !== same(docAtStart.profile) ||
    resolvedSpec() !== specAtStart
  const unchanged = (id: string): boolean => {
    const before = docAtStart.objects.find((o) => o.id === id)
    const after = now.doc.objects.find((o) => o.id === id)
    return !!before && !!after && same(before) === same(after)
  }
  for (const [id, item] of [...next]) {
    if (moved || !unchanged(id)) {
      addFailure(failed, 'stale', item.applied)
      next.delete(id)
    }
  }
  const fresh = plans.filter((plan) =>
    moved
      ? false
      : plan.kind === 'pageWidth'
        ? same(now.doc.page) === same(docAtStart.page)
        : unchanged(plan.objectId),
  )
  if (fresh.length < plans.length) addFailure(failed, 'stale', plans.length - fresh.length)
  plans = fresh

  const applied = plans.length + [...next.values()].reduce((n, x) => n + x.applied, 0)
  if (!applied) return { ok: false, reason: failed[0]?.reason ?? 'no_plan', failed }
  commitFixes(
    plans,
    new Map([...next].map(([id, x]) => [id, x.overrides])),
    label ?? hist('fixIssues', { count: applied }),
  )
  return { ok: true, applied, failed }
}

/** 后端的一份裁决 → 这几条问题里修成了几条、没修成的为什么。 */
function settle(
  res: SpecFixResponse,
  list: ValidationIssue[],
): { applied: number; failed: FixFailure[] } {
  const failed: FixFailure[] = []
  let applied = 0
  for (const i of list) {
    const gid = i.objectRef.gid ?? ''
    const skip = res.skipped.find((s) => s.rule === i.ruleCode && s.gid === gid)
    // 逐条的原因优先（字体没装 / 放不下）；没有逐条原因的，整张图没提交就按退出码说
    if (skip) addFailure(failed, skipReason(skip.reason), 1)
    else if (!res.ok) addFailure(failed, failureOf(res.exit), 1)
    else applied += 1
  }
  return { applied, failed }
}

function skipReason(reason: string): FixFailureReason {
  if (reason === 'font_unavailable') return 'font_unavailable'
  if (reason === 'no_fit') return 'no_fit'
  return 'no_plan'
}

/** 后端退出码 → 用户要知道的那一档。没登记的一律「渲染出错」，绝不当成修好了。 */
function failureOf(exit: string): FixFailureReason {
  switch (exit) {
    case 'font_unavailable':
      return 'font_unavailable'
    case 'not_resolved':
      return 'not_resolved'
    case 'constraint_conflict':
    case 'protected_changed':
    case 'budget_exceeded':
      return 'would_worsen'
    case 'nothing_to_do':
    case 'unsupported':
      return 'no_plan'
    default:
      return 'engine_failed'
  }
}

function fontOf(profile: PublicationProfile): string | undefined {
  return profile.font_family?.latin || undefined
}

function addFailure(
  list: FixFailure[],
  reason: FixFailureReason,
  count: number,
  font?: string,
): void {
  if (count <= 0) return
  const hit = list.find((f) => f.reason === reason)
  if (hit) hit.count += count
  else list.push(reason === 'font_unavailable' && font ? { reason, count, font } : { reason, count })
}

/** 一条计划写的是哪个属性。同一个键上有两条 = 后写的会盖掉先写的。 */
function targetKey(plan: FixPlan): string | null {
  return plan.kind === 'textSize' ? `${plan.objectId}|textSize` : null
}

/**
 * **同一个属性上的多条计划要合并成一条，不能挨个写。**
 *
 * 一条计划算出的目标值只是"对我这条规则最省事的那个数"。两条规则各写一遍时
 * 后写的赢，而它可能违反前一条（PR #214 第三轮评审）。合并办法：取各条**可接受
 * 区间的交集**，再把提议值夹进去；给不出区间、或者交集为空时**整组不修**——报
 * 一个修不了，比报"修好了"而它没好要诚实。面板内部的同类合并在后端
 * （`engine/specfix._plan_fonts` 按元素取区间）。
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
      if (plan.kind !== 'textSize' || !plan.bound) {
        usable = false
        break
      }
      if (plan.bound.min != null) lo = Math.max(lo, plan.bound.min)
      if (plan.bound.max != null) hi = Math.min(hi, plan.bound.max)
      best = Math.max(best, plan.sizePt)
    }
    if (!usable || lo > hi) {
      skipped += list.length
      continue
    }
    const first = list[0] as Extract<FixPlan, { kind: 'textSize' }>
    out.push({ ...first, sizePt: Math.min(Math.max(best, lo), hi) })
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
 * 画布层的计划 + 各面板**最终的全量 override 列表** → **一条历史**。
 *
 * 面板那一份是后端裁决过的整张列表（「热态 == 文件 == 重放」的那一份），
 * 直接整张换上，不在前端再合并一次。写完触发重渲染（预检按新 manifest 再算
 * 一遍，那一步由 validation store 的订阅负责）。
 */
function commitFixes(
  plans: FixPlan[],
  panels: Map<string, PanelOverride[]>,
  label: UiMessage,
): void {
  useDocumentStore.getState().commit(label, (d) => {
    for (const plan of plans) {
      if (plan.kind === 'pageWidth') {
        d.page.w = plan.widthMm
        continue
      }
      const obj = d.objects.find((o) => o.id === plan.objectId)
      if (obj?.type === 'text') obj.sizePt = plan.sizePt
    }
    for (const [id, overrides] of panels) {
      const obj = d.objects.find((o) => o.id === id)
      if (obj?.type === 'panel') obj.overrides = overrides
    }
  })
  for (const id of panels.keys()) {
    const next = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
    if (next?.type === 'panel') requestRender(next, true)
  }
}
