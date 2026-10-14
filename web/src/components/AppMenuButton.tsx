import { useTranslation } from 'react-i18next'
import { Menu } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { showDesktopAppMenu, windowChrome } from '@/lib/desktop'
import { cn } from '@/lib/utils'
import { IconButton } from './ui/Button'

/**
 * Windows 桌面版的应用菜单入口（窗框 `menu-button`，见 `lib/desktop.ts` 的 `windowChrome`）：
 * 系统菜单栏不再独占一行，「文件 / 编辑 / 显示 / 帮助」由这颗按钮在它正下方弹出——弹的就是
 * 壳里藏起来的那一份原生菜单，项与快捷键一个不差。别的窗框（macOS 的系统菜单栏、浏览器）不渲染。
 */
export function AppMenuButton({ className }: { className?: string }) {
  const { t } = useTranslation('common')
  if (windowChrome() !== 'menu-button') return null
  return (
    <IconButton
      label={t('actions.appMenu')}
      data-app-menu-button
      className={cn('shrink-0', className)}
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect()
        void showDesktopAppMenu(r.left, r.bottom)
      }}
    >
      <Menu size={ICON_SIZE.md} className="text-ink-2" />
    </IconButton>
  )
}
