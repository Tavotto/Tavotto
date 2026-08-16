/**
 * Magic Matplot 时代的 localStorage 键（mm2.* / mm3.ui）一次性搬迁到 magplot.*。
 *
 * 本模块以副作用方式在 main.tsx 的**第一条 import** 执行——各 store 在模块
 * 加载时就读 localStorage，搬迁必须先于它们。幂等：新键已存在则跳过；
 * 存储不可用时静默放弃，不影响启动。
 *
 * 注意：documentStore 里更老的单槽键 `mm2.autosave`（无点后缀）另有专门的
 * 结构迁移（migrateLegacySlot），不在这里改名。
 */
const RENAMES: [string, string][] = [
  ['mm2.docIndex', 'magplot.docIndex'],
  ['mm2.currentDoc', 'magplot.currentDoc'],
  ['mm2.assetUsed', 'magplot.assetUsed'],
  ['mm2.ai.agent', 'magplot.ai.agent'],
  ['mm3.ui', 'magplot.ui'],
]

const SLOT_PREFIX_OLD = 'mm2.autosave.'
const SLOT_PREFIX_NEW = 'magplot.autosave.'

export function migrateLegacyStorage(): void {
  try {
    const moves: [string, string][] = []
    for (const [from, to] of RENAMES) {
      if (localStorage.getItem(from) !== null && localStorage.getItem(to) === null) {
        moves.push([from, to])
      }
    }
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key?.startsWith(SLOT_PREFIX_OLD)) continue
      const to = SLOT_PREFIX_NEW + key.slice(SLOT_PREFIX_OLD.length)
      if (localStorage.getItem(to) === null) moves.push([key, to])
    }
    for (const [from, to] of moves) {
      const v = localStorage.getItem(from)
      if (v === null) continue
      localStorage.setItem(to, v)
      localStorage.removeItem(from)
    }
  } catch {
    /* 存储不可用只影响偏好恢复，不影响启动 */
  }
}

migrateLegacyStorage()
