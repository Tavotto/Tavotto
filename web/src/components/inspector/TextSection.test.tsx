/**
 * 属性面板文字框的上下标快捷键（Mod+↑ / Mod+↓）。
 *
 * 背景：上/下标按钮的 tooltip 长期写着「快捷键 ⌘/Ctrl+↑」，但这个绑定从来
 * 没接过——textarea 的 onKeyDown 只有一句 stopPropagation，全局键盘钩子也
 * 收不到。现在键位真接上了，这批用例守住三件事：
 *   1. 键盘和按钮走**同一条路**（同一个 toggleScript + 同一套光标复位）；
 *   2. 键盘路径不绕开 onFocus 开的事务——绕开的话一次编辑会碎成好几条历史；
 *   3. 只认干净的 Mod+方向键，裸 ↑/↓ 与带 ⌥/⇧ 的组合原样交给浏览器。
 *
 * jsdom 说明：jsdom 不实现 textarea 的光标移动（裸 ↑ 不会真的换行），所以
 * 第 3 条断言的是「事件没被认领」（defaultPrevented=false + 文本不变），
 * 光标真移动那一半由浏览器自己保证。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type TextObject } from '@/types/document'
import { TextSection, scriptHotkey } from './TextSection'

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const textObj = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: 'H2O',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 10,
  y: 20,
  w: 20,
  h: 8,
  ...over,
})

/** 面板取的是 store 里的当前值，改完文本后 textarea 会跟着重渲染 */
function Harness() {
  const objects = useDocumentStore((s) => s.doc.objects)
  // 按钮的 tooltip 是 Radix 的，缺 Provider 直接抛——App 里也是全局包一层
  return (
    <TooltipProvider>
      <TextSection objs={objects.filter((o) => o.type === 'text') as TextObject[]} />
    </TooltipProvider>
  )
}

let container: HTMLDivElement
let root: Root

const s = () => useDocumentStore.getState()
const ta = () => container.querySelector('textarea')!
const currentText = () => (s().doc.objects[0] as TextObject).text

beforeEach(async () => {
  localStorage.clear()
  useSelectionStore.getState().clear()
  await s().switchDocument(emptyProject(), 'd_textsection')
  s().commit(literal('放入对象'), (d) => {
    d.objects.push(textObj())
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<Harness />))
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

/** 选中 [start, end)——按钮和快捷键都是按 textarea 的实时选区干活的 */
const select = (start: number, end: number) => {
  ta().setSelectionRange(start, end)
}

const press = (key: string, init: KeyboardEventInit = {}) => {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  act(() => {
    ta().dispatchEvent(ev)
  })
  return ev
}

/** 光标复位排在 rAF 里（等补丁重渲染完），断言前先放过一帧 */
const nextFrame = async () => {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  })
}

const clickButton = (label: string) => {
  const btn = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!
  act(() => btn.click())
}

describe('scriptHotkey', () => {
  const ev = (over: Partial<KeyboardEvent>): KeyboardEvent =>
    ({ key: 'ArrowUp', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over }) as KeyboardEvent

  it('⌘/Ctrl + ↑↓ 认领为上/下标', () => {
    expect(scriptHotkey(ev({ metaKey: true }))).toBe('sup')
    expect(scriptHotkey(ev({ ctrlKey: true }))).toBe('sup')
    expect(scriptHotkey(ev({ key: 'ArrowDown', metaKey: true }))).toBe('sub')
    expect(scriptHotkey(ev({ key: 'ArrowDown', ctrlKey: true }))).toBe('sub')
  })

  it('裸方向键、其它按键、以及带 ⌥/⇧ 的组合一律不认领', () => {
    expect(scriptHotkey(ev({}))).toBeNull()
    expect(scriptHotkey(ev({ key: 'ArrowDown' }))).toBeNull()
    expect(scriptHotkey(ev({ key: 'ArrowLeft', metaKey: true }))).toBeNull()
    expect(scriptHotkey(ev({ key: 'z', metaKey: true }))).toBeNull()
    // ⌥↑ 按段移动、⇧⌘↑ 选到开头，都是系统键位，抢了就选不了文本
    expect(scriptHotkey(ev({ metaKey: true, altKey: true }))).toBeNull()
    expect(scriptHotkey(ev({ metaKey: true, shiftKey: true }))).toBeNull()
    expect(scriptHotkey(ev({ ctrlKey: true, shiftKey: true }))).toBeNull()
  })
})

