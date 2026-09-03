/**
 * 教程步骤的**稳定 id 与顺序**——整个 onboarding 里唯一的一份。
 *
 * 单独成文件、不 import 任何 store：`store/onboardingStore` 要按它做 back /
 * 迁移（过滤掉已经不存在的步骤），`lib/onboarding/steps` 要按它定义完成条件，
 * 两边都 import 这里，谁都不 import 谁。
 *
 * **id 是持久化格式的一部分**：改名 = 老用户本机记的「完成到第几步」全部
 * 失效（迁移会把认不出的 id 过滤掉，回到第一个未完成的步骤）。要改步骤内容
 * 就升 `ONBOARDING_FLOW_VERSION`，不要换 id。
 */
export const STEP_IDS = [
  'welcome',
  'open_fast_edit',
  'select_text',
  'change_typography',
  'locate_problem',
  'export_original',
  'add_to_layout',
  'multi_select_align',
  'export_canvas',
  'done',
] as const

export type StepId = (typeof STEP_IDS)[number]

export const isStepId = (v: unknown): v is StepId =>
  typeof v === 'string' && (STEP_IDS as readonly string[]).includes(v)

export const stepIndex = (id: StepId): number => STEP_IDS.indexOf(id)

/** 第一个不在 `done` 集合里的步骤；全完成了就回 `done` */
export function firstIncomplete(done: ReadonlySet<string>): StepId {
  for (const id of STEP_IDS) if (!done.has(id)) return id
  return 'done'
}
