/**
 * 「把 Agent 加的并进来」（ADR 0208 §五、第 2 期 ③）：插件往这份排版的项目文件里追加了标注之后，桌面这边
 * 内存里的排版还停在之前的基线上。后端确认「磁盘多出来的只有 Agent 追加的对象」时，这里把它们**一次 commit**
 * 并进当前画布（一步可撤销），并把项目文件的基线推到磁盘那份的修订号——之后 ⌘S 照常保存、不再撞冲突。
 *
 * 只在 Agent 加的东西**全在当前画布**上时才并：撤销栈只管当前画布，跨画布的合并撤不干净，宁可不提供
 * （照旧走覆盖 / 另存）。对象 id 撞上内存里已有的（本窗口在基线之后新建了同 id 的对象，几乎不可能）也不并。
 */
import { fetchAgentAdditions, type AgentAdditions } from '@/lib/api'
import { msg } from '@/i18n'
import { activeProjectFile, setProjectFile, useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import type { CanvasObject } from '@/types/document'

export interface MergeableAdditions {
  name: string
  revision: string
  count: number
  objects: CanvasObject[]
}

/** 当前绑定的项目排版有没有可并的 Agent 标注；没有绑定 / 不只是 Agent 加的 / 不在当前画布 → null */
export async function mergeableAgentAdditions(): Promise<MergeableAdditions | null> {
  const binding = activeProjectFile()
  if (!binding || !binding.revision) return null
  const got = await fetchAgentAdditions(binding.name, binding.revision)
  return toMergeable(binding.name, got)
}

export function toMergeable(name: string, got: AgentAdditions): MergeableAdditions | null {
  if (!got.available || !got.revision || !got.canvases?.length) return null
  const s = useDocumentStore.getState()
  if (got.canvases.length !== 1 || got.canvases[0].id !== s.activeCanvasId) return null
  const objects = got.canvases[0].objects
  const taken = new Set(s.doc.objects.map((o) => o.id))
  if (!objects.length || objects.some((o) => taken.has(o.id))) return null
  return { name, revision: got.revision, count: objects.length, objects }
}

/** 并进来：一次 commit（标签「并入 Agent 加的标注」），再把绑定的基线推到磁盘那一份。 */
export function mergeAgentAdditions(m: MergeableAdditions): boolean {
  const binding = activeProjectFile()
  if (!binding || binding.name !== m.name) return false
  const s = useDocumentStore.getState()
  const taken = new Set(s.doc.objects.map((o) => o.id))
  if (m.objects.some((o) => taken.has(o.id))) return false
  s.commit(msg('agentMerge.commit', { count: m.count }, 'workspace'), (d) => {
    d.objects.push(...structuredClone(m.objects))
  })
  const after = activeProjectFile()
  if (after) setProjectFile({ ...after, revision: m.revision, dirty: true })
  return true
}

/** 顶栏胶囊的 owner：同一条提醒只挂一个，并进来 / 换了排版之后由它认领清掉 */
export const AGENT_MERGE_OWNER = 'agentMerge'

/** 并进来，并在状态栏说一句结果（胶囊与冲突对话框共用） */
export function mergeAndReport(m: MergeableAdditions): boolean {
  const ok = mergeAgentAdditions(m)
  if (ok) {
    useUiStore
      .getState()
      .setStatus(msg('agentMerge.done', { count: m.count }, 'workspace'), 'done', { owner: AGENT_MERGE_OWNER })
  }
  return ok
}

/**
 * 不用等到 ⌘S：窗口回到前台时、以及开着期间每分钟，问一下绑定的项目排版有没有 Agent 刚加的标注；有就**直接并进来**
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
