import { useEffect, useRef, useState } from 'react'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

/**
 * 选择驱动的面板路由：
 * - 从左抽屉选择：互斥断点保留抽屉；宽窗仍显示属性。
 * - 从画布换选：打开属性；同一选区从树回到画布时也能打开属性。
 * - 用户收起属性后重复点击同一画布选区，保留已收起的状态。
 * - 选择清空 → 未钉住的属性栏收起，不留「没有选中对象」的占位。
 */
export function useSelectionRouting() {
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  const inElement = useUiStore((s) => s.elementPanelId != null)
  const primaryId = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const primaryGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const active = hasSelection || inElement
  const inDrawer = useRef<boolean | null>(null)
  const treeHandoff = useRef(false)
  const pendingRouting = useRef(false)
  const [canvasPress, setCanvasPress] = useState(0)
  const [interactionEnd, setInteractionEnd] = useState(0)

  // 侧栏交换会改变画布原点；追踪指针期间只记请求，结束后再处理最新选区。
  // 只唤醒确实有路由请求的手势：Space 平移本身不会请求属性栏。
  useEffect(() => useInteractionStore.subscribe((state, prev) => {
    if (state.kind === 'none' && prev.kind !== 'none' && pendingRouting.current) {
      setInteractionEnd(n => n + 1)
    }
  }), [])

  // pointerdown 的选择更新可能早于浏览器默认聚焦；触屏也未必聚焦。
  // capture 在树 / 画布的选择处理器之前记下来源，不能拿旧 activeElement 猜。
  // focusin / keydown 则覆盖键盘漫游与从输入框发起的操作。
  useEffect(() => {
    const remember = (e: Event) => {
      const target = e.target instanceof Element ? e.target : null
      const ui = useUiStore.getState()
      inDrawer.current = !!target?.closest('[data-left-drawer]')
      if (inDrawer.current) treeHandoff.current = ui.leftTab !== 'assets'
      else if (target?.closest('[data-inspector-panel]')) treeHandoff.current = false
      if (e instanceof PointerEvent && e.button === 0 && target?.closest('[data-canvas-stage]') &&
        ui.tool === 'select' && !useViewportStore.getState().spaceDown) {
        // Space 平移 / 绘图起手不是换选；新对象真被选中后由下面的选择 effect 路由。
        // 同一选区只在从树交接时请求路由；普通换选已有下面的 effect，不能请求两次。
        const handoff = treeHandoff.current || (ui.leftOpen && ui.leftTab !== 'assets')
        treeHandoff.current = false
        if (handoff) setCanvasPress(n => n + 1)
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
    if (useInteractionStore.getState().kind !== 'none') {
      pendingRouting.current = true
      return
    }
    pendingRouting.current = false
    const ui = useUiStore.getState()
    if (!active) {
      treeHandoff.current = false
      ui.autoHideProperties()
      return
    }
    const fromDrawer = inDrawer.current ?? !!document.activeElement?.closest('[data-left-drawer]')
    // 素材抽屉是挑一次即让位的入口；这条既有规则仍由 autoShowProperties 执行。
    if (fromDrawer && ui.leftOpen && ui.leftTab !== 'assets' && ui.layout !== 'wide') return
    ui.autoShowProperties()
  }, [active, primaryId, primaryGid, canvasPress, interactionEnd])
}
