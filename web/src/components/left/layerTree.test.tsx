/**
 * 图层行的行尾（2026-09-15 全面打磨拍板：与图内元素行同款）。
 *
 * 改造前行尾常驻两颗 28px 钮（锁定 / 隐藏）：省一次点击，代价是每一行都挂着
 * 两颗钮，而旁边那棵图内元素树对同一对操作是「12px 状态图标 + hover 才出的
 * ⋯」。两棵并列的树对同一件事长出两种形态，正是打磨要收掉的东西。
 *
 * 这里钉四条：
 *   1. 没有状态的行，行尾只有一颗 ⋯——两颗常驻钮不许回来；
 *   2. 已锁 / 已隐藏画 xs 状态图标（可达名说得出是什么），动作仍在 ⋯ 里；
 *   3. ⋯ 里的锁定 / 隐藏真的写进文档；
 *   4. 上移 / 下移一层作用在**打开菜单的那一行**上（不看选区），
 *      到顶 / 到底时那一项禁用——而不是点了什么都不发生。
 *
 * 第 4 条的反向那一半（顶行禁用）单独量：只测「点了会换位」的话，把
 * `canMoveUp` 写死成 true 照样绿。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useKeyboard } from '@/hooks/useKeyboard'
import { t } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const lt = (key: string, values?: Record<string, unknown>) =>
  t(`layerTree.${key}`, { ns: 'workspace', ...(values ?? {}) })

const panel = (id: string, name: string): CanvasObject =>
  ({
    id,
    type: 'panel',
    fileId: `${name}.pdf`,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    overrides: [],
    x: 0,
    y: 0,
    w: 80,
    h: 60,
  }) as CanvasObject

let root: Root

async function mount(objects: CanvasObject[]) {
  useSelectionStore.getState().set([])
  useDocumentStore.setState({
    doc: { ...emptyProject(), objects },
    past: [],
    future: [],
    txn: null,
  } as never)
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <LayerTree />
      </TooltipProvider>,
    )
  })
}

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

/** 第 n 行（顶层在最上面 = z 序倒序） */
// 收起的组成员保活一个退场时长（高度过渡），期间整棵 inert：不算「在树里」
const rows = () =>
  Array.from(document.querySelectorAll<HTMLElement>('li[data-layer]')).filter((r) => !r.closest('[inert]'))
const rowButtons = (i: number) => Array.from(rows()[i].querySelectorAll('button'))

/**
 * 打开这一行的 ⋯ 菜单。Radix 的触发器认 pointerdown（鼠标）与 Enter / Space，
 * 不认光秃秃的 `click()`；内容挂进 portal 之后还要等一拍。
 */
async function openRowMenu(i: number) {
  const trigger = rowButtons(i).at(-1)!
  await act(async () => {
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await new Promise((r) => setTimeout(r, 0))
  })
  return Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]'))
}

