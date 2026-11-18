/**
 * 一次选多份、各画一张：数据抽屉里的多选（⌘ / Ctrl / Shift / 「＋」/ 键盘 / Esc / 不可选的行）与底部那一条。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { msg } from '@/i18n'
import type { AiDataFileEntry } from '@/lib/api'
import { fileRows, tableKey, type TableRef } from '@/lib/dataFigure'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { DataBatchBar } from './DataBatchBar'
import { DataList, type DataListMulti } from './DataList'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
async function mount(ui: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{ui}</TooltipProvider>))
}
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
})
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)]

const okT = (rows: number) => ({ status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows })
const entries: AiDataFileEntry[] = [
  { path: 'a.csv', size: 1, table: okT(10) },
  { path: 'b.csv', size: 1, table: okT(20) },
  { path: 'bad.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null, preview: [] } },
  { path: 'sub/d.csv', size: 1, table: okT(5) },
  { path: 'book.xlsx', size: 1, sheets: [{ name: 'P1', hidden: false, rows: 24, columns: 2, status: 'ok', code: null }, { name: 'P2', hidden: false, rows: 8, columns: 2, status: 'ok', code: null }] },
]

describe('数据抽屉：多选', () => {
  const onSelect = vi.fn()
  const m = { onToggle: vi.fn(), onRange: vi.fn(), onClear: vi.fn() }
  const mountList = (over: Partial<DataListMulti> = {}) =>
    mount(<DataList rows={fileRows(entries)} selected={null} overviews={{}} onSelect={onSelect} multi={{ selected: new Set(), rowStates: {}, ...m, ...over }} />)
  const row = (name: string) => qa('[data-studio-nav]').find((r) => r.textContent!.includes(name))!
  beforeEach(() => {
    for (const f of [onSelect, m.onToggle, m.onRange, m.onClear]) f.mockClear()
  })
  const click = (el: Element, init: MouseEventInit = {}) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init })))
  const key = (el: Element, k: string, init: KeyboardEventInit = {}) => {
    const e = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init })
    act(() => void el.dispatchEvent(e))
    return e
  }

  it('⌘ / Ctrl + 点：加进 / 移出多选，不开临时标签；普通单击仍是原来的单击', async () => {
    await mountList()
    await click(row('a.csv'), { metaKey: true })
    expect(m.onToggle).toHaveBeenLastCalledWith({ path: 'a.csv', sheet: null })
    await click(row('b.csv'), { ctrlKey: true })
    expect(m.onToggle).toHaveBeenLastCalledWith({ path: 'b.csv', sheet: null })
    expect(onSelect).not.toHaveBeenCalled()
    await click(row('a.csv'))
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
  })

  it('Shift + 点：选一段，按当前可见的表行顺序（不可选的行不在里面；收起的工作簿里的表不算）', async () => {
    await mountList()
    await click(row('b.csv'), { shiftKey: true })
    const [ref, visible] = m.onRange.mock.calls[0] as [TableRef, TableRef[]]
    expect(ref).toEqual({ path: 'b.csv', sheet: null })
    // 文件夹在前；bad.dat 不是表；book.xlsx 还没展开，它的工作表不算
    expect(visible.map((r) => r.path)).toEqual(['sub/d.csv', 'a.csv', 'b.csv'])
    // 展开工作簿之后，工作表行进入可见顺序，并且工作表行同样能 ⌘ 点
    await click(row('book.xlsx'))
    await click(row('P2'), { shiftKey: true })
    const [, visible2] = m.onRange.mock.calls[1] as [TableRef, TableRef[]]
    expect(visible2.map((r) => `${r.path}|${r.sheet ?? ''}`)).toEqual(['sub/d.csv|', 'a.csv|', 'b.csv|', 'book.xlsx|P1', 'book.xlsx|P2'])
    await click(row('P1'), { metaKey: true })
    expect(m.onToggle).toHaveBeenLastCalledWith({ path: 'book.xlsx', sheet: 'P1' })
  })

  it('只有状态是 table 的行能进多选：读不了的行 ⌘ / Shift 点没有反应，也不会变成单击', async () => {
    await mountList()
    await click(row('bad.dat'), { metaKey: true })
    await click(row('bad.dat'), { shiftKey: true })
    expect(m.onToggle).not.toHaveBeenCalled()
    expect(m.onRange).not.toHaveBeenCalled()
    expect(onSelect).not.toHaveBeenCalled()
    expect(row('bad.dat').querySelector('[data-studio-pick-toggle]')).toBeNull()
  })

  it('「＋」：表行行尾灰字原位换成按钮（同一格、不撑宽），点它 = ⌘ 点，不开临时标签；已选的是对勾', async () => {
    await mountList({ selected: new Set([tableKey({ path: 'b.csv', sheet: null })]) })
    const plus = row('a.csv').querySelector<HTMLElement>('[data-studio-pick-toggle="off"]')!
    expect(plus).not.toBeNull()
    // 灰字和按钮在同一个格子里（grid 同格叠放），所以行不会变宽变高
    expect(plus.parentElement!.className).toContain('grid')
    expect(plus.className).toContain('col-start-1')
    await click(plus)
    expect(m.onToggle).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
    expect(onSelect).not.toHaveBeenCalled()
    expect(row('b.csv').querySelector('[data-studio-pick-toggle="on"]')).not.toBeNull()
    expect(row('b.csv').getAttribute('aria-selected')).toBe('true')
  })

  it('aria：树是 aria-multiselectable；选中的行 aria-selected', async () => {
    await mountList({ selected: new Set([tableKey({ path: 'a.csv', sheet: null })]) })
    expect(q('[role=tree]')!.getAttribute('aria-multiselectable')).toBe('true')
    expect(row('a.csv').getAttribute('aria-selected')).toBe('true')
    expect(row('b.csv').getAttribute('aria-selected')).toBe('false')
  })

  it('键盘：焦点行上 ⌘ / Ctrl + 空格切换（只有表行）；普通空格仍是打开', async () => {
    await mountList()
    row('a.csv').focus()
    expect(key(row('a.csv'), ' ', { metaKey: true }).defaultPrevented).toBe(true)
    expect(m.onToggle).toHaveBeenLastCalledWith({ path: 'a.csv', sheet: null })
    key(row('b.csv'), ' ', { ctrlKey: true })
    expect(m.onToggle).toHaveBeenCalledTimes(2)
    key(row('bad.dat'), ' ', { ctrlKey: true })
    expect(m.onToggle).toHaveBeenCalledTimes(2)
    expect(onSelect).not.toHaveBeenCalled()
    key(row('a.csv'), ' ')
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
  })

  it('Esc 清多选（有多选时这一下归它、不冒出去关临时标签）；没有多选时不拦', async () => {
    await mountList({ selected: new Set(['x']) })
    expect(key(row('a.csv'), 'Escape').defaultPrevented).toBe(true)
    expect(m.onClear).toHaveBeenCalledTimes(1)
    act(() => root.unmount())
    host.remove()
    await mountList()
    expect(key(row('a.csv'), 'Escape').defaultPrevented).toBe(false)
    expect(m.onClear).toHaveBeenCalledTimes(1)
  })

  it('这一份此刻的出图进度在行尾：转圈 / 已画 / 没画出来（原因在 title）；清单重读（rows 对象换了）状态按 tableKey 仍在', async () => {
    const rowStates = {
      [tableKey({ path: 'a.csv', sheet: null })]: { state: 'running' as const },
      [tableKey({ path: 'b.csv', sheet: null })]: { state: 'done' as const },
      [tableKey({ path: 'sub/d.csv', sheet: null })]: { state: 'failed' as const, reason: msg('dataFigureV2.nothing.generic', undefined, 'ai') },
    }
    await mountList({ rowStates })
    expect(row('a.csv').querySelector('[data-studio-row-state=running] svg')).not.toBeNull()
    expect(row('b.csv').querySelector('[data-studio-row-state=done]')!.textContent).toBe('已画')
    const failed = row('d.csv').querySelector('[data-studio-row-state=failed]')!
    expect(failed.textContent).toBe('没画出来')
    expect(failed.getAttribute('title')).toBe('这张表没能画出图。')
    // 清单重读：换一份新的 rows
    await act(async () => root.render(<TooltipProvider><DataList rows={fileRows(entries.map((e) => ({ ...e })))} selected={null} overviews={{}} onSelect={onSelect} multi={{ selected: new Set(), rowStates, ...m }} /></TooltipProvider>))
    expect(row('b.csv').querySelector('[data-studio-row-state=done]')).not.toBeNull()
  })

  it('不给 multi：行为与没有这个功能时相同（⌘ 点就是单击，没有「＋」）', async () => {
    await mount(<DataList rows={fileRows(entries)} selected={null} overviews={{}} onSelect={onSelect} />)
    await click(row('a.csv'), { metaKey: true })
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(q('[data-studio-pick-toggle]')).toBeNull()
    expect(q('[role=tree]')!.hasAttribute('aria-multiselectable')).toBe(false)
  })
})

describe('抽屉底部那一条 DataBatchBar', () => {
  const sel = (n: number) => useDataBatchStore.setState({ selection: Array.from({ length: n }, (_, i) => ({ path: `f${i}.csv`, sheet: null })) })
  beforeEach(() => useDataBatchStore.getState().clear())

  it('<2 份：这一条不存在；≥2 份：主按钮「各画一张」+ 灰字份数', async () => {
    sel(1)
    await mount(<DataBatchBar />)
    expect(q('[data-studio-batch-bar]')).toBeNull()
    act(() => sel(3))
    expect(q('[data-studio-batch-run]')!.textContent).toBe('各画一张')
    expect(q('[data-studio-batch-bar]')!.textContent).toContain('3')
    expect(q<HTMLButtonElement>('[data-studio-batch-run]')!.disabled).toBe(false)
  })

  it('进行中：按钮禁用、写「正在画 2 / 3」', async () => {
    sel(3)
    await mount(<DataBatchBar />)
    act(() => useDataBatchStore.setState({ running: true, progress: { done: 1, total: 3 } }))
    expect(q('[data-studio-batch-run]')!.textContent).toBe('正在画 2 / 3')
    expect(q<HTMLButtonElement>('[data-studio-batch-run]')!.disabled).toBe(true)
  })

  it('有没画成的（只剩 1 份也在）：按钮是「再试一次」；写不进文件夹：按钮是「换个文件夹」', async () => {
    sel(1)
    await mount(<DataBatchBar />)
    act(() => useDataBatchStore.setState({ rows: { k: { state: 'failed', reason: null } } }))
    expect(q('[data-studio-batch-run]')!.textContent).toBe('再试一次')
    act(() => useDataBatchStore.setState({ stop: 'unwritable' }))
    expect(q('[data-studio-batch-run]')!.textContent).toBe('换个文件夹')
  })
})
