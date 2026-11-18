/**
 * 一次选多份、各画一张：数据抽屉里的多选（⌘ / Ctrl / Shift / 键盘 / Esc / 不可选的行）、已选卡片与各状态的行尾、纯平铺的对齐，以及标题行下的动作区。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { msg } from '@/i18n'
import type { AiDataFileEntry } from '@/lib/api'
import { fileRows, tableKey, type TableRef } from '@/lib/dataFigure'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { DataBatchActions } from './DataBatchActions'
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

  it('已选的行是白底卡片（surface 底 + 细描边、medium 字重，不是 selected 灰底 + 粗体），行尾行数换成对勾；没选的行仍是灰字行数；没有「＋」', async () => {
    await mountList({ pickMode: true, selected: new Set([tableKey({ path: 'b.csv', sheet: null })]) })
    const b = row('b.csv')
    expect(b.className).toContain('bg-surface')
    expect(b.className).toContain('inset-ring')
    expect(b.className).toContain('font-medium')
    expect(b.className).not.toContain('bg-selected')
    expect(b.className).not.toContain('font-semibold')
    expect(b.className).not.toContain('shadow')
    expect(b.querySelector('[data-studio-row-state=picked] svg')).not.toBeNull()
    expect(b.textContent).not.toMatch(/行/)
    const a = row('a.csv')
    expect(a.className).not.toContain('inset-ring')
    expect(a.textContent).toMatch(/10 行/)
    expect(qa('[data-studio-pick-toggle]')).toHaveLength(0)
    // 相邻两行都选上：各是各的卡片，行高与行间距（gap-0.5 在列表上）不变
    act(() => root.unmount())
    host.remove()
    await mountList({ pickMode: true, selected: new Set([tableKey({ path: 'a.csv', sheet: null }), tableKey({ path: 'b.csv', sheet: null })]) })
    expect(row('a.csv').className).toContain('h-7')
    expect(row('b.csv').className).toContain('h-7')
    expect(q('[role=tree]')!.className).toContain('gap-0.5')
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

  it('多选态（pickMode）：单击 / Enter = 加选 / 取消、不开临时标签；读不了的行照旧开说明', async () => {
    await mountList({ pickMode: true })
    await click(row('a.csv'))
    expect(m.onToggle).toHaveBeenLastCalledWith({ path: 'a.csv', sheet: null })
    expect(onSelect).not.toHaveBeenCalled()
    key(row('b.csv'), 'Enter')
    expect(m.onToggle).toHaveBeenCalledTimes(2)
    expect(onSelect).not.toHaveBeenCalled()
    await click(row('bad.dat'))
    expect(m.onToggle).toHaveBeenCalledTimes(2)
    expect(onSelect).toHaveBeenCalledWith({ path: 'bad.dat', sheet: null })
  })

  it('整批进行中：点行不再加选 / 取消（⌘ 点、空格也一样），点表行 = 开它的临时标签看候选', async () => {
    await mountList({ pickMode: true, running: true })
    await click(row('a.csv'))
    expect(m.onToggle).not.toHaveBeenCalled()
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
    await click(row('b.csv'), { metaKey: true })
    key(row('b.csv'), ' ', { ctrlKey: true })
    expect(m.onToggle).not.toHaveBeenCalled()
  })

  it('开画后的行尾：等着（灰字）/ 转圈 / 已画（普通行，不是卡片）；没画出来 = 深一档的字 + 小感叹号、普通行、原因在 title', async () => {
    const sel = new Set(['a.csv', 'b.csv', 'c.csv', 'sub/d.csv'].map((p) => tableKey({ path: p, sheet: null })))
    const k = (p: string) => tableKey({ path: p, sheet: null })
    const four: AiDataFileEntry[] = [...entries, { path: 'c.csv', size: 1, table: okT(7) }]
    await mount(
      <DataList
        rows={fileRows(four)}
        selected={null}
        overviews={{}}
        onSelect={onSelect}
        multi={{
          ...m,
          pickMode: true,
          running: false,
          selected: sel,
          rowStates: {
            [k('a.csv')]: { state: 'queued' },
            [k('b.csv')]: { state: 'running' },
            [k('c.csv')]: { state: 'done' },
            [k('sub/d.csv')]: { state: 'failed', reason: msg('dataFigureV2.nothing.generic', undefined, 'ai') },
          },
        }}
      />,
    )
    expect(row('a.csv').querySelector('[data-studio-row-state=queued]')!.textContent).toBe('等着')
    expect(row('a.csv').className).toContain('inset-ring') // 等着仍是选中的卡片
    expect(row('b.csv').querySelector('[data-studio-row-state=running] svg')).not.toBeNull()
    expect(row('b.csv').className).toContain('inset-ring')
    expect(row('c.csv').querySelector('[data-studio-row-state=done]')!.textContent).toBe('已画')
    expect(row('c.csv').className).not.toContain('inset-ring')
    const failed = row('d.csv')
    expect(failed.className).not.toContain('inset-ring')
    expect(failed.getAttribute('aria-selected')).toBe('false')
    const f = failed.querySelector('[data-studio-row-state=failed]')!
    expect(f.textContent).toBe('没画出来')
    expect(f.querySelector('svg')).not.toBeNull()
    expect(f.className).toContain('text-ink')
    expect(f.className).not.toContain('text-ink-3')
    expect(f.className).not.toMatch(/danger|red|bg-/)
    expect(f.getAttribute('title')).toBe('这张表没能画出图。')
  })

  it('单击没画成的行 = 开它的临时标签（不是加选 / 取消）；Enter 同', async () => {
    const k = tableKey({ path: 'a.csv', sheet: null })
    await mountList({ pickMode: true, selected: new Set([k]), rowStates: { [k]: { state: 'failed', reason: null } } })
    await click(row('a.csv'))
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
    expect(m.onToggle).not.toHaveBeenCalled()
    onSelect.mockClear()
    key(row('a.csv'), 'Enter')
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
    expect(m.onToggle).not.toHaveBeenCalled()
  })

  it('纯平铺（没有文件夹 / 工作簿）：文件行没有树形箭头的空占位，图标左沿与标题文字左沿对齐；有文件夹 / 工作簿时保持原样', async () => {
    const flat: AiDataFileEntry[] = [
      { path: 'a.csv', size: 1, table: okT(10) },
      { path: 'b.csv', size: 1, table: okT(20) },
    ]
    await mount(<DataList rows={fileRows(flat)} selected={null} overviews={{}} onSelect={onSelect} />)
    const r = row('a.csv')
    const emptyCols = (el: Element) => [...el.children].filter((c) => c.tagName === 'SPAN' && c.className.includes('w-4') && c.children.length === 0)
    expect(emptyCols(r)).toHaveLength(0) // 没有 16px 的空箭头列
    expect((r as HTMLElement).style.paddingLeft).toBe('4px')
    act(() => root.unmount())
    host.remove()
    await mount(<DataList rows={fileRows(entries)} selected={null} overviews={{}} onSelect={onSelect} />)
    expect(emptyCols(row('a.csv'))).toHaveLength(1)
    expect((row('a.csv') as HTMLElement).style.paddingLeft).toBe('8px')
  })

  it('多选态下 Esc 退出（即使还没选任何一份）', async () => {
    await mountList({ pickMode: true })
    row('a.csv').focus()
    expect(key(row('a.csv'), 'Escape').defaultPrevented).toBe(true)
    expect(m.onClear).toHaveBeenCalledTimes(1)
  })

  it('不给 multi：行为与没有这个功能时相同（⌘ 点就是单击，没有「＋」）', async () => {
    await mount(<DataList rows={fileRows(entries)} selected={null} overviews={{}} onSelect={onSelect} />)
    await click(row('a.csv'), { metaKey: true })
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(q('[data-studio-pick-toggle]')).toBeNull()
    expect(q('[role=tree]')!.hasAttribute('aria-multiselectable')).toBe(false)
  })
})

describe('动作区 DataBatchActions', () => {
  const sel = (n: number) => useDataBatchStore.setState({ pickMode: true, selection: Array.from({ length: n }, (_, i) => ({ path: `f${i}.csv`, sheet: null })) })
  const zone = () => q('[data-studio-batch-zone]')!
  const line = () => zone().querySelector('p')!.textContent
  const btns = () => qa<HTMLButtonElement>('[data-studio-batch-zone] button')
  beforeEach(() => useDataBatchStore.getState().clear())

  it('不在多选态：不存在；退出多选态时走完退场过渡（Reveal）后卸载', async () => {
    await mount(<DataBatchActions open={false} />)
    expect(q('[data-studio-batch-zone]')).toBeNull()
    await act(async () => root.render(<TooltipProvider><DataBatchActions open /></TooltipProvider>))
    expect(q('[data-studio-batch-zone]')).not.toBeNull()
    expect(q('[data-reveal]')).not.toBeNull() // 用现有的高度过渡 primitive
    await act(async () => root.render(<TooltipProvider><DataBatchActions open={false} /></TooltipProvider>))
    await act(async () => void (await new Promise((r) => setTimeout(r, 200))))
    expect(q('[data-studio-batch-zone]')).toBeNull()
  })

  it('0 份：一句灰字「点要画的文件」，「各画一张」「合画一张」都禁用；1 份「已选 1 份」仍禁用；≥2 份启用', async () => {
    await mount(<DataBatchActions open />)
    expect(line()).toBe('点要画的文件')
    expect(btns().map((b) => [b.textContent, b.disabled])).toEqual([['各画一张', true], ['合画一张', true]])
    act(() => sel(1))
    expect(line()).toBe('已选 1 份')
    expect(btns().every((b) => b.disabled)).toBe(true)
    act(() => sel(3))
    expect(line()).toBe('已选 3 份')
    expect(btns().every((b) => !b.disabled)).toBe(true)
    // 主按钮 + 次按钮，等宽并排（两列等分的网格）
    expect(zone().querySelector('.grid-cols-2')!.children).toHaveLength(2)
  })

  it('高度全程不变：同一个固定高度的类名，从 0 份、已选、进行中、没画成到停下后都一样；透明底、底部一条细分割线', async () => {
    await mount(<DataBatchActions open />)
    const cls = zone().className
    expect(cls).toContain('h-[4.75rem]')
    expect(cls).toContain('border-b')
    expect(cls).not.toMatch(/(^|\s)bg-/)
    act(() => sel(3))
    expect(zone().className).toBe(cls)
    act(() => useDataBatchStore.setState({ running: true, progress: { done: 1, total: 3 } }))
    expect(zone().className).toBe(cls)
    act(() => useDataBatchStore.setState({ running: false, rows: { a: { state: 'done' }, b: { state: 'failed', reason: null } } }))
    expect(zone().className).toBe(cls)
    // 左右边缘：左 12px 与标题文字对齐、右 6px 与图钉右沿对齐（标题行 pl-3 pr-1.5）
    expect(cls).toContain('pl-3')
    expect(cls).toContain('pr-1.5')
  })

  it('进行中：「正在画 2 / 3」，两个按钮换成一个次按钮「停下」，点它 = stopRun；点过之后禁用', async () => {
    await mount(<DataBatchActions open />)
    act(() => {
      sel(3)
      useDataBatchStore.setState({ running: true, progress: { done: 1, total: 3 } })
    })
    expect(line()).toBe('正在画 2 / 3')
    expect(btns().map((b) => b.textContent)).toEqual(['停下'])
    await act(async () => btns()[0]!.click())
    expect(useDataBatchStore.getState().stopRequested).toBe(true)
    expect(btns()[0]!.disabled).toBe(true)
  })

  it('部分没画成：「1 张已画，1 张没画出来」，按钮是「看已画的」（主，切到素材抽屉）+「再试一次」（次，只重做没成的）', async () => {
    const run = vi.spyOn(useDataBatchStore.getState(), 'run').mockResolvedValue(undefined)
    useDataBatchStore.setState({ run })
    await mount(<DataBatchActions open />)
    act(() => useDataBatchStore.setState({ pickMode: true, rows: { a: { state: 'done' }, b: { state: 'failed', reason: null } } }))
    expect(line()).toBe('1 张已画，1 张没画出来')
    expect(btns().map((b) => b.textContent)).toEqual(['看已画的', '再试一次'])
    await act(async () => btns()[1]!.click())
    expect(run).toHaveBeenCalledWith('failed')
    // 一张都没画成：「看已画的」无处可去，禁用
    act(() => useDataBatchStore.setState({ rows: { b: { state: 'failed', reason: null } } }))
    expect(btns()[0]!.disabled).toBe(true)
  })

  it('写不进文件夹、整批停下：主按钮是「换个文件夹」，「再试一次」= 接着画整个选择', async () => {
    const run = vi.fn(async () => {})
    useDataBatchStore.setState({ run })
    await mount(<DataBatchActions open />)
    act(() => useDataBatchStore.setState({ pickMode: true, stop: 'unwritable', rows: { b: { state: 'failed', reason: null } } }))
    expect(btns().map((b) => b.textContent)).toEqual(['换个文件夹', '再试一次'])
    await act(async () => btns()[1]!.click())
    expect(run).toHaveBeenCalledWith('all')
  })

  it('「各画一张」= run(all)；「合画一张」= merge', async () => {
    const run = vi.fn(async () => {})
    const merge = vi.fn()
    useDataBatchStore.setState({ run, merge })
    await mount(<DataBatchActions open />)
    act(() => sel(2))
    await act(async () => btns()[0]!.click())
    expect(run).toHaveBeenCalledWith('all')
    await act(async () => btns()[1]!.click())
    expect(merge).toHaveBeenCalledTimes(1)
  })

  it('不把文件名 / 列名放进 data-*', async () => {
    await mount(<DataBatchActions open />)
    act(() => sel(2))
    for (const el of zone().querySelectorAll('*')) for (const a of el.getAttributeNames()) if (a.startsWith('data-')) expect(el.getAttribute(a)).not.toMatch(/csv|xlsx/)
  })
})
