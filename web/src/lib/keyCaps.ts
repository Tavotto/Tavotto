import { ALT, MOD } from '@/lib/utils'

/**
 * 键位文本 → 一个个键帽。命令面板与快捷键速查表共用这一份拆法，
 * 键位本身（`⌘E` / `⇧⌘S` / `Ctrl+滚轮` / `⌘+ / ⌘−`）仍是那一串文本，数据源不动。
 *
 * 规则：` / ` 隔开的是「或」（各自一组键帽）；组内先认修饰键与具名键（⇧⌘⌥⌃⏎ / Ctrl Alt
 * Shift Esc Enter Delete Space），其余连续字符是一个键（`S`、`]`、`↑`、`滚轮`）；
 * `+` 后面还有字符时是连接符，在末尾时它自己就是那个键（`⌘+` = 放大）。
 */
// ⌘ / ⌥ 不在源码里手写（`modKey.test`）：这一平台的修饰键就是 MOD / ALT，另一平台的字样不会出现
const NAMED = [MOD, ALT, 'Ctrl', 'Alt', 'Shift', 'Esc', 'Enter', 'Delete', 'Space', '⇧', '⌃', '⏎']

function capsOf(combo: string): string[] {
  const caps: string[] = []
  let i = 0
  while (i < combo.length) {
    const named = NAMED.find((n) => combo.startsWith(n, i))
    if (named) {
      caps.push(named)
      i += named.length
    } else if (combo[i] === '+' && i + 1 < combo.length && caps.length > 0) {
      i += 1 // 连接符
    } else if (combo[i] === '+') {
      caps.push('+')
      i += 1
    } else {
      let j = i + 1
      // 一个键：连续字符直到下一个具名键或连接符（`⌘]` 的 `]`、`滚轮`、`拖动`）
      while (
        j < combo.length &&
        combo[j] !== '+' &&
        !NAMED.some((n) => combo.startsWith(n, j))
      )
        j += 1
      caps.push(combo.slice(i, j))
      i = j
    }
  }
  return caps
}

/** 外层是「或」的几组，内层是每组的键帽 */
export function keyCaps(text: string): string[][] {
  return text
    .split(' / ')
    .map((c) => c.trim())
    .filter(Boolean)
    .map(capsOf)
}