describe('Mod+↑ / Mod+↓ 在文字框里', () => {
  it('给选区加上上标标记，并把光标放回标记内部', async () => {
    select(1, 2) // 选中 H2O 的 "2"
    const ev = press('ArrowUp', { metaKey: true })

    expect(ev.defaultPrevented).toBe(true) // 不拦的话光标先跳行
    expect(currentText()).toBe('H^{2}O')
    await nextFrame()
    expect([ta().selectionStart, ta().selectionEnd]).toEqual([3, 4])
  })

  it('Mod+↓ 是下标', async () => {
    select(1, 2)
    press('ArrowDown', { ctrlKey: true })
    expect(currentText()).toBe('H_{2}O')
    await nextFrame()
    expect([ta().selectionStart, ta().selectionEnd]).toEqual([3, 4])
  })

  it('再按一次就是取消（与按钮同一套切换语义）', async () => {
    select(1, 2)
    press('ArrowUp', { metaKey: true })
    await nextFrame()
    press('ArrowUp', { metaKey: true })
    expect(currentText()).toBe('H2O')
  })

  it('没有选区时插入一对空标记，光标落在里面可以直接打字', async () => {
    select(3, 3) // 光标在末尾
    press('ArrowUp', { metaKey: true })
    expect(currentText()).toBe('H2O^{}')
    await nextFrame()
    expect([ta().selectionStart, ta().selectionEnd]).toEqual([5, 5])
  })

  it('结果与点按钮完全一致——键盘走的就是按钮那个处理函数', async () => {
    select(1, 2)
    press('ArrowUp', { metaKey: true })
    await nextFrame()
    const byKey = { text: currentText(), start: ta().selectionStart, end: ta().selectionEnd }

    // 退回原文再用按钮走一遍
    act(() => {
      s().commit(literal('复位'), (d) => {
        const o = d.objects[0]
        if (o.type === 'text') o.text = 'H2O'
      })
    })
    select(1, 2)
    clickButton('上标（cm⁻¹ 这类）')
    await nextFrame()

    expect({ text: currentText(), start: ta().selectionStart, end: ta().selectionEnd }).toEqual(byKey)
  })

  it('裸 ↑ 不受影响：事件不被认领、文本一个字不动', () => {
    select(1, 2)
    const up = press('ArrowUp')
    const down = press('ArrowDown')

    expect(up.defaultPrevented).toBe(false)
    expect(down.defaultPrevented).toBe(false)
    expect(currentText()).toBe('H2O')
  })

  it('⌥⌘↑ / ⇧⌘↑ 同样放行（系统的选择与移动键位）', () => {
    select(1, 2)
    expect(press('ArrowUp', { metaKey: true, altKey: true }).defaultPrevented).toBe(false)
    expect(press('ArrowUp', { metaKey: true, shiftKey: true }).defaultPrevented).toBe(false)
    expect(currentText()).toBe('H2O')
  })

  it('并入 onFocus 开的事务：连按两次只留一条历史，撤销一次回到原文', async () => {
    const before = s().past.length
    act(() => ta().focus())

    select(1, 2)
    press('ArrowUp', { metaKey: true })
    await nextFrame()
    select(0, 1)
    press('ArrowDown', { metaKey: true })
    await nextFrame()
    expect(currentText()).toBe('_{H}^{2}O')

    act(() => ta().blur())
    expect(s().past.length).toBe(before + 1)

    act(() => {
      s().undo()
    })
    expect(currentText()).toBe('H2O')
  })
})

describe('与图内文字一致的界面结构（ADR 0010）', () => {
  it('「字号」「颜色」「对齐」是可见标签，与图内文字同一组行组件', () => {
    const text = container.textContent ?? ''
    for (const label of ['字号', '颜色', '对齐']) expect(text, label).toContain(label)
    // 画布文字没有字体族能力（统一走文档字体）——不摆假「字体」控件
    expect(text).not.toContain('字体')
  })

  it('大小写 / 行距 / 背景住进「更多」，默认收起', () => {
    const text = container.textContent ?? ''
    expect(text).toContain('更多')
    expect(text).not.toContain('行距')
    expect(text).not.toContain('大小写')
    const more = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === '更多',
    )!
    act(() => more.click())
    const after = container.textContent ?? ''
    expect(after).toContain('行距')
    expect(after).toContain('背景')
  })
})
