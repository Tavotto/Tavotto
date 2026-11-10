import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { X } from '@/components/ui/icons'
import { t as translate } from '@/i18n'
import { DURATION, usePresence } from '@/lib/motion'
import {
  dismissCardForGood,
  hintDismissTimer,
  isCardHint,
  useHintStore,
  type CardHintKind,
} from '@/lib/onboarding/hints'
import { useEffectiveTheme } from '@/lib/theme'
import { ALT, cn } from '@/lib/utils'
import { useDialogLayerStore } from '@/store/dialogLayerStore'
import { fitStage } from '@/store/zoomToSelection'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { useNoticeRailStore } from '@/store/noticeRailStore'
import { useUiStore } from '@/store/uiStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'
import { usePrepCardClearance } from '../FeedbackCorner'

// 场景连同 vendor/stageshow 整块按需加载：不出提示的会话一个字节都不下
const HintScene = lazy(() => import('./scenes'))

/** 动画演几遍后停在定格帧（之后指针移到卡上再演） */
export const HINT_CARD_LOOPS = 2
const STAGE_W = 320
const STAGE_H = 180

/** 卡上那一颗按钮：只给「一步就能替用户做完」的提示 */
const ACTIONS: Partial<Record<CardHintKind, { label: string; run: () => void }>> = {
  // `fitStage` 自己会发 `view.fitted` → 提示引擎记「学会」并收起这张卡
  fit_canvas: { label: 'topbar.fitCanvas', run: fitStage },
}

/**
 * 动画卡提示（ADR 0177）：**窗口右下角**的一张浮卡——上面一段小动画，下面一句话 + 至多一颗按钮 + ×。
 * 非模态、不抢焦点、不占布局；与通知轨上的一句话提示共用 `useHintStore.current` 一个槽位。
 * 位置与宽度同反馈卡（`FeedbackCorner`）：属性栏开着时它压在属性栏底部，不盖用户正在摆弄的画布
 * （放画布左下角试过：窄窗口里一张 320 宽的卡盖住半张图，还和底部居中的通知轨抢位置）。
 * 反馈卡在场时让位（不画、不收起）；与准备引导卡相交时按反馈卡同一套避让；属性栏收起、卡落在画布上时抬到通知轨之上。
 * 读屏的那一句由通知轨的活动区播（它按 `current` 取文案），这里不再加 aria-live，免得播两遍。
 * × = 这条别再提；到时自己收起 / 场景变了被收起 = 下次还可以再出一次（`hints.ts` 记账）。
 * 有模态对话框开着时不画（与轨上的提示同一条规则），关掉对话框后回来。
 */
export function HintCard() {
  const { t } = useTranslation('workspace')
  const current = useHintStore((s) => s.current)
  const token = useHintStore((s) => s.token)
  const overModal = useDialogLayerStore((s) => s.open > 0)
  // 反馈卡（意外错误 / 上次没正常退出）占着这个角：让位
  const unexpected = useUnexpectedErrorStore((s) => !!s.card)
  const previousExit = useExitNoticeStore((s) => !!s.previous)
  const cornerBusy = unexpected || previousExit
  // 属性栏收起时卡落在画布上：通知轨有东西就抬到它上面，别盖住通知上的按钮
  const railTop = useNoticeRailStore((s) => s.top)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const cardRef = useRef<HTMLDivElement | null>(null)
  const theme = useEffectiveTheme()
  const kind = isCardHint(current) ? current : null
  const presence = usePresence(!!kind && !overModal && !cornerBusy, DURATION.fast)
  const clearance = usePrepCardClearance(presence.mounted, cardRef)
  // 退场那一小段 current 已经空了：留着最后一张把动画播完
  const last = useRef<CardHintKind | null>(null)
  if (kind) last.current = kind
  const shown = kind ?? last.current

  // 演够两遍停在定格帧；换一张卡 / 重新出现时从头数
  const loops = useRef(0)
  const [resting, setResting] = useState(false)
  useEffect(() => {
    loops.current = 0
    setResting(false)
  }, [token])

  const held = useRef(new Set<string>())
  const grip = (r: string) => {
    held.current.add(r)
    hintDismissTimer.hold(r)
  }
  const drop = (r: string) => {
    held.current.delete(r)
    hintDismissTimer.release(r)
  }
  useEffect(
    () => () => {
      for (const r of held.current) hintDismissTimer.release(r)
      held.current.clear()
    },
    [],
  )

  if (!presence.mounted || !shown) return null
  const action = ACTIONS[shown]
  const overRail =
    !rightOpen && railTop !== null && typeof window !== 'undefined'
      ? { bottom: Math.max(16, window.innerHeight - railTop + 8) }
      : null
  return (
    <div
      ref={cardRef}
      data-hint-card={shown}
      style={clearance ?? overRail ?? undefined}
      data-state={presence.state}
      inert={presence.state === 'closed'}
      onPointerEnter={() => grip('pointer')}
      onPointerLeave={() => drop('pointer')}
      onFocus={() => grip('focus')}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) drop('focus')
      }}
      className={cn(
        'fixed bottom-4 right-4 z-drawer w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg bg-surface shadow-pop',
        'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
      )}
    >
      <div
        data-hint-card-stage
        aria-hidden
        className="border-b border-border bg-canvas"
        style={{ aspectRatio: `${STAGE_W} / ${STAGE_H}` }}
      >
        <Suspense fallback={null}>
          <HintScene
            key={`${shown}-${token}-${resting ? 'rest' : 'run'}`}
            kind={shown}
            theme={theme}
            play={resting ? 'hover' : 'loop'}
            onLoop={() => {
              loops.current += 1
              if (loops.current >= HINT_CARD_LOOPS) setResting(true)
            }}
          />
        </Suspense>
      </div>
      <div className="flex min-h-10 items-center gap-1 py-1 pl-3 pr-1.5">
        <p data-hint-card-text className="min-w-0 flex-1 text-sm text-ink">
          {t(`hints.${shown}`, { alt: ALT })}
        </p>
        {action && (
          <Button variant="ghost" size="sm" data-hint-card-action className="shrink-0 text-ink" onClick={action.run}>
            {translate(action.label, { ns: 'workspace' })}
          </Button>
        )}
        <Button
          size="icon-xs"
          data-hint-card-close
          onClick={() => dismissCardForGood(shown)}
          aria-label={translate('actions.close')}
          className="shrink-0 text-ink-3 hover:text-ink"
        >
          <X size={ICON_SIZE.sm} />
        </Button>
      </div>
    </div>
  )
}
