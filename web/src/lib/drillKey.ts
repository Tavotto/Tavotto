import type { ProblemDrill } from './problemList'

/**
 * 卡片的稳定机器标识（`data-problem-card-key`）：新手教程与 e2e 要指向「这条问题
 * 所在的那张卡片」，靠它而不是文案。
 *
 * 叶子模块（只有类型依赖）：`uiStore.setProblemDrill` 在运行时拿它判「同一现场里换没换支」，
 * 从 `problemList` 取会把 preflight / validation 一串拖进 store 的 import 环。`problemList` 原样再导出。
 */
export function drillKey(drill: ProblemDrill): string {
  switch (drill.kind) {
    case 'unverifiable':
      return 'unverifiable'
    case 'part':
      return `part:${drill.figure}:${drill.key}`
    default:
      return `${drill.kind}:${drill.key}`
  }
}
