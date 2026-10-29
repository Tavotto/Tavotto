/**
 * 属性面板的标注文字框：所见即所得（ADR 0152）。
 *
 * 守住四件事：
 *   1. 框里直接显示样式（老标注 `cm^{-1}` 显示成上标），打开不改写字符串；
 *   2. 角上的钮与快捷键作用在**选区**上，写回的是 LaTeX 子集并标 `textFormat`；
 *   3. 编辑并入 onFocus 开的事务：一次编辑在文档历史里只是一条；
 *   4. 外部改了字符串（撤销）框跟着换。
 *
 * 选区经 `richViewOf` 直接设在编辑器上：jsdom 不实现 contenteditable 的选区同步。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { TextSelection } from 'prosemirror-state'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { richViewOf } from '@/components/ui/RichTextField'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type TextObject } from '@/types/document'
import { propertyPathOf } from '@/lib/typography'
import { CANVAS_TEXT_PRIMARY_PROPS } from './typographyAdapter'
import { TextSection } from './TextSection'

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

/** 面板取的是 store 里的当前值 */
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
const current = () => s().doc.objects[0] as TextObject
const editorDom = () => container.querySelector('[data-rich-text="field"]')!
const view = () => richViewOf(editorDom())!

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

/** 选中 [from, to)（文档顶层就是行内内容：位置 = 字符偏移） */
const select = (from: number, to: number) =>
  act(() => {
    const v = view()
    v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, from, to)))
  })

const click = (action: string) =>
  act(() => container.querySelector<HTMLButtonElement>(`[data-rich-action="${action}"]`)!.click())

const press = (key: string, init: KeyboardEventInit = {}) =>
  act(() => {
    editorDom().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }))
  })

