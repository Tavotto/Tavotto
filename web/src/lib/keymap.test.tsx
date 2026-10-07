/**
 * 快捷键单一来源（`lib/keymap`，2026-10-07 设计审计 §10.1 P2）的两条判据。
 *
 * 主语：**挂着的 `useKeyboard` 在 window 上消费了哪些按键**（`defaultPrevented`）——不是它源码里写了
 * 哪些字串。一整张按键表（字母 / 数字 / 标点 / 功能键 × 六种修饰组合）逐个派发：
 *   1. 被消费的每一个都必须落在 `KEYMAP` 的某条 `match` 上（useKeyboard 加了绑定却没登记 → 红）；
 *   2. 反过来，与状态无关的每条登记（不是 native、不需要先有选区的）按美式布局合成出**真浏览器会给的**
 *      事件（⇧ 改写后的 key + 物理 code，⇧⌘] 报 `}`）确实被消费（登记了一条 useKeyboard 根本不认、
 *      或真浏览器里按不出来的键 → 红）。
 * 盲点写在明处：只在有选区 / 手势进行中才消费的键（Enter、方向键、手势中的 Esc）第 1 条照样覆盖
 * （它们在这里不被消费，不会误报），第 2 条跳过它们。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { KEYMAP, bindingFor, keyOf, keysOf, type KeyId, type KeyMatch } from './keymap'
import { useKeyboard } from '@/hooks/useKeyboard'
import { changeZOrder } from '@/store/actions'

vi.mock('@/store/actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/actions')>()),
  runManualSave: vi.fn().mockResolvedValue(undefined),
  startNamedNode: vi.fn(),
  toggleTimeline: vi.fn(),
  changeZOrder: vi.fn(),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Host() {
  useKeyboard()
  return null
}

let root: Root
let host: HTMLDivElement

beforeAll(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterAll(async () => {
  await act(async () => root.unmount())
  host.remove()
})

interface Probe {
  key: string
  code: string
  metaKey?: boolean
  ctrlKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
}

/** 一个按键在给定修饰下的 key / code（美式布局；⇧ 改写数字与标点） */
const SHIFTED: Record<string, string> = {
  '1': '!', '2': '@', '3': '#', '4': '$', '5': '%', '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
  '-': '_', '=': '+', '[': '{', ']': '}', '/': '?', ';': ':', "'": '"', ',': '<', '.': '>', '`': '~', '\\': '|',
}
const PUNCT_CODE: Record<string, string> = {
  '-': 'Minus', '=': 'Equal', '[': 'BracketLeft', ']': 'BracketRight', '/': 'Slash', ';': 'Semicolon',
  "'": 'Quote', ',': 'Comma', '.': 'Period', '`': 'Backquote', '\\': 'Backslash',
}
const SPECIAL: [string, string][] = [
  ['Escape', 'Escape'], ['Enter', 'Enter'], ['Delete', 'Delete'], ['Backspace', 'Backspace'],
  [' ', 'Space'], ['Tab', 'Tab'], ['ArrowUp', 'ArrowUp'], ['ArrowDown', 'ArrowDown'],
  ['ArrowLeft', 'ArrowLeft'], ['ArrowRight', 'ArrowRight'], ['F2', 'F2'], ['Home', 'Home'], ['End', 'End'],
]

function* probes(): Generator<Probe> {
  const base: { key: string; code: string; shifted?: string }[] = []
  for (const c of 'abcdefghijklmnopqrstuvwxyz') base.push({ key: c, code: `Key${c.toUpperCase()}`, shifted: c.toUpperCase() })
  for (const d of '0123456789') base.push({ key: d, code: `Digit${d}`, shifted: SHIFTED[d] })
  for (const p of Object.keys(PUNCT_CODE)) base.push({ key: p, code: PUNCT_CODE[p], shifted: SHIFTED[p] })
  for (const [key, code] of SPECIAL) base.push({ key, code })
  const mods = [
    {}, { shiftKey: true }, { ctrlKey: true }, { ctrlKey: true, shiftKey: true },
    { altKey: true }, { ctrlKey: true, altKey: true }, { metaKey: true },
  ]
  for (const b of base)
    for (const m of mods) yield { key: m.shiftKey && b.shifted ? b.shifted : b.key, code: b.code, ...m }
}

/** ⇧ 改写出来的字 → 未改写的那颗键（`}` → `]`） */
const UNSHIFTED = Object.fromEntries(Object.entries(SHIFTED).map(([b, s]) => [s, b]))

/** 一颗未改写的字符键在美式布局上的 `code` */
function codeOfBase(c: string): string | undefined {
  if (/^[a-z]$/.test(c)) return `Key${c.toUpperCase()}`
  if (/^[0-9]$/.test(c)) return `Digit${c}`
  if (c === ' ') return 'Space'
  return PUNCT_CODE[c]
}

