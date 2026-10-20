/**
 * 教程步骤的**稳定 id 与顺序**——整个 onboarding 里唯一的一份。
 *
 * 单独成文件、不 import 任何 store：`store/onboardingStore` 要按它做 back /
 * 迁移（过滤掉已经不存在的步骤），教程界面要按它排步骤，两边都 import 这里，
 * 谁都不 import 谁。
 *
 * **id 是持久化格式的一部分**：改名 = 老用户本机记的「完成到第几步」全部
 * 失效（迁移会把认不出的 id 过滤掉，回到第一个未完成的步骤）。要改步骤内容
 * 就升 `ONBOARDING_FLOW_VERSION`，不要换 id。
 *
 * 2026-10-19（flow v3，ADR 0136）：八步的工作台 coachmark 教程换成三步的原生演示教程
 * （拖入示例脚本 → 拖动图例 → 改标题字号）。旧 id 不复用：迁移把它们滤掉，进行中 /
 * 暂停的用户落到新的第一步，已完成 / 已跳过的用户不被重新打扰。欢迎页与完成页不是
 * 步骤——欢迎页是入口视图，`done` 是完成页（「开始使用」之前的那一屏）。
 */
export const STEP_IDS = ['demo_import', 'demo_move_legend', 'demo_change_title', 'done'] as const

export type StepId = (typeof STEP_IDS)[number]

export const isStepId = (v: unknown): v is StepId =>
  typeof v === 'string' && (STEP_IDS as readonly string[]).includes(v)

export const stepIndex = (id: StepId): number => STEP_IDS.indexOf(id)

/** 第一个不在 `done` 集合里的步骤；全完成了就回 `done` */
export function firstIncomplete(done: ReadonlySet<string>): StepId {
  for (const id of STEP_IDS) if (!done.has(id)) return id
  return 'done'
}

/** 真正要用户做事的步骤：去掉完成页。进度「第 n 步，共 N 步」按它 */
export const REAL_STEP_IDS: readonly StepId[] = STEP_IDS.filter((id) => id !== 'done')

/** 下一步；已是最后一个就 null */
export const nextStepId = (id: StepId): StepId | null => {
  const i = STEP_IDS.indexOf(id)
  return i >= 0 && i + 1 < STEP_IDS.length ? STEP_IDS[i + 1] : null
}
