/**
 * 文字标注的画布渲染与就地编辑。
 *
 * 就地编辑是所见即所得文字框（ADR 0152）：编辑器从现有正文（带样式）起步，失焦时整段写成一条历史；
 * 没改就不写。历史背景（2026-08-25「（a）」显示两遍）：plaintext contentEditable 时代退出编辑后会残留
 * 野节点——div 的 key 随编辑态变的修法保留，这里的用例改为断言编辑器本身的行为。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type TextObject } from '@/types/document'
import { CANVAS_TEXT_FAMILIES, canvasFontStack } from '@/lib/typography'
import { TextView } from './TextView'
import { richViewOf } from '@/components/ui/RichTextField'

const textObj = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: '',
  sizePt: 10,
  bold: false,
  color: '#111111',
  align: 'left',
  x: 0,
  y: 0,
  w: 40,
  h: 6,
  ...over,
})

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  // jsdom 没有 ResizeObserver；TextView 用它做高度回写，这里只需要不炸
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  localStorage.clear()
  useUiStore.setState({ editingTextId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_textview')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

describe('字体族落到画布上', () => {
  const renderOne = (over: Partial<TextObject>) => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj(over))
    })
    act(() => {
      root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />)
    })
    return container.querySelector('div')!
  }

  it('没设过字体 = 文档默认族（老文档的画面一个像素不变）', () => {
    expect(renderOne({ text: 'H2O' }).style.fontFamily).toBe('var(--font-doc)')
  })

  it('设过就按它画——不是只在属性页里换了个数字', () => {
    const el = renderOne({ text: 'H2O', fontFamily: 'monospace' })
    // 判据是**画布上的那条 CSS**，不是文档里的那个字段：两者之间就是这一轮
    // 补上的那一段线，量文档等于自己验自己
    expect(el.style.fontFamily).not.toBe('var(--font-doc)')
    expect(el.style.fontFamily).toBe(canvasFontStack('monospace'))
  })

  it('三个族在画布上各不相同（尺子看得见「族」这一维）', () => {
    const seen = new Set<string>()
    for (const f of CANVAS_TEXT_FAMILIES) seen.add(canvasFontStack(f))
    expect(seen.size).toBe(CANVAS_TEXT_FAMILIES.length)
  })
})

describe('TextView 编辑提交（所见即所得就地编辑，ADR 0152）', () => {
  const render = () =>
    act(() => {
      root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />)
    })
  const editor = () => container.querySelector<HTMLElement>('[data-rich-text="inline"]')!

  it('打字后失焦：一条历史写回 LaTeX 子集，退出后正文只显示一遍', async () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj({ text: '' }))
    })
    render()
    await act(async () => {
      useUiStore.getState().setEditingText('t1')
    })
    render()
    const view = richViewOf(editor())!
    await act(async () => {
      editor().focus()
      view.dispatch(view.state.tr.insertText('（a）'))
      editor().blur()
    })
    render()
    expect(useDocumentStore.getState().doc.objects[0]).toMatchObject({ text: '（a）', textFormat: 'latex' })
    expect(useUiStore.getState().editingTextId).toBeNull()
    expect(container.textContent).toBe('（a）')
  })

  it('进入编辑：编辑器里就是现有正文（带样式），只出现一遍', async () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj({ text: 'T \\textit{x}', textFormat: 'latex' }))
    })
    render()
    await act(async () => {
      useUiStore.getState().setEditingText('t1')
    })
    render()
    expect(editor().textContent).toBe('T x')
    expect(editor().querySelector('em')?.textContent).toBe('x')
  })

  it('没改就退出：不写文档、不进历史', async () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj({ text: 'cm^{-1}' }))
    })
    render()
    await act(async () => {
      useUiStore.getState().setEditingText('t1')
    })
    render()
    const before = useDocumentStore.getState().past.length
    await act(async () => {
      editor().focus()
      editor().blur()
    })
    expect(useDocumentStore.getState().doc.objects[0]).toMatchObject({ text: 'cm^{-1}' })
    expect((useDocumentStore.getState().doc.objects[0] as TextObject).textFormat).toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(before)
  })
})

/**
 * 预览的上下标合成与导出走**同一份判据**。
 *
 * 这里量的是「预览按覆盖表决定合不合成」，不是「浏览器能不能显示 ⁵」——
 * jsdom 与真浏览器当然都显示得出，拿它当判据的结果正是「预览好好的、导出
 * 上是个方框」。
 */
