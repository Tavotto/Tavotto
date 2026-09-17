/**
 * 撤销历史与事务的**纯算法**：补丁怎么累积、怎么压缩、栈怎么推、undo / redo 怎么走。
 *
 * 2026-09-17 审计任务书 PR E 前端那一半从 `store/documentStore.ts` 提出来的。这里没有
 * store、没有副作用（遥测 / 活动信号 / 诊断都留在 store 的四个入口里）；输入是文档与
 * 栈，输出是新的文档与栈。**commit / beginTxn / txnUpdate / endTxn / undo / redo 仍然只有
 * `documentStore` 那一个语义入口**——这里只是它调用的算法，不是第二个写文档的地方。
 *
 * 判据的主语：一条历史 = 一组正向补丁 + 一组反向补丁；事务 = 尚未落进历史的那一组。
 */
import { applyPatches, type Objectish, type Patch } from 'immer'

export interface PatchPair<L = unknown> {
  label: L
  patches: Patch[]
  inverse: Patch[]
}

/** 历史条数上限；超过丢最老的。 */
export const HISTORY_LIMIT = 200

/**
 * 把一次更新并进进行中的事务。
 *
 * **两个数组的时间序是相反的**：正向补丁追加累积（末尾最新），反向补丁前插累积
 * （`[...inverse, ...txn.inverse]`），所以反向数组**末尾才是最早的那条**——不压缩时按序
 * 全量 applyPatches，最早的最后落地，正好回到事务开始前。commit 与 txnUpdate 都走这一条，
 * 两处各写一遍就会有一处哪天把顺序写反。
 */
export function accumulate<L>(txn: PatchPair<L>, patches: Patch[], inverse: Patch[]): PatchPair<L> {
  return {
    label: txn.label,
    patches: [...txn.patches, ...patches],
    inverse: [...inverse, ...txn.inverse],
  }
}

/**
 * 压缩一个事务的补丁：同一路径只保留一条。
 * 正向保留最后一次，反向保留最早一次。含增删的批次不压缩以保证正确性。
 *
 * 两边都取「数组里最后出现的那条」：按 `accumulate` 的时序，正向拿到最新，反向拿到最早。
 * 反向若按数组顺序取第一条（= 最新），撤销就只退到倒数第二次更新。
 */
export function compress(patches: Patch[], inverse: Patch[]): [Patch[], Patch[]] {
  const replaceOnly = (list: Patch[]) => list.every((p) => p.op === 'replace')
  if (!replaceOnly(patches) || !replaceOnly(inverse)) return [patches, inverse]

  const key = (p: Patch) => p.path.join('\u0000')
  const fwd = new Map<string, Patch>()
  for (const p of patches) fwd.set(key(p), p)
  const inv = new Map<string, Patch>()
  for (const p of inverse) inv.set(key(p), p)
  return [[...fwd.values()], [...inv.values()]]
}

/** 推一条进 past，超过上限丢最老的；任何新落地的历史都清空 future。 */
export function push<E>(
  past: readonly E[],
  entry: E,
  limit = HISTORY_LIMIT,
): { past: E[]; future: E[] } {
  const next = [...past, entry]
  if (next.length > limit) next.splice(0, next.length - limit)
  return { past: next, future: [] }
}

export interface StepResult<D, E> {
  /** 走成了：新文档 + 新栈。栈空或补丁对不上时 `ok=false`、`doc` 原样。 */
  ok: boolean
  doc: D
  past: E[]
  future: E[]
  /** 走到的那条（栈空时 null；应用失败时是被丢弃的那条）。 */
  entry: E | null
}

/**
 * 撤销一步：把 past 末位的反向补丁打回去。
 *
 * 补丁按路径应用：万一与当前文档对不上（历史损坏），**扔掉这一条并保持文档不动**——
 * 绝不能让栈和文档进入半应用的错位状态。丢弃的那条从 past 里去掉（返回值 `ok=false`、
 * `entry` 是它），不进 future。
 */
export function undoStep<D extends Objectish, E extends PatchPair>(
  doc: D,
  past: readonly E[],
  future: readonly E[],
): StepResult<D, E> {
  const entry = past.at(-1)
  if (!entry) return { ok: false, doc, past: [...past], future: [...future], entry: null }
  let next: D
  try {
    next = applyPatches(doc, entry.inverse) as D
  } catch {
    return { ok: false, doc, past: past.slice(0, -1), future: [...future], entry }
  }
  return { ok: true, doc: next, past: past.slice(0, -1), future: [entry, ...future], entry }
}

/** 重做一步：把 future 首位的正向补丁打上去。失败语义与 `undoStep` 对称。 */
export function redoStep<D extends Objectish, E extends PatchPair>(
  doc: D,
  past: readonly E[],
  future: readonly E[],
): StepResult<D, E> {
  const entry = future[0]
  if (!entry) return { ok: false, doc, past: [...past], future: [...future], entry: null }
  let next: D
  try {
    next = applyPatches(doc, entry.patches) as D
  } catch {
    return { ok: false, doc, past: [...past], future: future.slice(1), entry }
  }
  return { ok: true, doc: next, past: [...past, entry], future: future.slice(1), entry }
}

/** 取消事务：把反向补丁打回去，回到事务开始前（没有反向补丁就原样）。 */
export function rollback<D extends Objectish>(doc: D, txn: PatchPair): D {
  return txn.inverse.length ? (applyPatches(doc, txn.inverse) as D) : doc
}
