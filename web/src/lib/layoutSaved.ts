/**
 * 「排版成功写进了一个文件」——保存侧发、时间线订阅（ADR 0101 §7）。
 *
 * 为什么是事件而不是保存侧直接调 `markMoment('save')`：写排版文件的路不止一条，
 * 而且还会变——⌘S 存本机、另存为画布文件、#674 的「写回绑定的项目文件」与
 * 「第一次存进项目」的命名框。让每一条都 import 时间线，等于把「保存」与「时间线」
 * 的合并顺序绑在一起：哪条路先合，哪条路就可能忘了打点（#674 的提前 return 就这样
 * 漏过一次）。事件把依赖反过来：保存侧只说「写成了」，不关心谁在听；时间线在
 * `startVersionCheckpoints` 里订阅。新增一条保存路径 = 在它的成功分支 emit 一次。
 *
 * 纯同步、无依赖；`via` 只用来区分来路（诊断与用例），不参与判据。
 *
 * `moment`（必填）：**写出去的那份**的快照（`captureMoment()`，与序列化那份文档同一刻取）。
 * 保存要 await：完成时用户可能已经换了排版（点只打给被保存的那一份，换走了就不打），也可能
 * 在同一份里接着改（节点拍的是写出去的那份，不是之后的样子）——Codex #679。必填是故意的：
 * 新增一条保存路径时，类型检查会逼它想清楚「存的是哪一刻」。
 */
import type { MomentSnapshot } from './timelineCheckpoint'

export type LayoutSavedVia =
  /** ⌘S：本机自动保存写完（没绑定项目文件时） */
  | 'local'
  /** 另存为画布文件（⇧⌘S 的命名框；#674 起也是「第一次存进项目」那一框） */
  | 'layout_file'
  /** #674：写回已经绑定的项目文件 */
  | 'project_file'

export interface LayoutSavedEvent {
  /** 写出去的那份：上下文 + 文档 + 面板图源 */
  moment: MomentSnapshot
}

type Listener = (via: LayoutSavedVia, event: LayoutSavedEvent) => void
const listeners = new Set<Listener>()

export function emitLayoutSaved(via: LayoutSavedVia, event: LayoutSavedEvent): void {
  for (const fn of [...listeners]) {
    try {
      fn(via, event)
    } catch {
      /* 听众出错不影响保存本身 */
    }
  }
}

export function onLayoutSaved(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