/** 菜单项的名字（快捷键提示「⌥↑」在同一项里，按开头认） */
const menuItem = (items: HTMLElement[], label: string) =>
  items.find((el) => el.textContent?.trim().startsWith(label))

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('图层行的行尾', () => {
  it('没有状态的行：只有一颗 ⋯，锁 / 眼不再常驻', async () => {
    await mount([panel('p1', 'Fig1')])
    const btns = rowButtons(0)
    expect(btns).toHaveLength(1)
    expect(btns[0].getAttribute('aria-label')).toBe(lt('rowActions', { label: 'Fig1' }))
    // 那颗 ⋯ 平时是透明的，指到行 / 键盘落进来才浮出（与元素行同一条，`ui/RowMenu`）
    expect(btns[0].className).toContain('group-hover:opacity-100')
    expect(btns[0].className).toContain('group-focus-within:opacity-100')
  })

  it('已锁 / 已隐藏：行尾画状态图标，动作仍在 ⋯ 里', async () => {
    await mount([{ ...panel('p1', 'Fig1'), locked: true, hidden: true } as CanvasObject])
    const row = rows()[0]
    // 状态是图标不是钮：数得出名字，但不是第二颗可点的东西
    expect(row.querySelector(`[aria-label="${lt('lockedState')}"]`)).not.toBeNull()
    expect(row.querySelector(`[aria-label="${lt('hiddenState')}"]`)).not.toBeNull()
    expect(rowButtons(0)).toHaveLength(1)

    const items = await openRowMenu(0)
    // 已经锁着 / 藏着，菜单里说的是反向动作
    expect(menuItem(items, lt('unlock'))).toBeDefined()
    expect(menuItem(items, lt('show'))).toBeDefined()
  })

  it('⋯ 里点「锁定」写进文档', async () => {
    await mount([panel('p1', 'Fig1')])
    const items = await openRowMenu(0)
    await act(async () => {
      menuItem(items, lt('lock'))!.click()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(useDocumentStore.getState().doc.objects[0].locked).toBe(true)
  })

  it('上移一层作用在这一行上；顶上那一行的「上移一层」禁用', async () => {
    // 文档序 = 从底到顶，所以显示顺序是 Fig2（顶）、Fig1（底）
    await mount([panel('p1', 'Fig1'), panel('p2', 'Fig2')])
    expect(rows().map((r) => r.dataset.layer)).toEqual(['p2', 'p1'])

    // 顶上那一行没有可换的上家
    const topItems = await openRowMenu(0)
    expect(menuItem(topItems, lt('moveUp'))!.getAttribute('aria-disabled')).toBe('true')
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await new Promise((r) => setTimeout(r, 0))
    })

    // 下面那一行上移一层：选区是空的，动作照样落在 p1 上
    const items = await openRowMenu(1)
    expect(menuItem(items, lt('moveUp'))!.getAttribute('aria-disabled')).toBeNull()
    await act(async () => {
      menuItem(items, lt('moveUp'))!.click()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p2', 'p1'])
  })
})

// Codex #833：行内改名用 Enter / Esc 收起，焦点回到这一行（方向键漫游不断链）；点了别处收起的不抢回来——
// 焦点已经在用户点的地方了，抢回这一行会让接下来的打字 / 快捷键落进图层树
describe('图层改名收起后的焦点', () => {
  const renameBox = () => rows()[0].querySelector('input')
  const startRename = async () => {
    await mount([panel('p1', 'Fig1')])
    rows()[0].focus()
    await act(async () => {
      rows()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true, cancelable: true }))
    })
    expect(document.activeElement).toBe(renameBox())
  }
  const settle = () => act(async () => new Promise<void>((r) => setTimeout(r, 10)))

  it.each(['Enter', 'Escape'])('%s 收起改名：焦点回到这一行', async (k) => {
    await startRename()
    await act(async () => {
      renameBox()!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    })
    await settle()
    expect(renameBox()).toBeNull()
    expect(document.activeElement).toBe(rows()[0])
  })

  it('点别处收起改名：焦点留在用户点的那个输入框上', async () => {
    await startRename()
    const other = document.createElement('input')
    document.body.appendChild(other)
    await act(async () => other.focus())
    await settle()
    expect(renameBox()).toBeNull()
    expect(document.activeElement).toBe(other)
  })
})

