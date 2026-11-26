/**
 * 改图助手「这条指令先出修改方案卡，还是照旧改脚本」的判定（ADR 0206 PR 4）。纯函数，判据只在这一处。
 *
 * 走方案路径 = **全部**成立：
 * 1. 开关开着（`ai.change_plan.enabled`，后端经 capabilities 给；缺省 / 老后端 = 开）；
 * 2. 这一发是输入框里发的，或历史气泡「重新发送」；**不是**失败后的「重试」（带 `pinned` 目标的）——重试要原样重做那一次；
 * 3. 没附参考图（结构化调用只有文字；带图的指令只有改脚本那条路读得到）；
 * 4. 指令不长于 `MAX_ASSISTANT_INSTRUCTION`（与后端 `changeplan.MAX_INSTRUCTION_CHARS` 同值）；
 * 5. 有这张图、不是位图、有脚本（有图内编辑入口）、不是 artifact 来源驱动的图；
 * 6. 不在内嵌画布 / playground（没有后端事务）；
 * 7. 这张图上没有别的方案在生成。
 *
 * 其余（连没连上能用的模型 / native / 没脚本……）后端同步判：被拒时 `changePlanStore.requestFromAssistant` 回 `'fallthrough'`，
 * 调用方照旧发（用户的指令一个字不丢）。数据出图、修复会话、转可编辑等别的任务类型根本不经过这条发送路径。
 */
import { ArtifactValidationError, refuseArtifactOperation } from '@/lib/artifactValidation'
import type { PanelObject } from '@/types/document'

/** 与后端 `changeplan.MAX_INSTRUCTION_CHARS` 同值 */
export const MAX_ASSISTANT_INSTRUCTION = 2000

export interface AssistantRouteInput {
  /** capabilities 的 `change_plan_enabled`（还没探测到 / 老后端 = undefined = 开） */
  enabled: boolean | undefined
  source: 'composer' | 'history'
  /** 重试带的、钉在那一次上的目标 */
  pinned: boolean
  /** 输入框里有参考图（含还在上传的） */
  hasRefs: boolean
  /** 指令（trim 之后）的长度 */
  length: number
  panel: PanelObject | undefined
  /** 内嵌画布 / playground（没有后端事务） */
  embedded: boolean
  /** 这张图上已经有一份方案在生成 */
  planBusy: boolean
}

export function assistantUsesPlan(i: AssistantRouteInput): boolean {
  if (i.enabled === false || i.embedded || i.planBusy || i.pinned || i.hasRefs) return false
  if (i.length === 0 || i.length > MAX_ASSISTANT_INSTRUCTION) return false
  const p = i.panel
  if (!p || p.fileKind === 'raster' || !p.script) return false
  try {
    refuseArtifactOperation(p)
  } catch (err) {
    if (err instanceof ArtifactValidationError) return false
    throw err
  }
  return true
}
