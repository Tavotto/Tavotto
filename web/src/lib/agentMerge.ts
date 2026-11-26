/**
 * 「把 Agent 的改动并进来」（ADR 0208 §五、第 2 期 ③；ADR 0212 第 5 期 ②）：插件往这份排版的项目文件里加了标注、或改 / 删 /
 * 重排了已有的矢量对象之后，桌面这边内存里的排版还停在之前的基线上。后端确认「基线之后只有 Agent 写过」并给出改了什么，
 * 这里按**字段三方合并**一次 commit 并进当前画布（一步可撤销），并把项目文件的基线推到磁盘那份的修订号——之后 ⌘S 照常保存。
 *
 * 只在桌面这边**没碰过** Agent 动到的地方时才并（ADR 0212 §二：碰过同一字段、或删 / 改冲突，就照旧走覆盖 / 另存，不替用户挑）：
 *
 * - 改：Agent 改过的每个字段，桌面这边还是基线的值；只把这几个字段换成 Agent 的，其余保留桌面的；
 * - 删：桌面这边那个对象与基线一模一样（桌面也已经删了 = 一致，跳过）；
 * - 加：id 不撞内存里已有的；按 Agent 那份顺序插在它前面那个对象之后；
 * - 顺序：Agent 重排过时，桌面这边基线里那些对象的相对顺序没动过；桌面新建的对象跟着它在桌面里的前一个；
 * - 布局组：Agent 改过时，桌面这边还是基线那份。
 *
 * 只在 Agent 的改动**全在当前画布**上时才并：撤销栈只管当前画布，跨画布的合并撤不干净，宁可不提供。
 */
import { fetchAgentAdditions, type AgentAdditions, type AgentCanvasDiff } from '@/lib/api'
import { msg } from '@/i18n'
import { activeProjectFile, setProjectFile, useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import type { CanvasObject, LayoutGroup } from '@/types/document'

export interface MergeableAdditions {
  name: string
  revision: string
  /** 动到的对象数（新加 + 改 + 删） */
  count: number
  /** 只有新加的对象（0208 的标注）：沿用「并入 Agent 加的标注」那句话 */
  onlyAdded: boolean
  diff: AgentCanvasDiff
  /** 发请求那一刻的上下文：回答只对这一份文档、这一个绑定、这一个基线成立（Codex #2217） */
  ctx: MergeContext
}

interface MergeContext {
  documentId: string
  projectId: string
  loadSeq: number
  canvasId: string
  base: string
}

function currentContext(): MergeContext | null {
  const s = useDocumentStore.getState()
  const b = activeProjectFile()
  if (!b || !b.revision) return null
  return { documentId: s.documentId, projectId: b.projectId, loadSeq: s.loadSeq, canvasId: s.activeCanvasId, base: b.revision }
}

/** 此刻还是不是发请求时那一份：文档、载入代次、项目、画布、基线有一样变了，回答就不作数 */
function stillSame(ctx: MergeContext): boolean {
  const now = currentContext()
  return (
    !!now &&
    now.documentId === ctx.documentId &&
    now.loadSeq === ctx.loadSeq &&
    now.projectId === ctx.projectId &&
    now.canvasId === ctx.canvasId &&
    now.base === ctx.base
  )
}

/** 按 JSON 语义比（键序无关；`undefined` 与缺这个键同义——落盘时本来就不写） */
function canon(v: unknown): unknown {
  if (Array.isArray(v)) return v.map((x) => (x === undefined ? null : canon(x)))
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {}
    for (const k of Object.keys(v as object).sort()) {
      const x = (v as Record<string, unknown>)[k]
      if (x !== undefined) out[k] = canon(x)
    }
    return out
  }
  return v
}
const same = (a: unknown, b: unknown): boolean => JSON.stringify(canon(a)) === JSON.stringify(canon(b))

/** 空数组的布局组与没有这个键同义（`canvasToDoc` 也不写它） */
const groupsOf = (g: LayoutGroup[] | null | undefined): LayoutGroup[] | null => (g && g.length ? g : null)

/** 把 `extra` 里的 id 按 `ref` 里的顺序插进 `base`：每个插在它在 `ref` 里最近的、已在结果里的前一个之后；没有就放最前 */
function weave(base: string[], extra: string[], ref: string[]): string[] {
  const out = [...base]
  const want = new Set(extra)
  for (let i = 0; i < ref.length; i++) {
    const id = ref[i]
    if (!want.has(id)) continue
    let at = 0
    for (let j = i - 1; j >= 0; j--) {
      const k = out.indexOf(ref[j])
      if (k >= 0) {
        at = k + 1
        break
      }
    }
    out.splice(at, 0, id)
  }
  return out
}