describe('科学文本解释', () => {
  const renderOne = (over: Partial<TextObject>) => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj(over))
    })
    act(() => {
      root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />)
    })
    return container.querySelector('div')!
  }

  it('默认（auto）原样显示 Unicode 上标——文本层不降级，预览也不该先降级', () => {
    const el = renderOne({ text: '×10⁵' })
    expect(el.textContent).toBe('×10⁵')
    expect(el.querySelectorAll('span[style]').length).toBe(0)
  })

  it('scientific 档不动主脸自己画得出的上标（`⁵` 是 Liberation 自带的 primary）', () => {
    // U10 之前 base-14 没有它，同一档会折成 `×105`（ADR 0072 §2 的用户可见变化表）
    expect(renderOne({ text: '×10⁵', interpretation: 'scientific' }).textContent).toBe('×10⁵')
  })

  it('scientific 档合成上标：基础字符 + 缩小抬高的 span', () => {
    // 只合成主脸画不出的：`⁻`（U+207B）哪张批准脸都没有（ADR 0060 §1），整段 `⁻²` 一起折成上标 `-2`
    const el = renderOne({ text: 'm⁻²', interpretation: 'scientific' })
    expect(el.textContent).toBe('m-2')
    const sup = [...el.querySelectorAll('span')].find((s) => s.style.verticalAlign)
    expect(sup?.textContent).toBe('-2')
    // 抬高是正值（vertical-align 正 = 往上）；字号按 SCRIPT_SIZE 缩
    expect(parseFloat(sup!.style.verticalAlign)).toBeGreaterThan(0)
    expect(parseFloat(sup!.style.fontSize)).toBeLessThan(mmPxOf(el))
  })

  it('`m²` 两档都不动——那是主脸自己画得出的设计字形', () => {
    expect(renderOne({ text: 'm²', interpretation: 'scientific' }).textContent).toBe('m²')
  })

  it('LaTeX 写法：只有 \\textit 那一段是 italic、\\textbf 那段是粗体，命令字符不显示', () => {
    const el = renderOne({ text: 'temp \\textit{T} \\textbf{K}', textFormat: 'latex' })
    expect(el.textContent).toBe('temp T K')
    const spans = [...el.querySelectorAll('span')]
    expect(spans.filter((s) => s.style.fontStyle === 'italic').map((s) => s.textContent)).toEqual(['T'])
    expect(spans.filter((s) => s.style.fontWeight === '700').map((s) => s.textContent)).toEqual(['K'])
  })

  it('没有 textFormat 的老标注：LaTeX 命令原样显示', () => {
    expect(renderOne({ text: '\\textit{T}' }).textContent).toBe('\\textit{T}')
  })
})

/** 取这段文字的正文字号（px），用来验证上标确实更小 */
function mmPxOf(el: HTMLElement): number {
  return parseFloat(el.style.fontSize)
}

/**
 * 编辑态的插入点与占位（2026-10-07 设计审计 §10.1）：插入点是界面的 accent，不跟正文颜色；空着时有一句占位，
 * 只在 ::before 里（`data-placeholder`），不进正文。主语：编辑态那个 div 的内联 style 与 data 属性。
 */
