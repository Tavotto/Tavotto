/**
 * 文字标注：退出编辑态后正文只显示一遍。
 *
 * 背景（2026-08-25 用户报告：键入「（a）」显示「（a）（a）」）：编辑期
 * contentEditable 往 div 里写的文本节点不归 React 管——React 在编辑态渲染的
 * 子节点是 null。退出编辑时 React 只把 <RenderedText> 插进去、不清野节点，
 * 于是 contentEditable 留下的那份和 React 渲染的那份同时在场，正文显示两遍。
 * （回归自 104729b：在那之前非编辑态子节点是纯字符串，React 对单字符串子节点
 * 走 textContent 直写路径，顺手清掉了整个 DOM；换成 <RenderedText> 元素后
 * 这条清扫就没了。）
 *
 * 修法是 div 的 key 随编辑态变（editing/static），两个方向的切换都强制重建
 * DOM。这里模拟 contentEditable 的行为直接往 DOM 塞真实文本节点——jsdom 不
 * 实现 innerText（赋值只是 expando 属性），所以 textContent + innerText 两个
 * 都要摆：前者是 React 看得见的真实 DOM，后者是 commitText 读的那份。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type TextObject } from '@/types/document'
import { CANVAS_TEXT_FAMILIES, canvasFontStack } from '@/lib/typography'
import { TextView } from './TextView'

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

/** 模拟 contentEditable 打字：真实文本节点（React 不知情）+ innerText expando */
function typeInto(el: HTMLElement, text: string) {
  el.textContent = text
  // jsdom 的 innerText 赋值不落进 DOM，只是普通属性——commitText 读的正是它
  ;(el as unknown as { innerText: string }).innerText = text
}

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

describe('TextView 编辑提交', () => {
  it('退出编辑后正文只显示一遍（不残留 contentEditable 的野节点）', async () => {
    const obj = textObj()
    useDocumentStore.getState().silent((d) => {
      d.objects.push(obj)
    })
    const render = () =>
      act(() => {
        root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />)
      })
    render()

    // 进入编辑态（等一拍让编辑 effect 跑完）
    await act(async () => {
      useUiStore.getState().setEditingText('t1')
    })
    render()
    const editingEl = container.querySelector('div')!
    expect(editingEl.getAttribute('contenteditable')).toBe('plaintext-only')

    // 打字 + 失焦提交（React 的 onBlur 由 focusout 驱动）
    await act(async () => {
      typeInto(editingEl, '（a）')
      editingEl.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    render()

    expect(useDocumentStore.getState().doc.objects[0]).toMatchObject({ text: '（a）' })
    // 修之前这里是「（a）（a）」：React 插入的 <RenderedText> 与
    // contentEditable 留下的文本节点同时在场
    expect(container.textContent).toBe('（a）')
  })

  it('再次进入编辑不把上一轮的渲染文本带进 DOM', async () => {
    useDocumentStore.getState().silent((d) => {
      d.objects.push(textObj({ text: '（a）' }))
    })
    const render = () =>
      act(() => {
        root.render(<TextView obj={useDocumentStore.getState().doc.objects[0] as TextObject} />)
      })
    render()
    expect(container.textContent).toBe('（a）')

    await act(async () => {
      useUiStore.getState().setEditingText('t1')
    })
    render()
    const editingEl = container.querySelector('div')!
    // 编辑态从空 DOM 开始（编辑 effect 的 innerText 赋值在 jsdom 里不进 DOM，
    // 真浏览器里它会重设全部内容）；展示态渲染的文本节点必须已被清走
    expect(editingEl.textContent).toBe('')
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
    expect(el().dataset.placeholder).toBeUndefined()
    act(() => useUiStore.setState({ editingTextId: 't1' }))
    act(() => root.render(<TextView obj={obj()} />))
    expect(el().style.caretColor).toBe('var(--color-accent)')
    expect(el().dataset.placeholder).toBe('输入文字')
    expect(el().textContent, '占位不进正文').toBe('')
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
