/**
 * 焦点交接（Codex #833 comment 4211499735）：命令面板这种「执行一条命令、自己随即关掉」的对话框，
 * 命令常常把焦点交给另一个表面（另一个对话框、命名小框）。那个表面打开时 `document.activeElement`
 * 还在**正在退场**的这层里（或这层已卸、落在 body），记下它没用。所以一层关上的那一刻登记「退场后
 * 该还给谁」，直到它的关闭归还真正跑完（`onCloseAutoFocus`）；这期间打开的表面经 `focusOrigin()`
 * 认领这个真正的打开者，关掉自己时才还得回去。
 */
let closingReturn: { owner: symbol; el: HTMLElement } | null = null

/**
 * 打开一个表面之前，焦点「从哪来」：通常就是 `document.activeElement`；若它在一个**正在关闭**的
 * 共用对话框里、或那层已卸掉落在了 body（命令面板执行完命令、正在退场），就是那层自己记下的打开者。
 * 不经 `ui/Dialog` 的就地表面（`NamedNodeQuickBox`、时间线抽屉）记打开者时也用它。
 */
export function focusOrigin(): HTMLElement | null {
  const active = document.activeElement
  const own = active instanceof HTMLElement && active !== document.body ? active : null
  if (own && own.closest('[data-dialog]')?.getAttribute('data-state') !== 'closed') return own
  if (closingReturn?.el.isConnected) return closingReturn.el
  return own
}

/** 一层共用对话框关上了：登记它退场后该还焦点给谁（`ui/Dialog` 调） */
export function holdFocusReturn(owner: symbol, el: HTMLElement): void {
  closingReturn = { owner, el }
}

/** 那层的关闭归还跑完了 / 又打开了 / 卸掉了：撤销它的登记（只撤自己那份） */
export function releaseFocusReturn(owner: symbol): void {
  if (closingReturn?.owner === owner) closingReturn = null
}
