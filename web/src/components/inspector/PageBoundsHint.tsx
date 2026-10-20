import { useEffect, useLayoutEffect, useRef, type FocusEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Lightbulb, X } from '@/components/ui/icons'
import { t as translate } from '@/i18n'
import { rescueFocus } from '@/lib/focusRescue'
import { hintDismissTimer, useHintStore } from '@/lib/onboarding/hints'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'

/**
 * 「页面尺寸与纸面边界」提示（`layout_page_bounds`）：**锚在右栏的页签条下**——「画布」页签就在
 * 它正上方，一颗「打开画布页」把人带到页面尺寸那一页。非模态、不抢焦点、可关、到时自己收；
 * 右栏不在（收起 / 窄屏抽屉被互斥收掉）时 `hints.ts` 根本不出这一条，已出的也随之收起，
 * 不会留一座悬浮孤岛。它与通知轨上的提示共用 `useHintStore.current` 一个槽位，所以不会叠。
 * 出现 / 退场动画走 `usePresence`（reduced-motion 下立即卸载）与 `animate-rise-*`（同一条媒体查询关掉）。
 */
export function PageBoundsHint() {
  const { t } = useTranslation('workspace')
  const open = useHintStore((s) => s.current === 'layout_page_bounds')
  const token = useHintStore((s) => s.token)
  const presence = usePresence(open, DURATION.fast)
  const held = useRef(new Set<string>())
  const grip = (r: string) => {
    held.current.add(r)
    hintDismissTimer.hold(r)
  }
  const drop = (r: string) => {
    held.current.delete(r)
    hintDismissTimer.release(r)
  }
  // 焦点在卡片里吗：卡片收起（按钮、× 或 9 秒兜底）会把它摘掉，焦点落 body 在 WebKit 里让 Tab 卡死
  // （`lib/focusRescue.ts`）。鼠标点击不抢焦点的卡片，焦点不在里面就什么都不做。
  const focusInside = useRef(false)
  // layout 阶段：赶在浏览器因 inert / 卸载做焦点修正之前读到「焦点还在卡片里」
  useLayoutEffect(() => {
    if (open) return
    if (focusInside.current) {
      focusInside.current = false
      // 接手者：右栏页签条里当前选中的页签（「打开画布页」之后正是「画布」）
      rescueFocus(() =>
        document.querySelector<HTMLElement>('[data-inspector-tab][aria-selected="true"]'),
      )
    }
  }, [open])
  useEffect(
    () => () => {
      for (const r of held.current) hintDismissTimer.release(r)
      held.current.clear()
    },
    [],
  )
  if (!presence.mounted) return null
  return (
    <div
      key={token}
      data-onboarding-hint="layout_page_bounds"
      data-state={presence.state}
      inert={presence.state === 'closed'}
      onPointerEnter={() => grip('pointer')}
      onPointerLeave={() => drop('pointer')}
      onFocus={() => {
        focusInside.current = true
        grip('focus')
      }}
      onBlur={(e: FocusEvent<HTMLDivElement>) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          focusInside.current = false
          drop('focus')
        }
      }}
      className={cn(
        'absolute inset-x-3 top-12 z-popover flex items-start gap-2 rounded-lg bg-surface px-3 py-2 text-sm text-ink shadow-pop',
        'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
      )}
    >
      <Lightbulb size={ICON_SIZE.sm} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-1">
        <p>{t('hints.layout_page_bounds')}</p>
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 text-ink"
          onClick={() => {
            useUiStore.getState().setRightTab('canvas')
            useHintStore.getState().dismiss()
          }}
        >
          {t('hints.pageBoundsOpen')}
        </Button>
      </div>
      <Button
        size="icon-xs"
        onClick={() => useHintStore.getState().dismiss()}
        aria-label={translate('actions.close')}
        className="shrink-0 text-ink-3 hover:text-ink"
      >
        <X size={ICON_SIZE.sm} />
      </Button>
    </div>
  )
}
