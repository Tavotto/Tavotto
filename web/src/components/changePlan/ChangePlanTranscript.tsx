import { useMemo } from 'react'
import { useChangePlanStore } from '@/store/changePlanStore'
import { ChangePlanCard } from './ChangePlanCard'
import { effectiveStatus } from './changePlanModel'
import { ReviewNoteCard } from './ReviewNoteCard'
import { usePlanPanel } from './usePlanFigure'

/**
 * 助手转录里这张图的方案卡（数据来自 `changePlanStore`，按项目 + 图分键；切图 / 换项目跟着走）：
 * 一次「交给 Agent 修改」= 一张审阅意见卡（用户一侧）+ 一张修改方案卡（助手一侧）。最新一张**待确认**的方案卡「查看」实心，旧的降次级。
 * 作为转录列表的子项直接排在同一个 flex 容器里（节奏由容器的 gap 管）。
 */
export function ChangePlanTranscript({ panelId }: { panelId: string }) {
  const order = useChangePlanStore((s) => s.order)
  const entries = useChangePlanStore((s) => s.entries)
  const mine = useMemo(() => order.map((k) => entries[k]).filter((e) => e && e.panelId === panelId), [order, entries, panelId])
  const panel = usePlanPanel(mine[0] ?? { panelId, documentId: '', loadSeq: 0, canvasId: '' })
  const latest = [...mine].reverse().find((e) => effectiveStatus(e, panel?.overrides ?? null) === 'ready')?.key
  return (
    <>
      {mine.map((e) => (
        <div key={e.key} className="flex flex-col gap-(--item-gap)" data-change-plan-turn={e.key}>
          <ReviewNoteCard entry={e} />
          <ChangePlanCard entry={e} latest={e.key === latest} />
        </div>
      ))}
    </>
  )
}