describe('树的语义与键位契约（2026-10-07 设计审计 §10.3）', () => {
  const key = (el: Element, k: string, init: KeyboardEventInit = {}) =>
    act(() => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
    })

  it('role=tree，每行报层级与同层位置（aria-level / posinset / setsize）', async () => {
    await mount([panel('p1', 'Fig1'), panel('p2', 'Fig2'), panel('p3', 'Fig3')])
    expect(document.querySelector('ul[role="tree"]')).not.toBeNull()
    const r = rows()
    expect(r.map((x) => x.getAttribute('role'))).toEqual(['treeitem', 'treeitem', 'treeitem'])
    expect(r.map((x) => x.getAttribute('aria-level'))).toEqual(['1', '1', '1'])
    expect(r.map((x) => x.getAttribute('aria-posinset'))).toEqual(['1', '2', '3'])
    expect(r.every((x) => x.getAttribute('aria-setsize') === '3')).toBe(true)
  })

  it('Enter = 只选这一个（主操作），F2 才是改名', async () => {
    await mount([panel('p1', 'Fig1'), panel('p2', 'Fig2')])
    useSelectionStore.getState().set(['p1', 'p2'])
    key(rows()[0], 'Enter')
    expect(useSelectionStore.getState().ids).toEqual(['p2'])
    expect(rows()[0].querySelector('input')).toBeNull()
    key(rows()[0], 'F2')
    expect(rows()[0].querySelector('input')).not.toBeNull()
  })

  it('图内编辑态还开着时，行上按一次 Esc 只清选区：不再冒到窗口级快捷键去退元素 / 图内编辑（Codex #832）', async () => {
    function Keys() {
      useKeyboard()
      return null
    }
    await mount([panel('p1', 'Fig1'), panel('p2', 'Fig2')])
    const host = document.createElement('div')
    document.body.appendChild(host)
    const keysRoot = createRoot(host)
    await act(async () => keysRoot.render(<Keys />))
    try {
      // 换左栏页签不结束图内编辑：那一段与选中的元素都还在
      useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
      useSelectionStore.getState().set(['p2'])
      act(() => rows()[0].focus())
      key(rows()[0], 'Escape')
      expect(useSelectionStore.getState().ids).toEqual([])
      expect(useUiStore.getState().elementPanelId).toBe('p1')
      expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
    } finally {
      await act(async () => keysRoot.unmount())
      useUiStore.setState({ elementPanelId: null, selectedGids: [] })
    }
  })

  it('⇧F10 开出与 ⋯ 同一份菜单；行有焦点时 ⋯ 进 Tab 顺序', async () => {
    await mount([panel('p1', 'Fig1')])
    const row = rows()[0]
    const more = rowButtons(0).at(-1)!
    expect(more.tabIndex).toBe(-1)
    act(() => row.focus())
    expect(more.tabIndex).toBe(0)
    key(row, 'F10', { shiftKey: true })
    expect(document.querySelector('[role="menu"]')).not.toBeNull()
  })
})

describe('图层树的树键位（WAI-ARIA tree，与元素树同一份 ui/treeKeys）', () => {
  const press = async (el: HTMLElement, key: string) => {
    await act(async () => {
      el.focus()
      el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    })
    await act(async () => {})
  }
  const grouped = () => [
    panel('p1', 'A'),
    { ...panel('p2', 'B'), groupId: 'g1' } as CanvasObject,
    { ...panel('p3', 'C'), groupId: 'g1' } as CanvasObject,
    panel('p4', 'D'),
  ]
  const keys = () => rows().map((r) => r.dataset.layer)

  it('Home / End 跳到第一行 / 最后一行', async () => {
    await mount(grouped())
    await press(rows()[1], 'End')
    expect((document.activeElement as HTMLElement).dataset.layer).toBe(keys().at(-1))
    await press(document.activeElement as HTMLElement, 'Home')
    expect((document.activeElement as HTMLElement).dataset.layer).toBe(keys()[0])
  })

  it('组标题：→ 在已展开时进第一个成员，← 先收起；成员 ← 回组标题', async () => {
    await mount(grouped())
    const group = () => rows().find((r) => r.dataset.layer!.startsWith('g:'))!
    expect(group().getAttribute('aria-expanded')).toBe('true')
    await press(group(), 'ArrowRight')
    const member = document.activeElement as HTMLElement
    expect(member.getAttribute('aria-level')).toBe('2')
    await press(member, 'ArrowLeft')
    expect(document.activeElement).toBe(group())
    await press(group(), 'ArrowLeft')
    expect(group().getAttribute('aria-expanded')).toBe('false')
    expect(rows().some((r) => r.getAttribute('aria-level') === '2')).toBe(false)
    await press(group(), 'ArrowRight')
    expect(group().getAttribute('aria-expanded')).toBe('true')
  })

  it('顶层叶子行 ← 不动', async () => {
    await mount(grouped())
    const top = rows()[0]
    await press(top, 'ArrowLeft')
    expect(document.activeElement).toBe(top)
  })
})

