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
import { t } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
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
const rows = () => Array.from(document.querySelectorAll<HTMLElement>('li[data-layer]'))
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

const menuItem = (items: HTMLElement[], label: string) =>
  items.find((el) => el.textContent?.trim() === label)

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('图层行的行尾', () => {
  it('没有状态的行：只有一颗 ⋯，锁 / 眼不再常驻', async () => {
    await mount([panel('p1', 'Fig1')])
    const btns = rowButtons(0)
    expect(btns).toHaveLength(1)
    expect(btns[0].getAttribute('aria-label')).toBe(lt('rowActions', { label: 'Fig1' }))
    // 那颗 ⋯ 平时是透明的，指到行 / 键盘落进来才浮出（与元素行同一条）
    expect(btns[0].parentElement!.className).toContain('group-hover:opacity-100')
    expect(btns[0].parentElement!.className).toContain('group-focus-within:opacity-100')
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
