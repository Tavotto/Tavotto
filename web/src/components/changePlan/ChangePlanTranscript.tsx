import { useMemo } from 'react'
import { BookOpen, ListChecks } from '../ui/icons'
import { isCanvasPlan, isTodoPlan, useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { openJournalPicker } from '@/store/journalPickerRequest'
import { journalCatalogUi } from '@/lib/journalUi'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import { ThinkingOrb } from '@/components/ui/ThinkingOrb'
import { Markdown } from '../ai/Markdown'
import { AssistantNoteBubble } from './AssistantNoteBubble'
import { ChangePlanCard } from './ChangePlanCard'
import { cp, effectiveStatus, planUndecided, todoScriptCardShown } from './changePlanModel'
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
        .filter((e) => e && (isCanvasPlan(e) ? e.documentId === documentId && e.loadSeq === loadSeq && e.canvasId === canvasId : !!panelId && e.panelId === panelId)),
    [order, entries, panelId, documentId, loadSeq, canvasId],
  )
}

/** 最新一张**待确认**的方案卡（它的「查看」实心，旧的降次级） */
export function useLatestReadyKey(mine: PlanEntry[], panelId: string): string | undefined {
  const panel = usePlanPanel(mine.find((e) => !isCanvasPlan(e)) ?? { panelId, documentId: '', loadSeq: 0, canvasId: '' })
  const source = usePlanSource(panel)
  useDocumentStore((s) => s.past.at(-1))
  useDocumentStore((s) => s.future.at(-1))
  return [...mine].reverse().find((e) => !e.todoOf && effectiveStatus(e, panel?.overrides ?? null, source) === 'ready')?.key
}

/** 一次方案的一轮：审阅来源 = 审阅意见卡 + 方案卡；助手来源 = 用户的指令气泡 + 方案卡（交给改脚本之后只剩那一行灰字，气泡由改脚本那一轮画） */
/** 提问里提到期刊 / 投稿 / 标准 / 规范：回答之后顺手给「选期刊」 */
const JOURNAL_ASK = /期刊|投稿|标准|规范|journal/i

export function ChangePlanTurn({ entry, latest }: { entry: PlanEntry; latest: boolean }) {
  // 「改脚本做剩下的」的脚本方案卡：同一条指令只画一次用户气泡——前一张卡还显示着它就不再画（前一张已收成一行灰字则由这张画）
  const prevShowsBubble = useChangePlanStore((s) => {
    const prev = entry.followsKey ? s.entries[entry.followsKey] : undefined
    return !!prev && prev.status !== 'handoff'
  })
  const noJournal = useDocumentStore((s) => !s.doc.profile?.journalRef)
  // 审阅来源的脚本方案卡接在原审阅卡后面：那几条意见已经在上面的审阅意见卡里，不再画第二张
  const prevExists = useChangePlanStore((s) => !!entry.followsKey && !!s.entries[entry.followsKey])
  // 建议待办卡自动续出来的脚本方案卡：生成中 / 已应用 / 要回答数据变化都由那张建议卡说话；失败 / 越界 / 图已改过时才露面（走现有的卡）
  // 改数据要先问的那一句也在建议卡里问（不单画这张卡）；答了「先不改」（本地码）同样不画，那一项就是「没改」
  // 没做成的原因也并进建议卡（一条一句），只有越界那张带「恢复」动作的卡还单独露面（`todoScriptCardShown`）
  if (entry.todoOf && !todoScriptCardShown(entry)) return null
  // 助手给建议：回答画成助手正文（与对话里的助手文字同一排版，不是气泡），待办卡在它下面
  const reply = entry.from === 'assistant' ? entry.plan?.reply : undefined
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
      ) : entry.status === 'answered' ? (
        reply ? (
          <div className="animate-settle-in flex flex-col items-start gap-1" data-change-plan-reply>
            <Markdown text={reply} />
            {/* 两个下一步同一排：写成待办 / 选期刊（agy 审后的同一款安静按钮），都没有就不占行 */}
            {(!entry.todosKey || (noJournal && journalCatalogUi() && JOURNAL_ASK.test(entry.instruction ?? ''))) && (
              <div className="-ml-2.5 flex flex-wrap items-center gap-1" data-change-plan-reply-actions>
                {/* 提问只回答：想让它动手，点这里把上面的建议写成待办卡（点过就不再出现） */}
                {!entry.todosKey && (
                  <Button variant="ghost" size="sm" className="gap-1.5 text-ink-2 hover:text-ink" onClick={() => void useChangePlanStore.getState().requestTodos(entry.key)} data-change-plan-to-todos>
                    {/* 一个清单小图标 + 深一档的字：一眼认得出是下一步的入口，不是回答的尾注（agy 审 2026-11-27） */}
                    <ListChecks size={ICON_SIZE.sm} aria-hidden className="shrink-0" />
                    {cp('todo.toTodos')}
                  </Button>
                )}
                {/* 提问提到期刊、而这份排版还没选期刊：再给一个安静的「选期刊」，打开问题页签顶部的期刊选择器 */}
                {noJournal && journalCatalogUi() && JOURNAL_ASK.test(entry.instruction ?? '') && (
                  <Button variant="ghost" size="sm" className="gap-1.5 text-ink-2 hover:text-ink" onClick={openJournalPicker} data-change-plan-pick-journal>
                    <BookOpen size={ICON_SIZE.sm} aria-hidden className="shrink-0" />
                    {cp('todo.pickJournal')}
                  </Button>
                )}
              </div>
            )}
          </div>
        ) : null
      ) : entry.from === 'assistant' ? (
        // 从思考那一行长出来：与对话新一轮同一个落位动效（agy 审 2026-11-27：别硬切成一张大卡）
        <div className="animate-settle-in flex flex-col gap-(--item-gap)">
          {reply && isTodoPlan(entry) && (
            <div data-change-plan-reply>
              <Markdown text={reply} />
            </div>
          )}
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