/**
 * 一条 `KeyMatch` 在美式布局的真浏览器里按出来的 keydown：`key` 是 ⇧ 改写**之后**的字
 * （⇧⌘] 报 `}`，不是 `]`），`code` 是物理键位。按不出来（要求 `}` 却又要求不按 ⇧）返回 null。
 * 主语是「用户真按下去时浏览器给的事件」，不是把 KEYMAP 里的字串原样塞回去——那样 ⇧⌘] 只认
 * `]` 的绑定在这里照样绿，真浏览器里却是死键。
 */
function usLayoutEvent(m: KeyMatch): Probe | null {
  const mods = { ctrlKey: !!m.mod, altKey: !!m.alt }
  let base: string
  let shift: boolean
  if (m.code) {
    // 按 code 认的：从物理键位反推未改写的字
    base = m.code.startsWith('Key')
      ? m.code.slice(3).toLowerCase()
      : m.code.startsWith('Digit')
        ? m.code.slice(5)
        : m.code === 'Space'
          ? ' '
          : (Object.entries(PUNCT_CODE).find(([, c]) => c === m.code)?.[0] ?? '')
    shift = !!m.shift
  } else if (m.key && m.key.length > 1) {
    // 具名键（escape → Escape）：⇧ 不改写它
    const named = m.key[0].toUpperCase() + m.key.slice(1)
    return { key: named, code: named, shiftKey: !!m.shift, ...mods }
  } else if (m.key && UNSHIFTED[m.key]) {
    // `}` / `?` / `+` 这类字只有按着 ⇧ 才按得出来
    if (m.shift === false) return null
    base = UNSHIFTED[m.key]
    shift = true
  } else {
    base = m.key ?? ''
    shift = !!m.shift
  }
  const code = codeOfBase(base)
  if (code === undefined) return null
  const key = !shift ? base : /^[a-z]$/.test(base) ? base.toUpperCase() : (SHIFTED[base] ?? base)
  return { key, code, shiftKey: shift, ...mods }
}

const press = (p: Probe) => {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...p })
  act(() => {
    window.dispatchEvent(ev)
  })
  // 放开空格：它会把视口留在「按住平移」态
  if (p.code === 'Space') {
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space', key: ' ' }))
    })
  }
  return ev
}

describe('keymap ↔ useKeyboard', () => {
  it('useKeyboard 消费的每一个按键都在 KEYMAP 里登记过', () => {
    const unregistered: string[] = []
    let consumed = 0
    for (const p of probes()) {
      if (!press(p).defaultPrevented) continue
      consumed++
      if (!bindingFor(p)) unregistered.push(JSON.stringify(p))
    }
    // 防空转：真的有一批按键被消费了
    expect(consumed).toBeGreaterThan(30)
    expect(unregistered).toEqual([])
  })

  it('每条与状态无关的登记，合成出来的按键确实被 useKeyboard 消费', () => {
    // 需要先有选区（Enter / 方向键）的不在这里判，见文件头
    const stateful = new Set<KeyId>(['enter', 'nudge'])
    const dead: string[] = []
    for (const [id, b] of Object.entries(KEYMAP) as [KeyId, (typeof KEYMAP)[KeyId]][]) {
      if ('native' in b || stateful.has(id)) continue
      for (const m of b.match) {
        const p = usLayoutEvent(m)
        if (!p) {
          dead.push(`${id} ${JSON.stringify(m)}（美式布局按不出这个组合）`)
          continue
        }
        if (!press(p).defaultPrevented) dead.push(`${id} ${JSON.stringify(p)}`)
      }
    }
    expect(dead).toEqual([])
  })

  it('⌘] 族按真浏览器的事件（美式布局 ⇧] = `}`）分出上移 / 置顶 / 下移 / 置底', () => {
    vi.mocked(changeZOrder).mockClear()
    for (const [key, code, shiftKey] of [
      ['}', 'BracketRight', true],
      [']', 'BracketRight', false],
      ['[', 'BracketLeft', false],
      ['{', 'BracketLeft', true],
    ] as const) {
      expect(press({ key, code, shiftKey, ctrlKey: true }).defaultPrevented).toBe(true)
    }
    expect(vi.mocked(changeZOrder).mock.calls.map((c) => c[0])).toEqual(['top', 'up', 'down', 'bottom'])
  })
})

describe('显示串', () => {
  it('keysOf 用斜杠连起几条绑定；单条与 keyOf 相同', () => {
    expect(keysOf('undo')).toBe(keyOf('undo'))
    expect(keysOf('undo', 'redo')).toBe(`${keyOf('undo')} / ${keyOf('redo')}`)
  })

  it('bindingFor：⇧2 认 code 不认 key（布局无关）', () => {
    expect(bindingFor({ key: '@', code: 'Digit2', shiftKey: true, metaKey: false, ctrlKey: false, altKey: false })).toBe('zoomSelection')
    expect(bindingFor({ key: '"', code: 'Digit2', shiftKey: true, metaKey: false, ctrlKey: false, altKey: false })).toBe('zoomSelection')
    expect(bindingFor({ key: '2', code: 'Digit2', shiftKey: false, metaKey: false, ctrlKey: false, altKey: false })).toBeNull()
  })
})
