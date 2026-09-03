import { useUiStore } from '@/store/uiStore'

/**
 * 多选栏的「更多」：打开属性页并把排列组滚进视野。
 *
 * 选择一个字不动；窄屏下 `setRightTab` 本来就会把抽屉铺开（浮动栏随之让位）。
 * 属性页没有精确的 section 路由，这里只认 `ArrangeSection` 挂的
 * `data-arrange-section` 锚点——右栏可能这一帧才打开，滚动放到下一帧。
 */
export function openArrangeInInspector() {
  useUiStore.getState().setRightTab('properties')
  const scroll = () => {
    const el = document.querySelector<HTMLElement>('[data-arrange-section]')
    el?.scrollIntoView?.({ block: 'nearest' })
  }
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(scroll)
  else scroll()
}