describe('树行的列间距（G5）', () => {
  it('图层行 / 组标题行不再自己写 gap：图标到文字与元素树同为 listRowClass 的 4px', async () => {
    await mount([panel('p1', 'A'), { ...panel('p2', 'B'), groupId: 'g1' } as CanvasObject])
    for (const r of rows()) expect(r.className).not.toMatch(/\bgap-(?!1\b)\S+/)
    expect(rows()[0].className).toMatch(/\bgap-1\b/)
  })
})

describe('选中图层滚入视野（G6）', () => {
  const withScrollSpy = async (fn: (calls: string[]) => Promise<void>) => {
    const calls: string[] = []
    const orig = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (this: Element, arg?: unknown) {
      expect(arg).toEqual({ block: 'nearest' })
      calls.push((this as HTMLElement).dataset.layer ?? '')
    }
    try {
      await fn(calls)
    } finally {
      Element.prototype.scrollIntoView = orig
    }
  }

  it('从画布选中某个图层：那一行 scrollIntoView({ block: nearest })——树仍持有焦点时也滚', async () => {
    await withScrollSpy(async (calls) => {
      await mount([panel('p1', 'A'), panel('p2', 'B'), panel('p3', 'C')])
      await act(async () => useSelectionStore.getState().set(['p2']))
      expect(calls.at(-1)).toBe('p2')
      // 树持有焦点（用户刚在树里点过 / 走过），随后画布上改选：照样滚
      await act(async () => rows().find((r) => r.dataset.layer === 'p2')!.focus())
      await new Promise((r) => setTimeout(r, 5)) // 树里那次操作的「来源」标记已过期
      calls.length = 0
      await act(async () => useSelectionStore.getState().set(['p3']))
      expect(calls).toEqual(['p3'])
    })
  })

  it('用户在树里按键 / 点击造成的选择变化：不额外滚（键盘移动的 focus() 自己带进视野）', async () => {
    await withScrollSpy(async (calls) => {
      await mount([panel('p1', 'A'), panel('p2', 'B'), panel('p3', 'C')])
      const first = rows()[0]
      await act(async () => first.focus())
      await new Promise((r) => setTimeout(r, 5))
      calls.length = 0
      await act(async () => {
        first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
      })
      expect(useSelectionStore.getState().ids).toEqual([rows()[1].dataset.layer])
      expect(calls).toEqual([])
      await new Promise((r) => setTimeout(r, 5))
      await act(async () => {
        rows()[2].dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 }))
      })
      expect(useSelectionStore.getState().ids).toEqual([rows()[2].dataset.layer])
      expect(calls).toEqual([])
    })
  })

  it('选中项在折叠的组里：滚到组标题行', async () => {
    await withScrollSpy(async (calls) => {
      await mount([
        panel('p1', 'A'),
        { ...panel('p2', 'B'), groupId: 'g1' } as CanvasObject,
        { ...panel('p3', 'C'), groupId: 'g1' } as CanvasObject,
      ])
      const toggle = rows().find((r) => r.dataset.layer === 'g:g1')!.querySelector('button')!
      await act(async () => toggle.click())
      expect(rows().some((r) => r.dataset.layer === 'p2')).toBe(false)
      calls.length = 0
      await act(async () => useSelectionStore.getState().set(['p2']))
      expect(calls.at(-1)).toBe('g:g1')
    })
  })
})

