import { useEffect, useRef, useState } from 'react'
import { isMac } from '@/lib/utils'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { isRightOverlay, useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

/**
 * 选择驱动的面板路由：
 * - 从左抽屉选择：互斥断点保留抽屉；宽窗仍显示属性。
 * - 从画布换选：打开属性；同一选区从树回到画布时也能打开属性。
 * - 用户收起属性后重复点击同一画布选区，保留已收起的状态。
 * - 选择清空 → 未钉住的属性栏收起，不留「没有选中对象」的占位。
 * - 素材抽屉（未固定时挑完即让位）：指针还在抽屉里时不收，连着加几张图不必反复点开；
 *   指针离开抽屉后再按原规则让位。
 * - 覆盖式属性栏（< 1280 没固定，`isRightOverlay`）：点画布空白处 / Esc 收回；只收此刻的开合，不写偏好。
 */
// 助手空态的选图器：用户正在助手页签里挑「要改哪张图」，这次选择不该把页签切走。
// 一次性意图 + 短时效（选择没变就不会走路由，意图不能留到之后的画布点选上）。
let assistantPickAt = 0
export function holdAssistantTabForNextSelection(): void {
  assistantPickAt = Date.now()
}
function consumeAssistantPick(): boolean {
  const held = Date.now() - assistantPickAt < 1000
  assistantPickAt = 0
  return held
}

export function useSelectionRouting() {
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  const inElement = useUiStore((s) => s.elementPanelId != null)
  const primaryId = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const primaryGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const active = hasSelection || inElement
  const inDrawer = useRef<boolean | null>(null)
  const treeHandoff = useRef(false)
  const canvasPointer = useRef<number | null>(null)
  const pendingRouting = useRef(false)
  const pointerInDrawer = useRef(false)
  const yieldPending = useRef(false)
  const [canvasPress, setCanvasPress] = useState(0)
  const [interactionEnd, setInteractionEnd] = useState(0)

  // 侧栏交换会改变画布原点；追踪指针期间只记请求，结束后再处理最新选区。
  // 只唤醒确实有路由请求的手势：Space 平移本身不会请求属性栏。
  useEffect(() => useInteractionStore.subscribe((state, prev) => {
    if (state.kind === 'none' && prev.kind !== 'none') {
      // Esc / 失去捕获可在 pointerup 前结束追踪；后续松手不能重新完成已取消的交接。
      canvasPointer.current = null
      if (pendingRouting.current) setInteractionEnd(n => n + 1)
    }
  }), [])

  // 原生菜单没有 DOM 事件；右栏收起取消交接，进入宽窗恢复仍有效的树交接。
  useEffect(() => useUiStore.subscribe((state, prev) => {
    if (prev.rightOpen && !state.rightOpen) {
      treeHandoff.current = false
      canvasPointer.current = null
      pendingRouting.current = false
    }
    if (prev.layout !== 'wide' && state.layout === 'wide' && treeHandoff.current) {
      setInteractionEnd(n => n + 1)
    }
  }), [])

  // 指针是否在左抽屉里；离开时若有被推迟的让位，补一次（选择还在才补）
  useEffect(() => {
    const settle = (inside: boolean) => {
      if (inside === pointerInDrawer.current) return
      pointerInDrawer.current = inside
      if (inside || !yieldPending.current) return
      yieldPending.current = false
      const hasSel = useSelectionStore.getState().ids.length > 0 || useUiStore.getState().elementPanelId != null
      if (hasSel) useUiStore.getState().autoShowProperties()
    }
    const over = (e: PointerEvent) =>
      settle(!!(e.target instanceof Element && e.target.closest('[data-left-drawer]')))
    const leaveWindow = () => settle(false)
    document.addEventListener('pointerover', over, true)
    document.documentElement.addEventListener('pointerleave', leaveWindow)
    return () => {
      document.removeEventListener('pointerover', over, true)
      document.documentElement.removeEventListener('pointerleave', leaveWindow)
    }
  }, [])

  // 覆盖式属性栏的收回：选择清空由下面的 autoHideProperties 管（只收属性页），这里补两个入口——
  // 助手 / 画布 / 时间线页签开着时没有选择变化可等；Esc 在没有选区可退时才收（有选区先退选区）
  useEffect(() => {
    const close = () => {
      const ui = useUiStore.getState()
      if (ui.layout !== 'narrow' && isRightOverlay(ui) && ui.rightOpen) useUiStore.setState({ rightOpen: false })
    }
    const noSelection = () => {
      const ui = useUiStore.getState()
      return useSelectionStore.getState().ids.length === 0 && ui.elementPanelId == null
    }
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || !(e.target instanceof Element)) return
      if (!e.target.closest('[data-canvas-stage]') || e.target.closest('[data-object-id]')) return
      // 点在空白处：选区随这次点击清掉（pointerup 之后才落地），所以等一拍再看
      setTimeout(() => noSelection() && close(), 0)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || !noSelection()) return
      const t = e.target instanceof Element ? e.target : null
      if (t?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      close()
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  // pointerdown 的选择更新可能早于浏览器默认聚焦；触屏也未必聚焦。
  // capture 在树 / 画布的选择处理器之前记下来源，不能拿旧 activeElement 猜。
  // focusin / keydown 则覆盖键盘漫游与从输入框发起的操作。
  useEffect(() => {
    const remember = (e: Event) => {
      const target = e.target instanceof Element ? e.target : null
      const ui = useUiStore.getState()
      inDrawer.current = !!target?.closest('[data-left-drawer]')
      if (inDrawer.current) treeHandoff.current = ui.leftTab !== 'assets'
      else if (target?.closest('[data-inspector-panel]') ||
        (target?.closest('[data-scrim]') && ui.rightOpen)) treeHandoff.current = false
      if (e instanceof PointerEvent && e.button === 0 && !(isMac && e.ctrlKey) &&
        target?.closest('[data-canvas-stage]') &&
        ui.tool === 'select' && !useViewportStore.getState().spaceDown) {
        // Space 平移 / 绘图起手不是换选；新对象真被选中后由下面的选择 effect 路由。
        // 同一选区只在从树交接时请求路由；普通换选已有下面的 effect，不能请求两次。
        // capture 的 effect 可能先于 target 起拖；按住时不能交换侧栏。
        if (treeHandoff.current) canvasPointer.current = e.pointerId
      }
    }
    const release = (e: PointerEvent) => {
      if (canvasPointer.current === null || canvasPointer.current !== e.pointerId) return
      canvasPointer.current = null
      if (e.type !== 'pointerup') return
      treeHandoff.current = false
      inDrawer.current = false
      setCanvasPress(n => n + 1)
    }
    document.addEventListener('pointerdown', remember, true)
    document.addEventListener('pointerup', release, true)
    document.addEventListener('pointercancel', release, true)
    document.addEventListener('focusin', remember, true)
    document.addEventListener('keydown', remember, true)
    return () => {
      document.removeEventListener('pointerdown', remember, true)
      document.removeEventListener('pointerup', release, true)
      document.removeEventListener('pointercancel', release, true)
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
      canvasPointer.current = null
      ui.autoHideProperties()
      return
    }
    const fromDrawer = inDrawer.current ?? !!document.activeElement?.closest('[data-left-drawer]')
    // 素材抽屉是挑一次即让位的入口；这条既有规则仍由 autoShowProperties 执行。
    if (fromDrawer && ui.leftOpen && ui.leftTab !== 'assets' && ui.layout !== 'wide') return
    if (pointerInDrawer.current && ui.leftOpen && ui.leftTab === 'assets') {
      yieldPending.current = true
      ui.autoShowProperties({ holdAssets: true })
      return
    }
    yieldPending.current = false
    if (consumeAssistantPick() && ui.rightOpen && ui.rightTab === 'assistant') return
    ui.autoShowProperties()
  }, [active, primaryId, primaryGid, canvasPress, interactionEnd])
}
