import { ALT, MOD } from '@/lib/utils'

/**
 * 快捷键的**唯一出处**（2026-10-07 设计审计 §10.1 P2）：键位显示串与「这条绑定认哪些按键」都在这里。
 *
 * 此前同一条键位在五处手写（快捷键帮助、顶栏 Tip、菜单项右侧、命令面板、右键菜单），改一处漏四处。
 * 现在：
 *   - 显示：`keyOf(id)` —— `Tip shortcut` / `MenuItem shortcut` / 命令面板 / 快捷键帮助都从这里取；
 *   - 判定：`match` 是这条绑定在 `hooks/useKeyboard` 里认的按键。`lib/keymap.test.ts` 把一整张按键表
 *     逐个派发给挂着的 `useKeyboard`，**凡是被它消费（preventDefault）的按键都必须落在某条 `match` 上**——
 *     useKeyboard 加了一条新绑定而这里没登记，用例当场红（判的是行为，不是源码里的字串）。
 *
 * 不在 keydown 层的绑定（原生剪贴板事件、文字编辑框里的组合、指针手势）只登记显示串，`native: true`。
 */

export interface KeyMatch {
  /** `KeyboardEvent.key`，小写比较 */
  key?: string
  /** `KeyboardEvent.code`（⌥ / ⇧ 会改写 key 的那几条按 code 认） */
  code?: string
  /** ⌘ / Ctrl；不写 = 必须没按 */
  mod?: boolean
  /** 不写 = 不论 */
  shift?: boolean
  /** 不写 = 不论 */
  alt?: boolean
}

export interface KeyBinding {
  /** 显示串（键帽按字拆，见 `ui/Kbd`） */
  keys: string
  /** useKeyboard 认的按键；空 = 不经 keydown（原生事件 / 别处的控件自己处理） */
  match: KeyMatch[]
  /** 原生剪贴板事件、文字框内组合等：keydown 层不消费它 */
  native?: true
}

const k = (keys: string, ...match: KeyMatch[]): KeyBinding => ({ keys, match })
const native = (keys: string): KeyBinding => ({ keys, match: [], native: true })

/**
 * 全部绑定。id 是稳定标识（命令面板 / 菜单 / 帮助都按它取），不随文案变。
 * 加一条：先在这里登记，再在 useKeyboard 里接；快捷键帮助要列它就在 `HELP_ROWS` 里引用。
 */
export const KEYMAP = {
  // 文件
  save: k(`${MOD}S`, { key: 's', mod: true, shift: false, alt: false }),
  saveAs: k(`⇧${MOD}S`, { key: 's', mod: true, shift: true, alt: false }),
  timeline: k(`⇧${MOD}H`, { code: 'KeyH', mod: true, shift: true, alt: false }),
  saveNamed: k(`${ALT}${MOD}S`, { code: 'KeyS', mod: true, shift: false, alt: true }),
  export: k(`${MOD}E`, { key: 'e', mod: true }),
  palette: k(`${MOD}K`, { key: 'k', mod: true }),
  help: k('?', { key: '?' }),
  // 选择
  selectAll: k(`${MOD}A`, { key: 'a', mod: true }),
  enter: k('Enter', { key: 'enter' }),
  escape: k('Esc', { key: 'escape' }),
  // 编辑
  undo: k(`${MOD}Z`, { key: 'z', mod: true, shift: false }),
  redo: k(`⇧${MOD}Z`, { key: 'z', mod: true, shift: true }, { key: 'y', mod: true }),
  copy: native(`${MOD}C`),
  paste: native(`${MOD}V`),
  duplicate: k(`${MOD}D`, { key: 'd', mod: true }),
  delete: k('Delete', { key: 'delete' }, { key: 'backspace' }),
  nudge: k(
    '↑ ↓ ← →',
    { key: 'arrowup' },
    { key: 'arrowdown' },
    { key: 'arrowleft' },
    { key: 'arrowright' },
  ),
  // 排列
  zTop: k(`⇧${MOD}]`, { key: ']', mod: true, shift: true }),
  zUp: k(`${MOD}]`, { key: ']', mod: true, shift: false }),
  zDown: k(`${MOD}[`, { key: '[', mod: true, shift: false }),
  zBottom: k(`⇧${MOD}[`, { key: '[', mod: true, shift: true }),
  // 视图
  zoomIn: k(`${MOD}+`, { key: '=', mod: true }, { key: '+', mod: true }),
  zoomOut: k(`${MOD}−`, { key: '-', mod: true }),
  zoomActual: k(`${MOD}0`, { key: '0', mod: true }),
  zoomFit: k(`${MOD}1`, { key: '1', mod: true }),
  // ⇧2 = 缩放到选区（Figma / Sketch 的同一个键）。按 code 认：⇧ 把 key 改成 @ / " 因布局而异
  zoomSelection: k('⇧2', { code: 'Digit2', shift: true, alt: false }),
  // 空格在 keydown 最前面就被认领（带不带 ⌘ 都按住平移），所以两种都登记
  pan: k('Space', { code: 'Space' }, { code: 'Space', mod: true }),
  // 工具
  toolSelect: k('V', { key: 'v' }),
  toolText: k('T', { key: 't' }),
  toolArrow: k('A', { key: 'a' }),
  toolRect: k('R', { key: 'r' }),
  toolEllipse: k('O', { key: 'o' }),
  toolLine: k('L', { key: 'l' }),
  // 画布标签（焦点在标签上时；`CanvasTabs` 自己处理，不经全局 keydown）
  tabRename: native('F2'),
  tabClose: native(`${MOD}W`),
  tabReorder: native(`${ALT}← / ${ALT}→`),
} satisfies Record<string, KeyBinding>

export type KeyId = keyof typeof KEYMAP

/** 一条绑定的显示串（Tip / MenuItem / 命令面板 / 帮助都用它） */
export function keyOf(id: KeyId): string {
  return KEYMAP[id].keys
}

/** 几条绑定合成一格：「⌘Z / ⇧⌘Z」 */
export function keysOf(...ids: KeyId[]): string {
  return ids.map(keyOf).join(' / ')
}

/** 这个按键事件落在哪条绑定上（给 keymap 用例与诊断用；useKeyboard 自己的分支不经它） */
export function bindingFor(e: {
  key: string
  code: string
  metaKey?: boolean
  ctrlKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
}): KeyId | null {
  const mod = !!(e.metaKey || e.ctrlKey)
  for (const [id, b] of Object.entries(KEYMAP) as [KeyId, KeyBinding][]) {
    for (const m of b.match) {
      if (m.key !== undefined && e.key.toLowerCase() !== m.key) continue
      if (m.code !== undefined && e.code !== m.code) continue
      if ((m.mod ?? false) !== mod) continue
      if (m.shift !== undefined && m.shift !== !!e.shiftKey) continue
      if (m.alt !== undefined && m.alt !== !!e.altKey) continue
      return id
    }
  }
  return null
}
