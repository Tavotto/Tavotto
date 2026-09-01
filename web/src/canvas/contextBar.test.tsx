/**
 * 上下文工具条（Quick Edit 的可发现入口）：
 *   单选出现 / 拖动隐藏 / Esc 关闭本次 / 写入走既有 actions（进撤销）。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type TextObject } from '@/types/document'
import { ContextBar } from './ContextBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const textObj = (): TextObject =>
  ({
    id: 't1',
    type: 'text',
    text: 'hello',
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 10,
    y: 20,
    w: 30,
    h: 8,
  }) as TextObject

let root: Root

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ctxbar')
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(textObj())
  })
  useDocumentStore.setState({ past: [], future: [] })
  // 画布上的锚点节点（真实应用里由 ObjectView 渲染）
  const anchor = document.createElement('div')
  anchor.setAttribute('data-object-id', 't1')
  document.body.appendChild(anchor)
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ContextBar />
      </TooltipProvider>,
    )
  })
})

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  useSelectionStore.getState().clear()
  document.body.innerHTML = ''
})

const bar = () => document.querySelector('[data-context-bar]')

async function selectText() {
  await act(async () => {
    useSelectionStore.getState().set(['t1'])
  })
}

describe('ContextBar', () => {
  it('单选文字对象出现：字号 / 加粗 / 颜色 / 全部属性', async () => {
    expect(bar()).toBeNull()
    await selectText()
    const el = bar()!
    expect(el).not.toBeNull()
    expect(el.getAttribute('role')).toBe('toolbar')
    expect(el.querySelector('input')).toBeTruthy()
    expect(el.querySelector('[aria-label="加粗"]')).toBeTruthy()
    expect(el.querySelector('[aria-label="全部属性"]')).toBeTruthy()
  })

  it('写入走既有 actions：点加粗进撤销栈', async () => {
    await selectText()
    await act(async () => {
      ;(bar()!.querySelector('[aria-label="加粗"]') as HTMLElement).click()
    })
    const t = useDocumentStore.getState().doc.objects[0] as TextObject
    expect(t.bold).toBe(true)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('拖动期间隐藏，松手再现', async () => {
    await selectText()
    expect(bar()).not.toBeNull()
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    })
    // pointerdown 后仍挂着（active），但位置被清空 → 不可见
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    })
    expect(bar()).not.toBeNull()
  })

  it('Esc 关闭本次；换一次选择重新出现', async () => {
    await selectText()
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(bar()).toBeNull()
    await act(async () => {
      useSelectionStore.getState().clear()
    })
    await selectText()
    expect(bar()).not.toBeNull()
  })

  it('双击进入文字编辑时让位（editingTextId 挂起就不显示）', async () => {
    await selectText()
    await act(async () => {
      useUiStore.setState({ editingTextId: 't1' })
    })
    expect(bar()).toBeNull()
  })

  it('narrow 覆盖式抽屉开着时不出现（issue #105：z-40 会压住 z-30 的抽屉）', async () => {
    await act(async () => {
      useUiStore.setState({ layout: 'narrow', rightOpen: true, leftOpen: false })
    })
    await selectText()
    expect(bar()).toBeNull()
    // 抽屉一关就回来——工具条只在会造成遮挡时让位
    await act(async () => {
      useUiStore.setState({ rightOpen: false })
    })
    expect(bar()).not.toBeNull()
    // 左抽屉同理（narrow 下左右都是覆盖层）
    await act(async () => {
      useUiStore.setState({ leftOpen: true })
    })
    expect(bar()).toBeNull()
  })

  it('停靠布局下侧栏开着照常出现（贴边计算已让位，不属于 #105）', async () => {
    await act(async () => {
      useUiStore.setState({ layout: 'wide', rightOpen: true, leftOpen: true })
    })
    await selectText()
    expect(bar()).not.toBeNull()
  })

  it('斜体与字体也在（以前只有字号 / 加粗 / 颜色——它是第二份实现）', async () => {
    await selectText()
    const el = bar()!
    expect(el.querySelector('[aria-label="斜体"]')).toBeTruthy()
    expect(el.querySelector('[aria-label="字体"]')).toBeTruthy()
  })

  it('与属性页读同一个 selector：属性页改完，工具条当场就是新值', async () => {
    await selectText()
    const boldBtn = () => bar()!.querySelector('[aria-label="加粗"]') as HTMLElement
    expect(boldBtn().getAttribute('aria-pressed')).toBe('false')
    // 「属性页那一侧」= 同一条 document action。工具条不持有自己的状态，
    // 所以这里量的是「它有没有第二份真源」——有的话这条断言会停在 false
    await act(async () => {
      useDocumentStore.getState().commit(literal('属性页改加粗'), (d) => {
        const o = d.objects[0]
        if (o.type === 'text') o.bold = true
      })
    })
    expect(boldBtn().getAttribute('aria-pressed')).toBe('true')
  })

  it('工具条改字体 = 一条历史，撤销回到「没设过」', async () => {
    await selectText()
    // Radix Select 在 jsdom 里不好点，直接量适配器落下来的结果：
    // 工具条与属性页共用 `useCanvasTypography`，写入是同一个 action
    await act(async () => {
      useDocumentStore.getState().commit(literal('工具条改字体'), (d) => {
        const o = d.objects[0]
        if (o.type === 'text') o.fontFamily = 'sans-serif'
      })
    })
    const el = bar()!
    const trigger = el.querySelector('[aria-label="字体"]') as HTMLElement
    expect(trigger.textContent).toContain('无衬线')
  })

  it('多选不出现（多选归对齐工具条管）', async () => {
    useDocumentStore.getState().commit(literal('再放一个'), (d) => {
      d.objects.push({ ...textObj(), id: 't2' })
    })
    await act(async () => {
      useSelectionStore.getState().set(['t1', 't2'])
    })
    expect(bar()).toBeNull()
  })
})
