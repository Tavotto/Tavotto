import { useEffect, useRef, useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleAlert, X } from '@/components/ui/icons'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { DURATION, usePresence } from '@/lib/motion'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'

/** 卡离窗口右 / 下边的距离（`right-4` / `bottom-4`）与卡宽（`w-80`），避让计算用 */
const EDGE_PX = 16
const CARD_W_PX = 320
const GAP_PX = 8

type Clearance = { bottom: number } | { right: number } | null

/** 几个矩形的外包框（没有就是 undefined） */
type Box = { left: number; top: number; right: number; bottom: number }
function union(rects: DOMRect[]): Box | undefined {
  if (rects.length === 0) return undefined
  const left = Math.min(...rects.map((r) => r.left))
  const top = Math.min(...rects.map((r) => r.top))
  const right = Math.max(...rects.map((r) => r.right))
  const bottom = Math.max(...rects.map((r) => r.bottom))
  return { left, top, right, bottom }
}

/**
 * 与准备引导卡（`PreparationCard`，画布右下角）不叠（Codex #1184）：属性栏收起时两张卡都在右下角，
 * 这张卡会盖住准备卡底部的按钮。只在这张卡显示时观察 `[data-prep-card]` / `[data-prep-pill]`（出现 / 消失 / 尺寸）、它的各级定位祖先的尺寸
 * （拖窄属性栏时准备卡自己尺寸不变、是画布变宽把它带走的，Codex #1184）与自己的高度，
 * 水平相交时按序挑一个**整张都在窗口里**的位置：① 准备卡上方 8px（上面放得下这张卡的实测高度）；
 * ② 准备卡左侧 8px、贴底（左边放得下卡宽）；③ 都放不下（窗口太小）就留在默认位置——宁可叠，也不把卡推出窗口。
 */
