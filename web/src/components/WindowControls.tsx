import { type ReactNode, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, Minus, Square, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  onWindowMaximizedChange,
  windowChrome,
  windowControl,
  type WindowControlAction,
} from '@/lib/desktop'
import { cn } from '@/lib/utils'

/**
 * Windows 桌面版右上角的最小化 / 最大化（还原）/ 关闭（窗框 `menu-button`：壳去掉了系统标题栏，
 * 见 `lib/desktop.ts` 的 `windowChrome`）。固定在窗口右上角、与顶栏同高，压在所有页面之上
 * （工作台 / 主页 / 教程 / 崩溃页 / 会话失败页都是它）——所以在 `main.tsx` 里单独挂一个根，
 * 不随 App 树或错误边界换掉。各页顶部一行右侧让出 `WINDOW_CONTROLS_INSET`。
 * 形制照 Windows 标题栏按钮：方角、46 宽、悬停浮一层，关闭悬停变红。别的窗框不渲染。
 */
export function WindowControls() {
  const { t } = useTranslation('common')
  const [maximized, setMaximized] = useState(false)
  const enabled = windowChrome() === 'menu-button'

  useEffect(() => {
    if (!enabled) return
    let live = true
    let off = () => {}
    void onWindowMaximizedChange((v) => {
      if (live) setMaximized(v)
    }).then((fn) => {
      if (live) off = fn
      else fn()
    })
    return () => {
      live = false
      off()
    }
  }, [enabled])

  if (!enabled) return null
  const button = (action: WindowControlAction, label: string, icon: ReactNode, danger = false) => (
    <button
      type="button"
      aria-label={label}
      data-window-control={action}
      onClick={() => void windowControl(action)}
      className={cn(
        'flex h-full w-[46px] items-center justify-center text-ink-2',
        danger
          ? 'hover:bg-danger hover:text-surface active:bg-danger/90'
          : 'hover:bg-surface-hover active:bg-surface-active',
      )}
    >
      {icon}
    </button>
  )
  return (
    <div data-window-controls className="fixed right-0 top-0 z-window-controls flex h-11">
      {button('minimize', t('actions.windowMinimize'), <Minus size={ICON_SIZE.md} />)}
      {button(
        'toggle-maximize',
        maximized ? t('actions.windowRestore') : t('actions.windowMaximize'),
        maximized ? <Copy size={ICON_SIZE.sm} /> : <Square size={ICON_SIZE.sm} />,
      )}
      {button('close', t('actions.close'), <X size={ICON_SIZE.md} />, true)}
    </div>
  )
}
