import { useMemo } from 'react'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { IconSlot } from '@/components/ui/Icon'
import { ThinkingOrb } from '@/components/ui/ThinkingOrb'
import { AssistantNoteBubble } from './AssistantNoteBubble'
import { ChangePlanCard } from './ChangePlanCard'
import { cp, effectiveStatus, planUndecided } from './changePlanModel'
import { ReviewNoteCard } from './ReviewNoteCard'
import { usePlanPanel, usePlanSource } from './usePlanFigure'

/**
 * 助手里要画的方案条目（创建顺序，旧 → 新）：这张图的，加上当前这份文档 / 画布上一键排版的整版方案
 * （不挂在某张图上，没选图时也看得到）。
 */
export function usePanelPlanEntries(panelId: string | undefined): PlanEntry[] {
  const order = useChangePlanStore((s) => s.order)
  const entries = useChangePlanStore((s) => s.entries)
  const documentId = useDocumentStore((s) => s.documentId)
  const loadSeq = useDocumentStore((s) => s.loadSeq)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  return useMemo(
    () =>
      order
        .map((k) => entries[k])
        .filter((e) => e && (e.kind === 'layout' ? e.documentId === documentId && e.loadSeq === loadSeq && e.canvasId === canvasId : !!panelId && e.panelId === panelId)),
    [order, entries, panelId, documentId, loadSeq, canvasId],
  )
}

/** 最新一张**待确认**的方案卡（它的「查看」实心，旧的降次级） */
export function useLatestReadyKey(mine: PlanEntry[], panelId: string): string | undefined {
  const panel = usePlanPanel(mine.find((e) => e.kind !== 'layout') ?? { panelId, documentId: '', loadSeq: 0, canvasId: '' })
  const source = usePlanSource(panel)
  useDocumentStore((s) => s.past.at(-1))
  useDocumentStore((s) => s.future.at(-1))
  return [...mine].reverse().find((e) => effectiveStatus(e, panel?.overrides ?? null, source) === 'ready')?.key
}

/** 一次方案的一轮：审阅来源 = 审阅意见卡 + 方案卡；助手来源 = 用户的指令气泡 + 方案卡（交给改脚本之后只剩那一行灰字，气泡由改脚本那一轮画） */
export function ChangePlanTurn({ entry, latest }: { entry: PlanEntry; latest: boolean }) {
  // 「改脚本做剩下的」的脚本方案卡：同一条指令只画一次用户气泡——前一张卡还显示着它就不再画（前一张已收成一行灰字则由这张画）
  const prevShowsBubble = useChangePlanStore((s) => {
    const prev = entry.followsKey ? s.entries[entry.followsKey] : undefined
    return !!prev && prev.status !== 'handoff'
  })
  // 审阅来源的脚本方案卡接在原审阅卡后面：那几条意见已经在上面的审阅意见卡里，不再画第二张
  const prevExists = useChangePlanStore((s) => !!entry.followsKey && !!s.entries[entry.followsKey])
  return (
    <div className="flex flex-col gap-(--item-gap)" data-change-plan-turn={entry.key}>
      {entry.from === 'assistant' ? entry.status !== 'handoff' && !prevShowsBubble && <AssistantNoteBubble entry={entry} /> : !(entry.followsKey && prevExists) && <ReviewNoteCard entry={entry} />}
      {planUndecided(entry) ? (
        <p className="flex items-center gap-1 text-sm text-ink-3" data-change-plan-thinking>
          <IconSlot className="mr-1">
            <ThinkingOrb form="connecting" />
          </IconSlot>
          <span className="text-shimmer" role="status">
            {cp('card.thinking')}
          </span>
        </p>
      ) : entry.from === 'assistant' ? (
        // 从思考那一行长出来：与对话新一轮同一个落位动效（agy 审 2026-11-27：别硬切成一张大卡）
        <div className="animate-settle-in">
          <ChangePlanCard entry={entry} latest={latest} />
        </div>
      ) : (
        <ChangePlanCard entry={entry} latest={latest} />
      )}
    </div>
  )
}

/**
 * 助手转录里这张图的方案卡（数据来自 `changePlanStore`，按项目 + 图分键；切图 / 换项目跟着走）：
 * 一次「交给 Agent 修改」= 一张审阅意见卡（用户一侧）+ 一张修改方案卡（助手一侧）。最新一张**待确认**的方案卡「查看」实心，旧的降次级。
 * 作为转录列表的子项直接排在同一个 flex 容器里（节奏由容器的 gap 管）。
 * 助手面板里它与改脚本的会话按时间穿插（`AiPanel` 用 `usePanelPlanEntries` + `ChangePlanTurn` 自己排）；这个整块版本给单测与只有方案的场合。
 */
export function ChangePlanTranscript({ panelId }: { panelId: string }) {
  const mine = usePanelPlanEntries(panelId)
  const latest = useLatestReadyKey(mine, panelId)
  return (
    <>
      {mine.map((e) => (
        <ChangePlanTurn key={e.key} entry={e} latest={e.key === latest} />
      ))}
    </>
  )
}