function usePrepCardClearance(active: boolean, self: RefObject<HTMLElement | null>): Clearance {
  const [clearance, setClearance] = useState<Clearance>(null)
  useEffect(() => {
    if (!active || typeof document === 'undefined') {
      setClearance(null)
      return
    }
    const observed = new Set<Element>()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => measure())
    const watch = (el: Element | null | undefined) => {
      if (el && !observed.has(el)) {
        observed.add(el)
        ro?.observe(el)
      }
    }
    function measure() {
      // 准备引导卡有两种形态：展开的卡与收起后的角标（`data-prep-pill`），都住在同一个角上
      const preps = Array.from(document.querySelectorAll('[data-prep-card], [data-prep-pill]'))
      const prep = preps[0] ?? null
      preps.forEach(watch)
      // 准备卡贴着定位祖先的右下角：祖先变宽 / 变高它就跟着挪，自己尺寸不变、DOM 也没增删
      for (let a = (prep as HTMLElement | null)?.offsetParent; a && a !== document.body; a = (a as HTMLElement).offsetParent)
        watch(a)
      watch(self.current)
      const r = union(preps.map((el) => el.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0))
      const vw = window.innerWidth
      const vh = window.innerHeight
      const left = vw - EDGE_PX - CARD_W_PX
      const overlaps = !!r && r.right > left && r.left < vw - EDGE_PX
      let next: Clearance = null
      if (overlaps) {
        const h = self.current?.getBoundingClientRect().height ?? 0
        if (r.top - GAP_PX - h >= EDGE_PX) next = { bottom: Math.round(vh - r.top + GAP_PX) }
        else if (r.left - GAP_PX - CARD_W_PX >= EDGE_PX) next = { right: Math.round(vw - r.left + GAP_PX) }
      }
      setClearance((cur) => (JSON.stringify(cur) === JSON.stringify(next) ? cur : next))
    }
    measure()
    const mo = new MutationObserver(measure)
    mo.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', measure)
    return () => {
      mo.disconnect()
      ro?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [active, self])
  return clearance
}

type Prompt =
  | { slot: 'unexpected'; token: number }
  | { slot: 'previous'; token: number; kind: string }

/**
 * 右下角的问题反馈卡（2026-10-19，用户：「一键反馈只出现在胶囊内，很多人不知道点」）。
 *
 * 此前「出了点意外」与「上次没有正常退出」是底部通知轨里的两条胶囊，「发送问题反馈」是胶囊里一颗 ghost 按钮——
 * 一行高、和「渲染完成」长得一样，用户读不出它在请自己做点什么。现在单独成卡、贴窗口右下角：
 * 红色图标 + 一句标题 + 一句为什么 + 一颗主按钮「发送问题反馈」+ ×。
 *
 * - **只认意外错误**（用户 10-19 裁决）：来源仍是 `unexpectedErrorStore`（判据在 `lib/unexpectedErrors.ts`）与
 *   `exitNoticeStore.previous`；用户脚本报错、导出失败这类可预期的失败不弹这张卡。
 * - **同时只有一张**：两类都在时先说意外错误（刚发生的事），它关掉后才轮到「上次没有正常退出」。
 * - **主动作只打开确认框**（`diagSendStore.setOpen(true)`，本机备包）并收起**两类**卡——一份诊断包已覆盖两件事；
 *   只收当前那张的话，排在后面的那张会立刻冒出来，且 `z-toast` 高于 `z-dialog`，压在表单上（Codex #1184）。
 *   上传要等用户在框里点「发送」。确认框开着时整个角落不渲染（store 不动）。
 * - 发送入口关着（`capability === null`）时整张卡不渲染，与此前的判据相同。
 */
export function FeedbackCorner() {
  const { t } = useTranslation('workspace')
  const diagEnabled = useDiagSendStore((s) => s.capability !== null)
  const sendOpen = useDiagSendStore((s) => s.open)
  const unexpected = useUnexpectedErrorStore((s) => s.card)
  const previous = useExitNoticeStore((s) => s.previous)

  const current: Prompt | null = !diagEnabled || sendOpen
    ? null
    : unexpected
      ? { slot: 'unexpected', token: unexpected.token }
      : previous
        ? { slot: 'previous', token: previous.token, kind: previous.kind }
        : null
  // 退场那一小段时间 store 已经清空了：留最后一张播完退场，不出现空壳
  const last = useRef<Prompt | null>(null)
  if (current) last.current = current
  const presence = usePresence(!!current, DURATION.fast)
  const shown = current ?? last.current
  const cardRef = useRef<HTMLElement | null>(null)
  const clearance = usePrepCardClearance(presence.mounted, cardRef)

  const dismiss = (p: Prompt) => {
    if (p.slot === 'unexpected') useUnexpectedErrorStore.getState().dismiss()
    else useExitNoticeStore.getState().dismissPrevious()
  }
  const openSend = () => {
    useUnexpectedErrorStore.getState().dismiss()
    useExitNoticeStore.getState().dismissPrevious()
    useDiagSendStore.getState().setOpen(true)
  }

  const title = (p: Prompt | null) =>
    p ? t(p.slot === 'unexpected' ? 'unexpectedError.text' : 'exitNotice.previous.text') : ''
  const body = (p: Prompt) => t(p.slot === 'unexpected' ? 'unexpectedError.body' : 'exitNotice.previous.body')

  return (
    <div
      data-feedback-corner
      style={clearance ?? undefined}
      // 层级 z-drawer：在画布浮层 / 准备卡之上，但在任何对话框的遮罩（z-overlay）与对话框（z-dialog）之下——
      // 哪个对话框开着都自然盖住它，不用逐个登记（Codex #1184；此前 z-toast 会压在设置等对话框上）
      className="pointer-events-none fixed bottom-4 right-4 z-drawer flex w-80 max-w-[calc(100vw-2rem)] flex-col items-end"
    >
      <div aria-live="polite" className="sr-only">
        {current ? title(current) : ''}
      </div>
      {presence.mounted && shown && (
        <section
          ref={cardRef}
          key={`${shown.slot}-${shown.token}`}
          aria-label={title(shown)}
          data-state={presence.state}
          inert={presence.state === 'closed'}
          {...(shown.slot === 'unexpected'
            ? { 'data-unexpected-error-card': '' }
            : { 'data-previous-exit-card': shown.kind })}
          className="pointer-events-auto relative flex w-full gap-3 rounded-lg bg-surface p-3.5 pr-10 shadow-pop data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out"
        >
          <span aria-hidden className="flex size-8 shrink-0 items-center justify-center rounded-full bg-danger-surface text-danger">
            <CircleAlert size={ICON_SIZE.md} />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="text-sm font-medium text-ink">{title(shown)}</p>
            <p className="text-sm leading-5 text-ink-2">{body(shown)}</p>
            <div className="mt-2 flex">
              <Button variant="primary" size="sm" onClick={openSend} data-feedback-corner-send>
                {t('settings.diagnostics.send.cardButton', { ns: 'dialogs' })}
              </Button>
            </div>
          </div>
          <Button
            size="icon-xs"
            onClick={() => dismiss(shown)}
            aria-label={t('actions.close', { ns: 'common' })}
            data-feedback-corner-close
            className="absolute right-2 top-2 text-ink-3 hover:text-ink"
          >
            <X size={ICON_SIZE.sm} />
          </Button>
        </section>
      )}
    </div>
  )
}