export interface MergedCanvas {
  objects: CanvasObject[]
  layoutGroups: LayoutGroup[] | null
}

/**
 * 字段三方合并：基线（`diff` 里的改前）+ Agent（改后）+ 桌面此刻（`objects` / `layoutGroups`）。桌面碰过 Agent 动到的地方 → null。
 * 纯函数，不改入参。
 */
export function planAgentMerge(
  objects: readonly CanvasObject[],
  layoutGroups: LayoutGroup[] | null | undefined,
  diff: AgentCanvasDiff,
): MergedCanvas | null {
  const mem = new Map(objects.map((o) => [o.id, o]))
  const next = new Map<string, CanvasObject>()
  for (const a of diff.added) {
    if (mem.has(a.id) || next.has(a.id)) return null
    next.set(a.id, structuredClone(a))
  }
  const gone = new Set<string>()
  for (const b of diff.deleted) {
    const m = mem.get(b.id)
    if (!m) continue // 桌面这边也删了：一致
    if (!same(m, b)) return null // 桌面改过它、Agent 删了它：删 / 改冲突
    gone.add(b.id)
  }
  for (const { before, after } of diff.changed) {
    const m = mem.get(after.id)
    if (!m) return null // 桌面删了、Agent 改了
    const b = before as unknown as Record<string, unknown>
    const a = after as unknown as Record<string, unknown>
    const mine = m as unknown as Record<string, unknown>
    const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])].filter((k) => !same(b[k], a[k]))
    if (keys.some((k) => !same(mine[k], b[k]))) return null // 同一字段两边都改了
    const patched: Record<string, unknown> = { ...structuredClone(mine) }
    for (const k of keys) {
      if (a[k] === undefined) delete patched[k]
      else patched[k] = structuredClone(a[k])
    }
    next.set(after.id, patched as unknown as CanvasObject)
  }
  const memIds = objects.map((o) => o.id).filter((id) => !gone.has(id))
  const baseSet = new Set(diff.base_order)
  let order: string[]
  if (diff.reordered) {
    const live = new Set(memIds)
    const mineRel = memIds.filter((id) => baseSet.has(id))
    const baseRel = diff.base_order.filter((id) => live.has(id))
    if (!same(mineRel, baseRel)) return null // 两边都重排过
    const added = new Set(diff.added.map((o) => o.id))
    const skeleton = diff.order.filter((id) => live.has(id) || added.has(id))
    const mineOnly = memIds.filter((id) => !baseSet.has(id) && !added.has(id))
    order = weave(skeleton, mineOnly, memIds)
  } else {
    order = weave(memIds, diff.added.map((o) => o.id), diff.order)
  }
  let groups = groupsOf(layoutGroups)
  if (diff.layout_groups) {
    if (!same(groups, groupsOf(diff.layout_groups.before))) return null
    groups = groupsOf(diff.layout_groups.after)
    groups = groups ? structuredClone(groups) : null
  }
  return { objects: order.map((id) => next.get(id) ?? mem.get(id)!), layoutGroups: groups }
}

/** 当前绑定的项目排版有没有可并的 Agent 改动；没有绑定 / 不只是 Agent 写的 / 不在当前画布 / 桌面碰过 → null */
export async function mergeableAgentAdditions(): Promise<MergeableAdditions | null> {
  const binding = activeProjectFile()
  const ctx = currentContext()
  if (!binding || !ctx) return null
  const got = await fetchAgentAdditions(binding.name, ctx.base)
  // 请求途中切了项目 / 换了文档 / 换了画布 / 基线变了：这份回答属于别人，不并（项目副本的画布 id 与排版名可能相同）
  if (!stillSame(ctx)) return null
  return toMergeable(binding.name, got, ctx)
}

export function toMergeable(name: string, got: AgentAdditions, ctx: MergeContext): MergeableAdditions | null {
  if (!got.available || !got.revision || !got.canvases?.length) return null
  if (got.canvases.length !== 1 || got.canvases[0].id !== ctx.canvasId) return null
  const diff = got.canvases[0]
  const s = useDocumentStore.getState()
  if (!planAgentMerge(s.doc.objects, s.doc.layoutGroups, diff)) return null
  const onlyAdded = !diff.changed.length && !diff.deleted.length && !diff.reordered && !diff.layout_groups
  const count = diff.added.length + diff.changed.length + diff.deleted.length
  return { name, revision: got.revision, count, onlyAdded, diff, ctx }
}

