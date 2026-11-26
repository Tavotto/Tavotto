import type { PlanEntry } from '@/store/changePlanStore'

/**
 * 助手里用户一侧的那条指令（改图助手先出修改方案卡时，这一轮没有会话，用户的原话由这个气泡承担）：
 * 右对齐、底与会话里的用户气泡（`Transcript.UserBubble`）同一档。没有「重新发送」之类的操作——失败 / 不满意走卡片自己的下一步。
 */
export function AssistantNoteBubble({ entry }: { entry: PlanEntry }) {
  if (!entry.instruction) return null
  return (
    <div data-change-plan-user className="flex flex-col items-end">
      <div className="flex max-w-[85%] flex-col items-end rounded-lg bg-surface-hover px-3 py-2">
        <p className="type-reading line-clamp-3 self-stretch select-text whitespace-pre-wrap break-words">{entry.instruction}</p>
      </div>
    </div>
  )
}
