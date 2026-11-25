import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { hasUsableAi } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { requestConnectAi } from '@/store/connectAiStore'
import { useChangePlanStore } from '@/store/changePlanStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Link2, Pencil } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Tip } from '../ui/Tooltip'
import { failureText, rs } from '../review/reviewText'
import type { ReviewPill } from './useReviewPill'

/** 点「交给 Agent 修改」：右栏切到助手，发一张审阅意见卡 + 方案卡（同一批意见重复点 = 聚焦已有的卡，由 store 去重）。没连 AI 先走「连接 AI」，连好接着做 */
export function handToAgent(pill: Pick<ReviewPill, 'panelId' | 'shareId' | 'numbers'>): void {
  const go = () => {
    useUiStore.getState().setRightTab('assistant')
    void useChangePlanStore.getState().requestFromReview(pill.panelId, { share_id: pill.shareId, numbers: pill.numbers })
  }
  const caps = useAiStore.getState().caps
  // caps 还没读到时不拦（让引擎按「没连接」给稳定码，卡片上同样有「连接 AI」）
  if (caps && !hasUsableAi(caps)) requestConnectAi({ onConnected: go })
  else go()
}

/**
 * 审阅页签里悬浮的主胶囊（`ReviewPanel` 的 `floating` 槽）：水平居中、浮在列表底部之上，这一屏的主按钮只有它。
 * 「交给 Agent 修改」→ 方案应用之后同一位置换成「更新到同一链接」（对外动作，只有用户点了才发；调现有 `reviewStore.update`，失败 / 额度照 0187）→ 更新后变灰「链接已更新」。
 */
export function ChangePlanPill({ pill }: { pill: ReviewPill }) {
  useTranslation('dialogs')
  const stage = useReviewStore((s) => s.stage)
  const [failed, setFailed] = useState<string | null>(null)
  const busy = stage === 'rendering' || stage === 'uploading'
  const { mode } = pill
  const update = async () => {
    setFailed(null)
    await useReviewStore.getState().update()
    const st = useReviewStore.getState()
    if (st.stage === 'failed' && st.failure) setFailed(st.failure.code)
  }
  const tip = mode === 'run' ? rs('changePlan.pillTip') : mode === 'update' ? rs('changePlan.pillUpdateTip') : undefined
  const btn = (
    <Button
      variant={mode === 'updated' ? 'secondary' : 'primary'}
      size="md"
      disabled={mode === 'updated' || busy}
      data-change-plan-pill={mode}
      onClick={mode === 'run' ? () => handToAgent(pill) : () => void update()}
      className="rounded-full shadow-pop"
    >
      {mode === 'run' ? <Pencil size={ICON_SIZE.sm} /> : <Link2 size={ICON_SIZE.sm} />}
      {rs(mode === 'run' ? 'changePlan.pill' : mode === 'update' ? 'changePlan.pillUpdate' : 'changePlan.pillUpdated')}
    </Button>
  )
  return (
    <div className="flex flex-col items-center gap-1.5">
      {mode === 'update' && failed && (
        <span className="max-w-64 text-center text-xs text-ink-3" data-change-plan-pill-failure={failed}>
          {failureText(failed)}
        </span>
      )}
      {tip ? (
        <Tip label={tip} side="top" tone="light">
          {btn}
        </Tip>
      ) : (
        btn
      )}
    </div>
  )
}