describe('所见即所得的标注文字框', () => {
  it('老标注照样式显示，打开不改写字符串', () => {
    act(() => s().commit(literal('换文字'), (d) => void ((d.objects[0] as TextObject).text = 'cm^{-1}')))
    expect(editorDom().textContent).toBe('cm-1')
    expect(editorDom().querySelector('[data-rt="sup"]')?.textContent).toBe('-1')
    expect(current().text).toBe('cm^{-1}')
    expect(current().textFormat).toBeUndefined()
  })

  it('选中一段点斜体：只有那段斜体，写回 LaTeX 子集并标 textFormat', () => {
    select(0, 1)
    click('italic')
    expect(current().text).toBe('\\textit{H}2O')
    expect(current().textFormat).toBe('latex')
    expect(editorDom().querySelector('em')?.textContent).toBe('H')
    expect(container.querySelector('[data-rich-action="italic"]')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('⌘I / ⌘B / ⌘↑ / ⌘↓ 与按钮同一条路', () => {
    select(1, 2)
    // jsdom 的 platform 不是 Mac：ProseMirror 的 Mod 解析成 Ctrl
    press('ArrowDown', { ctrlKey: true })
    expect(current().text).toBe('H\\textsubscript{2}O')
    select(0, 1)
    press('b', { ctrlKey: true })
    expect(current().text).toBe('\\textbf{H}\\textsubscript{2}O')
  })

  it('再点一次就是取消', () => {
    select(0, 1)
    click('bold')
    click('bold')
    expect(current().text).toBe('H2O')
  })

  it('并入 onFocus 开的事务：连改两次只留一条历史，撤销一次回到原文', () => {
    const before = s().past.length
    act(() => (editorDom() as HTMLElement).focus())
    select(0, 1)
    click('italic')
    select(2, 3)
    click('sup')
    act(() => (editorDom() as HTMLElement).blur())
    expect(s().past.length).toBe(before + 1)
    act(() => s().undo())
    expect(current().text).toBe('H2O')
    // 外部改回（撤销）后框跟着换
    expect(editorDom().querySelector('em')).toBeNull()
  })

  it('换行钮插入换行', () => {
    select(1, 1)
    click('break')
    expect(current().text).toBe('H\n2O')
  })
})

describe('与图内文字一致的界面结构（ADR 0010）', () => {
  it('「字体」「字号」「颜色」「对齐」是可见标签，与图内文字同一份控件', () => {
    const text = container.textContent ?? ''
    // 「字体」是 Prompt 13 补上的那一条：标注文字以前根本设不了字体
    for (const label of ['字体', '字号', '颜色', '对齐']) expect(text, label).toContain(label)
  })

  it('每一条排版属性都挂着定位锚点，锚点名与 property path 逐字一致', () => {
    for (const prop of CANVAS_TEXT_PRIMARY_PROPS) {
      const path = propertyPathOf('canvasText', prop)
      expect(path, prop).toBeTruthy()
      // 问题面板「定位到字段」查的就是这个选择器（lib/issueFocus.ts）；
      // 少一个锚点的表现是「点了定位什么都没发生」，而界面并不报错
      expect(container.querySelector(`[data-prop="${path}"]`), prop).not.toBeNull()
    }
  })

  it('没设过字体 = 继承默认族，不显示「已修改」，也不给恢复按钮', () => {
    const text = container.textContent ?? ''
    expect(text).toContain('衬线')
    expect(text).not.toContain('已修改')
  })

  it('大小写 / 行距 / 背景住进「更多」，默认收起', () => {
    const text = container.textContent ?? ''
    expect(text).toContain('更多')
    expect(text).not.toContain('行距')
    expect(text).not.toContain('大小写')
    const more = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim().split(', ')[0] === '更多',
    )!
    act(() => more.click())
    const after = container.textContent ?? ''
    expect(after).toContain('行距')
    expect(after).toContain('背景')
  })
})

/**
 * 科学文本那一行与字形提示：**两样都只在确有其事时才出现**。
 *
 * 默认界面只显示完成任务所需的信息（`00_SHARED_RULES` §7）——文字里没有
 * Unicode 上下标时，那个选择对用户不产生任何差别。
 */
describe('科学文本与字形提示', () => {
  const setText = (text: string, over: Partial<TextObject> = {}) => {
    act(() => {
      s().commit(literal('换文字'), (d) => {
        Object.assign(d.objects[0] as TextObject, { text, ...over })
      })
    })
  }
  const row = () => container.querySelector(`[data-prop="${propertyPathOf('canvasText', 'interpretation')}"]`)

  it('普通文字：那一行根本不出现', () => {
    setText('Sample A')
    expect(row()).toBeNull()
  })

  it('有 Unicode 上标时才出现', () => {
    setText('×10⁵')
    expect(row()).not.toBeNull()
  })

  it('锚点用的是 propertyPathOf()，问题面板定位得到它', () => {
    setText('H₂O')
    // 三处（检查报的字段名 / 控件锚点 / 定位选择器）读同一份表——各写各的
    // 字符串时，缺的那一处表现为「点了定位什么都没发生」，界面并不报错。
    expect(row()?.getAttribute('data-prop')).toBe('interpretation')
  })

  it('画不出来的字符逐字列出来（那是导出后的方框）', () => {
    setText('T؟ = 5')
    expect(container.textContent).toContain('؟')
  })

  it('主脸自己画得出的字符：既不说方框、也不说换脸', () => {
    // U10（ADR 0072）起 `⁵` 是 Liberation 自带的 primary；批准字体集合没有隐式回退层
    // （`fallback` 恒空，ADR 0060 §1），所以「另一种字体」那一句在活表上不会出现——
    // 那个分支保留给将来 allowlist 加脸后的 fallback 层，它的判据在 glyphPlan 里
    setText('×10⁵')
    expect(container.textContent?.includes('导出后是方框')).toBe(false)
    expect(container.textContent?.includes('另一种字体')).toBe(false)
  })
})

describe('换了写法、字符串不变（Codex #1489）', () => {
  it('同一串字从旧标记换成 LaTeX 子集：框按新写法重读', () => {
    act(() => s().commit(literal('换文字'), (d) => void ((d.objects[0] as TextObject).text = '\\textit{x}')))
    expect(editorDom().textContent).toBe('\\textit{x}')
    act(() => s().commit(literal('换写法'), (d) => void ((d.objects[0] as TextObject).textFormat = 'latex')))
    expect(editorDom().textContent).toBe('x')
    expect(editorDom().querySelector('em')?.textContent).toBe('x')
  })
})