describe('被拖行淡化（G11）', () => {
  it('dragstart 时原位那一行淡化，dragend 还原', async () => {
    await mount([panel('p1', 'A'), panel('p2', 'B')])
    const row = rows()[0]
    expect(row.className).not.toMatch(/opacity-dragging/)
    const ev = (type: string) => {
      const e = new Event(type, { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown }
      e.dataTransfer = { setData() {}, effectAllowed: '', types: [] }
      return e
    }
    await act(async () => row.dispatchEvent(ev('dragstart')))
    expect(rows()[0].className).toMatch(/\bopacity-dragging\b/)
    expect(rows()[0].dataset.dragging).toBe('true')
    await act(async () => rows()[0].dispatchEvent(ev('dragend')))
    expect(rows()[0].className).not.toMatch(/opacity-dragging/)
  })
})

describe('空态（UI 2.0 第二轮 09）', () => {
  it('没有对象：列表顶部一行灰字，没有大图标与标题，也没有任何按钮 / 链接（起步动作只有画布中央的「添加图」，审计 T03）', async () => {
    await mount([])
    const line = document.querySelector<HTMLElement>('[data-empty-line]')!
    expect(line.textContent).toBe(lt('emptyTitle'))
    expect(document.querySelector('[data-empty-state]')).toBeNull()
    expect(line.querySelector('svg')).toBeNull()
    expect(line.querySelector('button, a, [role="button"], [role="link"]')).toBeNull()
  })
})

describe('展开 / 折叠组时不走 FLIP（TreeBranch 的 grid 过渡单独负责），重排照旧', () => {
  it('折叠组期间的位移不播；过渡结束后基准重量，之后的位移按真实值播（不从半路多滑）', async () => {
    const anims: Array<{ id: string; dy: number }> = []
    let shift = 0
    const protoRect = HTMLElement.prototype.getBoundingClientRect
    const protoAnimate = (Element.prototype as { animate?: unknown }).animate
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      const id = this.getAttribute('data-layer')
      const top = id === 'p4' ? 100 + shift : id ? 10 : 0
      return { left: 0, top, width: 100, height: 20, right: 100, bottom: top + 20, x: 0, y: top } as DOMRect
    }
    ;(Element.prototype as { animate?: unknown }).animate = function (this: Element, frames: Keyframe[]) {
      const m = String((frames[0] as { transform?: string }).transform).match(/(-?[\d.]+)px[ ,]+\s*(-?[\d.]+)px/)
      anims.push({ id: (this as HTMLElement).dataset.layer ?? '', dy: m ? Number(m[2]) : NaN })
      return { finished: Promise.resolve(), cancel() {} } as unknown as Animation
    }
    try {
      await mount([
        panel('p1', 'A'),
        { ...panel('p2', 'B'), groupId: 'g1' } as CanvasObject,
        { ...panel('p3', 'C'), groupId: 'g1' } as CanvasObject,
        panel('p4', 'D'),
      ])
      anims.length = 0
      // 折叠组：p4 随之上移（终点 −40），同一次提交——不播
      shift = -10 // 过渡半路
      const toggle = rows().find((r) => r.dataset.layer === 'g:g1')!.querySelector('button')!
      await act(async () => toggle.click())
      expect(anims.filter((a) => a.id === 'p4')).toEqual([])
      // 过渡进行中夹进一次改选提交（p4 在半路）：让位期内，只记位置、不播
      await act(async () => useSelectionStore.getState().set(['p1']))
      expect(anims.filter((a) => a.id === 'p4')).toEqual([])
      // 过渡结束（保活期过去、子树卸载 → onSettled → rebase）：p4 到了终点
      shift = -40
      await act(async () => {
        await new Promise((r) => setTimeout(r, 200))
      })
      expect(anims.filter((a) => a.id === 'p4')).toEqual([])
      // 之后别的原因造成的位移：p4 从 −40 → −16，位移是真实的 24（从半路 −10 起算会是 6）
      shift = -16
      await act(async () => useSelectionStore.getState().set(['p4']))
      expect(anims.filter((a) => a.id === 'p4')).toEqual([{ id: 'p4', dy: -24 }])
    } finally {
      HTMLElement.prototype.getBoundingClientRect = protoRect
      ;(Element.prototype as { animate?: unknown }).animate = protoAnimate
    }
  })
})
