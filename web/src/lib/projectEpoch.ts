/**
 * 「发起时的项目」守卫：凡是 await 之后还要改文档 / 画布 / 工作区 / 其它 store 的异步函数，
 * 发起时 `captureProjectEpoch()`，每个 await 之后 `if (!guard.still()) return`。
 *
 * 认的是**项目代际**而不是项目 id：A → B → A 回到同一个 id 也已换代（绑定的项目每变一次计一代）。
 * 需要再叠一层自己的代（如 store 的 `epoch`）就传 `extra`，两者任一变了都算不再有效。
 * 不再有效 = 整个丢弃，不改文档、不改工作区、不改任何 store（A 的描述符 / 素材 id 绝不落进 B）。
 */
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'

let generation = 0
onCurrentProjectChange(() => {
  generation += 1
})

export interface ProjectEpochGuard {
  /** 发起时的项目 id（可能为 null：跟随后端默认项目） */
  readonly pj: string | null
  /** 还是发起时的那个项目、那一代吗？ */
  still: () => boolean
}

export function captureProjectEpoch(extra?: () => unknown): ProjectEpochGuard {
  const pj = currentProjectId()
  const gen = generation
  const x = extra?.()
  return {
    pj,
    still: () => generation === gen && currentProjectId() === pj && (!extra || extra() === x),
  }
}