describe('TextView 编辑态的插入点与占位', () => {
  it('编辑时：caret 是 accent、带占位；不编辑时两样都没有', () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj())
    })
    const obj = () => useDocumentStore.getState().doc.objects[0] as TextObject
    act(() => root.render(<TextView obj={obj()} />))
    const el = () => container.querySelector<HTMLElement>('div')!
    expect(el().style.caretColor).toBe('')
    expect(container.querySelector('[data-placeholder]')).toBeNull()
    act(() => useUiStore.setState({ editingTextId: 't1' }))
    act(() => root.render(<TextView obj={obj()} />))
    expect(el().style.caretColor).toBe('var(--color-accent)')
    const editor = container.querySelector<HTMLElement>('[data-rich-text="inline"]')!
    expect(editor.dataset.placeholder).toBe('输入文字')
    expect(editor.textContent, '占位不进正文').toBe('')
    expect(editor.hasAttribute('data-empty')).toBe(true)
  })

  it('打字后不算空；全删之后又算空', () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj())
    })
    const obj = () => useDocumentStore.getState().doc.objects[0] as TextObject
    act(() => useUiStore.setState({ editingTextId: 't1' }))
    act(() => root.render(<TextView obj={obj()} />))
    const editor = () => container.querySelector<HTMLElement>('[data-rich-text="inline"]')!
    const view = richViewOf(editor())!
    act(() => view.dispatch(view.state.tr.insertText('a')))
    expect(editor().hasAttribute('data-empty')).toBe(false)
    act(() => view.dispatch(view.state.tr.delete(0, view.state.doc.content.size)))
    expect(editor().hasAttribute('data-empty')).toBe(true)
  })
})

/**
 * 改字时的文字选区（Codex P2，PR #834 两轮）：全局 `::selection` 是界面的（半透明 accent tint + 界面 ink）。页面底色（CanvasPage）
 * 与文字框底色（`obj.bg`）都是用户定的：深底上换 paper-ink 是深字压深底，中灰上 tint 配哪种纸墨都到不了 4.5:1。
 * 主语：编辑态那个 div 上 `selection:*` 落到的那对 token（不透明的选区底 + 配它的纸墨）——由「这段字实际坐在什么上」
 * （obj.bg → 页面底色 → 纸）挑。token 合成后的对比度在 tokenContrast.test 量。
 */
describe('TextView 纸上的文字选区按实际的底取', () => {
  const selection = (over: Partial<TextObject>, page?: { bg?: string; transparent?: boolean }) => {
    useDocumentStore.getState().silent((d) => {
      if (page) Object.assign(d.page, page)
      d.objects.push(textObj({ text: 'Fig. 1', ...over }))
    })
    useUiStore.setState({ editingTextId: 't1' })
    act(() => root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />))
    const cls = container.querySelector<HTMLElement>('div')!.className
    return [...cls.matchAll(/(?:^|\s)selection:((?:bg|text)-[\w-]+)(?=\s|$)/g)].map((m) => m[1]).sort()
  }
  const LIGHT = ['bg-paper-selection', 'text-paper-ink']
  const DEEP = ['bg-paper-selection-deep', 'text-paper']

  it('文字框自己是深底（#000）、字是浅色：深的选区配纸色的字，不是深字', () => {
    expect(selection({ color: '#f5f5f5', bg: '#000000' })).toEqual(DEEP)
  })

  it('文字框没底、页面底色是深色：跟着页面底色走', () => {
    expect(selection({ color: '#f5f5f5' }, { bg: '#101820' })).toEqual(DEEP)
  })

  it('中灰（#7d7d7d）上也自己定底：不靠半透明 tint', () => {
    expect(selection({ bg: '#7d7d7d' })).toEqual(LIGHT)
  })

  it('文字框的浅底压在深色页面上：以文字框自己的底为准', () => {
    expect(selection({ bg: '#FFFFFF' }, { bg: '#000000' })).toEqual(LIGHT)
  })

  it('白纸：浅的选区配纸上的墨', () => {
    expect(selection({})).toEqual(LIGHT)
  })

  it('页面设了深色但标成透明：画的是浅棋盘格，按纸算', () => {
    expect(selection({}, { bg: '#000000', transparent: true })).toEqual(LIGHT)
  })
})
