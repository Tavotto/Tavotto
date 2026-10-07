import { useTranslation } from 'react-i18next'
import { ClipboardPaste, Fullscreen, SlidersHorizontal, SquareMousePointer } from '@/components/ui/icons'
import { MenuCheckItem, MenuItem, MenuSeparator, PointMenu } from '@/components/ui/Menu'
import { keyOf } from '@/lib/keymap'
import { canPasteFromMenu, pasteObjects } from '@/lib/clipboard'
import { isSelectAllTarget, selectAll } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { runDiscreteAction } from '@/store/gestureCoordinator'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

/**
 * 空白画布上的右键菜单（2026-10-07 设计审计 §10.1）：此前 `CanvasStage` 没有 `onContextMenu`，空白处右键
 * 弹出的是 WebView 自己的「重新加载 / 检查元素」。
 *
 * 只放「在这块画布上」能做的事，**每一项都调键盘 / 顶栏 / 命令面板已经在调的那个函数**，不新增能力：
 *   粘贴（`pasteObjects`，只在 `canPasteFromMenu` 的引擎上出现）· 全选（`selectAll`）· 适应画布（`fitAnimated`，与 ⌘1 同一个）·
 *   标尺 / 网格 / 安全区开关（与命令面板、画布设置同一份 uiStore 开关）· 画布设置（右栏「画布」页）。
 * 离散动作先过 `runDiscreteAction`（先落定开着的连续编辑，与菜单栏 / 剪贴板事件同一道闸）。
 * 外壳是右键菜单的同一份 `PointMenu`（非模态、Esc 不出菜单、焦点还给打开前的元素）。
 */
export function CanvasContextMenu({ at, close }: { at: { x: number; y: number }; close: () => void }) {
  const { t } = useTranslation('workspace')
  const showRulers = useUiStore((s) => s.showRulers)
  const showGrid = useUiStore((s) => s.showGrid)
  const showSafeArea = useUiStore((s) => s.showSafeArea)
  // 与 `selectAll` 同一判据：全是隐藏 / 锁定的对象时点了什么也选不上，不该亮着（Codex #833）
  const canSelectAll = useDocumentStore((s) => s.doc.objects.some(isSelectAllTarget))
  const ui = () => useUiStore.getState()
  const run = (fn: () => unknown) => () => {
    close()
    runDiscreteAction('app', null, fn)
  }
  return (
    <PointMenu
      open
      onOpenChange={(open) => {
        if (!open) close()
      }}
      at={at}
      ariaLabel={t('canvasMenu.aria')}
      data-canvas-menu=""
    >
      {/* 只在异步读剪贴板走得通的引擎上提供（`canPasteFromMenu`）：WebKit 上点了只会报「无法读取剪贴板」，
          那里的粘贴是 ⌘V 的原生事件 */}
      {canPasteFromMenu() && (
        <MenuItem
          icon={ClipboardPaste}
          shortcut={keyOf('paste')}
          data-canvas-menu-item="paste"
          onSelect={run(() => void pasteObjects())}
        >
          {t('canvasMenu.paste')}
        </MenuItem>
      )}
      <MenuItem
        icon={SquareMousePointer}
        shortcut={keyOf('selectAll')}
        disabled={!canSelectAll}
        data-canvas-menu-item="select-all"
        onSelect={run(selectAll)}
      >
        {t('canvasMenu.selectAll')}
      </MenuItem>
      <MenuSeparator />
      <MenuItem
        icon={Fullscreen}
        shortcut={keyOf('zoomFit')}
        data-canvas-menu-item="fit"
        onSelect={run(() => {
          const page = useDocumentStore.getState().doc.page
          useViewportStore.getState().fitAnimated(page.w, page.h)
        })}
      >
        {t('topbar.fitCanvas')}
      </MenuItem>
      {/* 开关：选了不关菜单（MenuCheckItem），连着开两三个不用反复右键 */}
      <MenuCheckItem checked={showRulers} data-canvas-menu-item="rulers" onSelect={() => ui().setShowRulers(!showRulers)}>
        {t('canvasMenu.rulers')}
      </MenuCheckItem>
      <MenuCheckItem checked={showGrid} data-canvas-menu-item="grid" onSelect={() => ui().setShowGrid(!showGrid)}>
        {t('canvasMenu.grid')}
      </MenuCheckItem>
      <MenuCheckItem
        checked={showSafeArea}
        data-canvas-menu-item="safe-area"
        onSelect={() => ui().setCanvasPref({ showSafeArea: !showSafeArea })}
      >
        {t('canvasMenu.safeArea')}
      </MenuCheckItem>
      <MenuSeparator />
      <MenuItem
        icon={SlidersHorizontal}
        data-canvas-menu-item="canvas-settings"
        onSelect={run(() => ui().setRightTab('canvas'))}
      >
        {t('topbar.canvasSettings')}
      </MenuItem>
    </PointMenu>
  )
}
