/**
 * 焦点救援：一次状态切换把当前焦点元素从 DOM 里摘掉之后，把焦点交给一个还活着
 * 的接手者。
 *
 * **为什么必须做，而不是「浏览器会自己处理」**：Chromium 在焦点掉回 body 之后，
 * Tab 会从文档开头重新开始，用户几乎感觉不到；**WebKit 不会**——它的顺序聚焦
 * 起点跟着那个已经消失的元素一起没了，于是 Tab 与 Shift+Tab **双向都不动**，
 * 键盘用户就此困在页面里，只能用鼠标点一下才能继续。macOS 桌面壳用的正是
 * WKWebView，Safari 用户同理。
 *
 * 实测（Playwright WebKit，`e2e/keyboard-golden-path.spec.ts` 的现场，#138）：
 *
 *     [probe] 起点               BODY
 *     [probe] press Tab       -> BODY
 *     [probe] press Alt+Tab   -> BODY
 *     [probe] 直接 focus(input) -> INPUT
 *     [probe] 之后 Tab        -> DIV|调整属性栏宽度…      ← 立刻恢复正常
 *
 * 也就是说：页面上有没有可聚焦元素**不是**分界线（那一刻有 33 个、含两个
 * input），分界线是「焦点还在不在文档里」。
 */

/** 这个元素还在文档里、并且看得见吗？（焦点接手者必须两条都满足） */
function alive(el: Element | null | undefined): el is HTMLElement {
  return (
    !!el && el instanceof HTMLElement && el.isConnected && el.getClientRects().length > 0
  )
}

/**
 * 盯住这次切换：**如果**它把当前焦点元素摘掉了、且没有别人接住焦点，就把焦点
 * 交给 `pick()` 选出的接手者。
 *
 * 为什么要盯而不是「下一帧看一眼」：卸载可能比一两帧晚——画布上那条浮动工具条
 * 是跟着选择状态走的，进图内编辑之后要再过一轮才收起。第一版就是在两个 rAF
 * 之后检查，那时按钮还在，于是什么都没做，等它真的消失时已经没人管了。
 *
 * 三种情况都**不**接管，避免抢走用户自己的焦点：
 *   * 焦点已经落在另一个活着的元素上——别人已经安排好了；
 *   * `within` 毫秒内焦点元素一直好好的——这次切换根本没摘掉它；
 *   * `pick()` 选不出活着的接手者——宁可什么都不做，也不把焦点扔到看不见的
 *     元素上（那比丢焦点更难排查）。
 */
export function rescueFocus(
  pick: () => HTMLElement | null | undefined,
  { within = 2_000 }: { within?: number } = {},
): void {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return

  let done = false
  const stop = () => {
    if (done) return
    done = true
    observer.disconnect()
    clearTimeout(timer)
    document.removeEventListener('focusin', onFocusIn, true)
  }

  /** 焦点掉了没？掉了就接管。返回「这件事已经了结」。 */
  const settle = (): boolean => {
    const now = document.activeElement
    if (alive(now) && now !== document.body) return false // 焦点还在，继续等
    const next = pick()
    if (!alive(next)) return false // 接手者还没出现（或本来就没有）
    next.focus()
    return true
  }

  // 别人主动拿走焦点：撤，不跟用户抢
  const onFocusIn = () => {
    if (document.activeElement !== document.body) stop()
  }

  const observer = new MutationObserver(() => {
    if (settle()) stop()
  })
  const timer = setTimeout(stop, within)

  document.addEventListener('focusin', onFocusIn, true)
  observer.observe(document.body, { childList: true, subtree: true })
  // 也可能这一刻就已经掉了（同步卸载）
  if (settle()) stop()
}