/** 并进来：一次 commit，再把绑定的基线推到磁盘那一份。合并在 commit 这一刻按此刻的文档重算（问完之后桌面可能又改过）。 */
export function mergeAgentAdditions(m: MergeableAdditions): boolean {
  const binding = activeProjectFile()
  if (!binding || binding.name !== m.name || !stillSame(m.ctx)) return false
  const s = useDocumentStore.getState()
  // 手势进行中（拖动等）：commit 会并进那次手势的事务，取消手势就把改动一起撤掉——等手势结束，下一次再并
  if (s.txn) return false
  const plan = planAgentMerge(s.doc.objects, s.doc.layoutGroups, m.diff)
  if (!plan) return false
  const label = m.onlyAdded
    ? msg('agentMerge.commit', { count: m.count }, 'workspace')
    : msg('agentMerge.commitEdit', { count: m.count }, 'workspace')
  s.commit(label, (d) => {
    d.objects = structuredClone(plan.objects)
    if (plan.layoutGroups) d.layoutGroups = structuredClone(plan.layoutGroups)
    else delete d.layoutGroups
  })
  // **真的进了文档**才推进基线：只读 / 被接管的窗口里 commit 什么都不做，推了基线下一次保存就会把改动静默抹掉
  const now = useDocumentStore.getState().doc
  if (!same(now.objects, plan.objects) || !same(groupsOf(now.layoutGroups), plan.layoutGroups)) return false
  const after = activeProjectFile()
  if (after) setProjectFile({ ...after, revision: m.revision, dirty: true })
  return true
}

/** 顶栏胶囊的 owner：同一条提醒只挂一个，并进来 / 换了排版之后由它认领清掉 */
export const AGENT_MERGE_OWNER = 'agentMerge'

let undoRunner: (() => void) | null = null
/** 状态栏「撤销」走与 ⌘Z 同一个入口（`useKeyboard.runUndoRedo`）：那一处不能被本模块 import（成环），由它交进来 */
export function setAgentMergeUndoRunner(run: () => void): void {
  undoRunner = run
}

/** 含删除的那次并入，状态栏带「撤销」挂多久（之后照常收起，⌘Z 仍可撤） */
export const AGENT_MERGE_UNDO_DISMISS_MS = 10_000

/**
 * 并进来，并在状态栏说一句结果（胶囊与冲突对话框共用）。
 *
 * 含**删除**的那次（维护者 2026-11-26 选「加一个撤销按钮」：东西少了比多了难察觉）：那句话旁边挂「撤销」，点了撤的是这一次并入——
 * 只在同一份文档、栈顶还是这一条历史时撤；之后又改了别的，撤的就是别的了，只收起提示（同一键排版的胶囊）。
 */
export function mergeAndReport(m: MergeableAdditions): boolean {
  const ok = mergeAgentAdditions(m)
  if (!ok) return false
  const ui = useUiStore.getState()
  const text = msg(m.onlyAdded ? 'agentMerge.done' : 'agentMerge.doneEdit', { count: m.count }, 'workspace')
  if (!m.diff.deleted.length) {
    ui.setStatus(text, 'done', { owner: AGENT_MERGE_OWNER })
    return ok
  }
  const s = useDocumentStore.getState()
  const entry = s.past.at(-1)
  const documentId = s.documentId
  // 说明这次「含删除」——那颗「撤销」为什么在这里（agy 审核意见）；「保存后写进项目」顶栏圆点已经在说，这句不再重复
  ui.setStatus(msg('agentMerge.doneDeleted', { count: m.count }, 'workspace'), 'done', {
    owner: AGENT_MERGE_OWNER,
    dismissMs: AGENT_MERGE_UNDO_DISMISS_MS,
    action: {
      label: msg('agentMerge.undo', undefined, 'workspace'),
      run: () => {
        const cur = useDocumentStore.getState()
        if (cur.documentId === documentId && entry && cur.past.at(-1) === entry) {
          if (undoRunner) undoRunner() // 它自己会说「已撤销…」
          else cur.undo()
        } else useUiStore.getState().setStatus(null)
      },
    },
  })
  return ok
}

/**
 * 不用等到 ⌘S：窗口回到前台时、以及开着期间每分钟，问一下绑定的项目排版有没有 Agent 刚写的改动；有就**直接并进来**
 * （一次 commit、可 ⌘Z 撤销），状态栏短暂说一句（维护者 2026-11-25：没有要用户裁决的事，不挂要点的胶囊）。
 * 返回停止函数（App 卸载时调）。
 */
export function watchAgentAdditions(intervalMs = 60_000): () => void {
  let busy = false
  const check = async () => {
    if (busy || document.visibilityState === 'hidden') return
    busy = true
    try {
      const m = await mergeableAgentAdditions()
      if (m) mergeAndReport(m)
    } finally {
      busy = false
    }
  }
  const onFocus = () => void check()
  window.addEventListener('focus', onFocus)
  const timer = window.setInterval(() => void check(), intervalMs)
  return () => {
    window.removeEventListener('focus', onFocus)
    window.clearInterval(timer)
  }
}
