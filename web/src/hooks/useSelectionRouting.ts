import { useEffect, useRef, useState } from 'react'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

/**
 * 选择驱动的面板路由：
 * - 从左抽屉选择：互斥断点保留抽屉；宽窗仍显示属性。
 * - 从画布选择：打开属性；再次点击同一选区也能从树回到属性。
 * - 选择清空 → 未钉住的属性栏收起，不留「没有选中对象」的占位。
 */
export function useSelectionRouting() {
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  const inElement = useUiStore((s) => s.elementPanelId != null)
  const primaryId = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const primaryGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const active = hasSelection || inElement
  const inDrawer = useRef<boolean | null>(null)
  const [canvasPress, setCanvasPress] = useState(0)

  // pointerdown 的选择更新可能早于浏览器默认聚焦；触屏也未必聚焦。
  // capture 在树 / 画布的选择处理器之前记下来源，不能拿旧 activeElement 猜。
  // focusin / keydown 则覆盖键盘漫游与从输入框发起的操作。
  useEffect(() => {
    const remember = (e: Event) => {
      const target = e.target instanceof Element ? e.target : null
      inDrawer.current = !!target?.closest('[data-left-drawer]')
      if (e instanceof PointerEvent && e.button === 0 && target?.closest('[data-canvas-stage]') &&
        useUiStore.getState().tool === 'select' && !useViewportStore.getState().spaceDown) {
        // Space 平移 / 绘图起手不是换选；新对象真被选中后由下面的选择 effect 路由。
        setCanvasPress(n => n + 1)
      }
    }
    document.addEventListener('pointerdown', remember, true)
    document.addEventListener('focusin', remember, true)
    document.addEventListener('keydown', remember, true)
    return () => {
      document.removeEventListener('pointerdown', remember, true)
      document.removeEventListener('focusin', remember, true)
      document.removeEventListener('keydown', remember, true)
    }
  }, [])

  // 首次出现选择与换选使用同一判据；显式 setRightTab 不经过自动路由。
  useEffect(() => {
    const ui = useUiStore.getState()
    if (!active) {
      ui.autoHideProperties()
      return
    }
    const fromDrawer = inDrawer.current ?? !!document.activeElement?.closest('[data-left-drawer]')
    // 素材抽屉是挑一次即让位的入口；这条既有规则仍由 autoShowProperties 执行。
    if (fromDrawer && ui.leftOpen && ui.leftTab !== 'assets' && ui.layout !== 'wide') return
    ui.autoShowProperties()
  }, [active, primaryId, primaryGid, canvasPress])
}
