/**
 * 数据页（ADR 0178）：抽屉里的文件树、画布标签栏的「数据」临时标签（候选网格 + 数据条）、右栏的这份数据、
 * 各个失败 / 说明、「换个画法」胶囊。文案断言用中文界面（默认语言）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ApiError, aiDataFigureCommit, aiDataFigurePropose, aiDataFilesAll, aiDataOverview, type AiDataCommitResult, type AiDataFileEntry, type AiDataOverview } from '@/lib/api'
import { fileRows } from '@/lib/dataFigure'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { DataList } from './DataList'
import { DataDrawer } from './DataDrawer'
import { DataDraft } from './DataDraft'
import { DataPanel } from './DataPanel'
import { RedrawPill } from './RedrawPill'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { TablePreview } from './TablePreview'
import { useDocumentStore } from '@/store/documentStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))

let root: Root
let host: HTMLDivElement
async function mount(ui: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{ui}</TooltipProvider>)
  })
}
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
})
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)]
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => (el as HTMLElement).click())
}
const flush = async () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

const ov = (over: Partial<AiDataOverview> = {}): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [
    { name: 'day', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false },
    { name: 'group', kind: 'text', numeric: 0, text: 3, empty: 0, nan: 0, id_like: false },
    { name: 'sid', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: true },
  ],
  preview_rows: [{ line: 2, cells: ['0', 'ctrl', '1'] }, { line: 3, cells: ['2', 'drug', '2'] }],
  truncated: false, lead_lines: [], raw_lines: [], ...over,
})

describe('清单 DataList（文件树）', () => {
  const ok = (rows: number, columns = 3) => ({ status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns, rows })
  const entries: AiDataFileEntry[] = [
    { path: 'a.csv', size: 1, table: ok(32) },
    { path: 'sub/g.txt', size: 1, table: { status: 'unsure', code: null, delimiter: 'space', header_row: 2, columns: 2, rows: null } },
    { path: 'sub/b.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null } },
    { path: 'sub/deep/d.csv', size: 1, table: ok(5, 2) },
    { path: 'big.csv', size: 1, table: null },
    { path: 'book.xlsx', size: 1, sheets: [{ name: 'P1', hidden: false, rows: 24, columns: 2, status: 'ok', code: null }] },
  ]
  const mountTree = (over: Partial<React.ComponentProps<typeof DataList>> = {}, e = entries) =>
    mount(<DataList rows={fileRows(e)} selected={null} overviews={{}} onSelect={() => {}} {...over} />)
  const rowsText = () => qa('[data-studio-nav]').map((r) => r.textContent)
  const row = (txt: string) => qa('[data-studio-nav]').find((r) => r.textContent!.includes(txt))!

  it('role=tree；文件夹行是箭头 + 图标 + 名字 + 行尾文件个数，顶层文件夹默认展开；文件行单行、行尾灰字', async () => {
    await mountTree()
    expect(q('[role=tree]')).not.toBeNull()
    const sub = row('sub')
    expect(sub.getAttribute('role')).toBe('treeitem')
    expect(sub.getAttribute('aria-expanded')).toBe('true')
    expect(sub.querySelector('[data-tree-count]')!.textContent).toBe('3') // g.txt / b.dat / deep/d.csv
    expect(row('a.csv').textContent).toContain('32 行')
    expect(row('g.txt').textContent).toContain('没认准')
    expect(row('b.dat').textContent).toContain('读不了')
    expect(row('big.csv').textContent).toContain('没读')
    // 列数不放在行上
    expect(row('a.csv').textContent).not.toContain('3 列')
    // 完整信息进 title
    expect(row('a.csv').getAttribute('title')).toContain('32 行 · 3 列')
    expect(row('g.txt').getAttribute('title')).toContain('没认准，先不用')
  })

  it('子文件夹默认：文件总数少于规则线全展开；多了只展开顶层', async () => {
    await mountTree()
    expect(row('deep').getAttribute('aria-expanded')).toBe('true')
    act(() => root.unmount())
    host.remove()
    const many: AiDataFileEntry[] = Array.from({ length: 40 }, (_, i) => ({ path: `top/in/f${i}.csv`, size: 1, table: ok(1) }))
    await mountTree({}, many)
    expect(row('top').getAttribute('aria-expanded')).toBe('true')
    expect(row('in').getAttribute('aria-expanded')).toBe('false')
    expect(qa('[data-studio-file]').length).toBe(0)
  })

  it('点整行展开 / 收起；展开才出现子项；工作簿同一套交互，子项是工作表、行尾是行数', async () => {
    await mountTree()
    expect(rowsText().join('|')).not.toContain('P1')
    await click(row('book.xlsx'))
    expect(row('P1').textContent).toContain('24 行')
    expect(row('book.xlsx').getAttribute('aria-expanded')).toBe('true')
    await click(row('sub'))
    expect(row('sub').getAttribute('aria-expanded')).toBe('false')
    await click(row('book.xlsx'))
    expect(row('book.xlsx').getAttribute('aria-expanded')).toBe('false')
  })

  it('错峰淡入只给用户刚展开的那一枝（首屏默认展开的不演），延迟走动效令牌、前 8 个以后一起', async () => {
    const many: AiDataFileEntry[] = Array.from({ length: 12 }, (_, i) => ({ path: `d/f${i}.csv`, size: 1, table: ok(1) }))
    await mountTree({}, many)
    expect(qa('.animate-rise-in').length).toBe(0)
    await click(row('d'))
    await click(row('d'))
    const kids = qa('[data-studio-file]')
    expect(kids.length).toBe(12)
    expect(kids.every((k) => k.className.includes('animate-rise-in'))).toBe(true)
    expect(kids[11]!.style.animationDelay).toBe(kids[8]!.style.animationDelay)
    expect(kids[1]!.style.animationDelay).toContain('--duration-exit')
  })

  it('键盘：↑↓ 走行、→ 展开 / 进子项、← 收起 / 回父、Enter 打开预览、空格同 Enter', async () => {
    const onSelect = vi.fn()
    await mountTree({ onSelect })
    const key = async (el: HTMLElement, k: string) => {
      el.focus()
      await act(async () => { el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })) })
    }
    const sub = row('sub')
    await key(sub, 'ArrowLeft')
    expect(row('sub').getAttribute('aria-expanded')).toBe('false')
    await key(row('sub'), 'ArrowRight')
    expect(row('sub').getAttribute('aria-expanded')).toBe('true')
    await key(row('sub'), 'ArrowRight') // 已展开 → 进第一个子节点（文件夹在前：deep）
    expect(document.activeElement).toBe(row('deep'))
    await key(row('deep'), 'ArrowLeft') // 展开着 → 先收起
    await key(row('deep'), 'ArrowLeft') // 已收起 → 回父
    expect(document.activeElement).toBe(row('sub'))
    await key(row('sub'), 'ArrowDown')
    expect(document.activeElement).toBe(qa('[data-studio-nav]')[qa('[data-studio-nav]').indexOf(row('sub')) + 1])
    await key(row('a.csv'), 'Enter')
    expect(onSelect).toHaveBeenLastCalledWith({ path: 'a.csv', sheet: null })
    await key(row('a.csv'), ' ')
    expect(onSelect).toHaveBeenCalledTimes(2)
    await key(row('book.xlsx'), 'Enter')
    expect(row('P1')).toBeTruthy()
  })

  it('选中行 aria-selected、是唯一的 Tab 停靠点；没选中时第一行是', async () => {
    await mountTree({ selected: { path: 'a.csv', sheet: null } })
    expect(row('a.csv').getAttribute('aria-selected')).toBe('true')
    expect(qa('[data-studio-nav][tabindex="0"]').map((e) => e.textContent)).toEqual([row('a.csv').textContent])
    act(() => root.unmount())
    host.remove()
    await mountTree()
    expect(qa('[data-studio-nav][tabindex="0"]').length).toBe(1)
    expect(qa('[data-studio-nav]')[0]!.getAttribute('tabindex')).toBe('0')
  })

  it('单击一行 = 选这一张表；data-* 里没有文件名（诊断轨迹会记 data 属性）', async () => {
    const onSelect = vi.fn()
    await mountTree({ onSelect })
    await click(row('a.csv'))
    expect(onSelect).toHaveBeenCalledWith({ path: 'a.csv', sheet: null })
    await click(row('book.xlsx'))
    await click(row('P1'))
    expect(onSelect).toHaveBeenLastCalledWith({ path: 'book.xlsx', sheet: 'P1' })
    for (const el of qa('[role=tree] *')) for (const a of el.getAttributeNames()) if (a.startsWith('data-')) expect(el.getAttribute(a)).not.toMatch(/\.(csv|txt|dat|xlsx)/)
  })

  it('用户改过表头行、重读成功后，这一行改说重读后的行数', async () => {
    await mountTree({ overviews: { 'sub/g.txt': ov({ path: 'sub/g.txt', rows: 40, columns: 2 }) } })
    expect(row('g.txt').textContent).toContain('40 行')
  })
})

describe('数据条里的表 TablePreview', () => {
  it('按内容自然宽度靠左；数值列右对齐、文字列左对齐；小数补不可见占位不改数字', async () => {
    const o = ov({
      fields: [
        { name: 'day', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false },
        { name: 'group', kind: 'text', numeric: 0, text: 3, empty: 0, nan: 0, id_like: false },
      ],
      preview_rows: [{ line: 2, cells: ['0.38', 'a'] }, { line: 3, cells: ['0.396', 'b'] }, { line: 4, cells: ['5', 'c'] }, { line: 5, cells: ['6', 'd'] }],
    })
    await mount(<TablePreview ov={o} rows={3} onHeaderRow={() => {}} />)
    const table = q('[data-studio-preview]')!
    expect(table.className).not.toContain('w-full')
    expect(qa('thead th > div')[0]!.className).toContain('items-end')
    expect(qa('thead th > div')[1]!.className).toContain('items-start')
    expect(qa('tbody tr')).toHaveLength(3) // 收起只露 3 行
    expect(qa('tbody td').filter((_, i) => i % 2 === 0).map((c) => c.textContent)).toEqual(['0.380', '0.396', '5.000'])
  })

  it('没认准：原文行里猜的那一行有框、行尾「列名」；点别的行 = 按那一行重读；点已选的那行也算确认', async () => {
    const onHeaderRow = vi.fn()
    const raw = [1, 2, 3, 4].map((n) => ({ line: n, text: `row ${n}` }))
    await mount(<TablePreview ov={ov({ status: 'unsure', header_row: 2, raw_lines: raw, fields: [], preview_rows: [] })} rows={3} onHeaderRow={onHeaderRow} />)
    expect(q('label[data-look-line="2"]')!.textContent).toContain('列名')
    expect(q('label[data-look-line="1"]')!.className).toContain('opacity-45') // 猜的那行之前是说明行，灰显
    await click(q('[data-look-line="4"] input'))
    expect(onHeaderRow).toHaveBeenLastCalledWith(4)
    await click(q('[data-look-line="2"] input'))
    expect(onHeaderRow).toHaveBeenLastCalledWith(2)
  })
})

describe('单位行置灰（后端 units_row）', () => {
  it('原始行视图：header_row + 1 那一行用次要文字色，不加标签、不加底色', async () => {
    const raw = [1, 2, 3, 4, 5].map((n) => ({ line: n, text: `row ${n}` }))
    await mount(<TablePreview ov={ov({ status: 'unsure', header_row: 3, raw_lines: raw, fields: [], preview_rows: [] })} rows={3} guessRow={3} unitsRow onHeaderRow={() => {}} />)
    const unit = q('label[data-look-line="4"]')!
    expect(unit.className).toContain('text-ink-3')
    expect(unit.className).not.toMatch(/(^|\s)bg-/)
    expect(unit.textContent).toBe('4row 4')
    expect(q('label[data-look-line="5"]')!.className).not.toContain('text-ink-3')
  })
  it('表格视图：header_row + 1 那一行次要文字色；没有 units_row（缺失 = false）就不灰', async () => {
    const o = ov({ preview_rows: [{ line: 3, cells: ['nm', 'a.u.', 'x'] }, { line: 4, cells: ['400', '0.1', 'y'] }] })
    await mount(<TablePreview ov={o} rows={3} guessRow={2} unitsRow onHeaderRow={() => {}} />)
    expect(qa('tbody tr')[0]!.className).toContain('text-ink-3')
    expect(qa('tbody tr')[1]!.className).not.toContain('text-ink-3')
    act(() => root.unmount())
    host.remove()
    await mount(<TablePreview ov={o} rows={3} guessRow={2} onHeaderRow={() => {}} />)
    expect(qa('tbody tr')[0]!.className).not.toContain('text-ink-3')
  })
})

describe('临时标签 DataDraft + 右栏 DataPanel + 胶囊 RedrawPill（接 store 走一遍）', () => {
  const files = vi.mocked(aiDataFilesAll)
  const overview = vi.mocked(aiDataOverview)
  const propose = vi.mocked(aiDataFigurePropose)
  const commit = vi.mocked(aiDataFigureCommit)
  const items = (n: number) =>
    ['line_band', 'scatter_fit', 'points_mean', 'box_points'].slice(0, n).map((c, i) => ({ id: `i${i + 1}`, title_code: c, why_code: c, recommended: i === 0, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${i + 1}`, thumbnail_status: 'pending' as const }))
  const roles = { x: 'day', y: ['tumor_volume'], group: 'group', facet: null, unit: null, options: { x: ['day'], y: ['tumor_volume', 'day'], group: ['group'] } }
  const cands = (n = 4) => ({ kind: 'candidates' as const, session: 'S1', items: items(n), dropped: [], render: 'lazy' as const, header_row: 1, roles, defaults: [], mixed_cells: [] })
  /** jsdom 不取图：模拟缩略图到了（ok）/ 取不到（!ok，后端 422） */
  const thumb = async (i: number, ok = true) => act(async () => { qa('[data-candidate] img')[i]!.dispatchEvent(new Event(ok ? 'load' : 'error')) })
  const allThumbs = async () => { for (let i = qa('[data-candidate] img').length - 1; i >= 0; i--) await thumb(i) }
  const tbl = (path: string, status: 'ok' | 'unsure' | 'unreadable' = 'ok') => ({
    path,
    size: 1,
    table: status === 'unreadable' ? { status, code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null, preview: [] } : { status, code: null, delimiter: 'comma' as const, header_row: 1, columns: 3, rows: 144 },
  })

  beforeEach(() => {
    for (const f of [files, overview, propose, commit]) f.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
    useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
    files.mockResolvedValue({ files: [tbl('growth.csv'), tbl('run42.dat', 'unreadable'), tbl('names.csv')], truncated: false })
    overview.mockResolvedValue(ov({ rows: 144, columns: 3 }))
  })
  const start = async (ui: React.ReactNode = <><DataDrawer /><DataDraft /><DataPanel /></>) => {
    await act(async () => useDataFigureStore.getState().loadFiles())
    await mount(ui)
    await flush()
  }
  const open = async (i: number) => {
    await click(qa('[data-studio-file]')[i]!)
    await flush()
    await flush()
  }

  it('点文件：临时标签开了就出候选，没有「开始」按钮；每张 = 名字 + 推荐 + 一句说明，整张可点', async () => {
    propose.mockResolvedValue(cands())
    await start()
    expect(q('[data-data-draft]')).toBeNull()
    await open(0)
    expect(propose).toHaveBeenCalledTimes(1)
    expect(propose.mock.calls[0]![0]).toMatchObject({ table: 'growth.csv' })
    expect(q('[data-studio-start]')).toBeNull()
    const tiles = qa('[data-candidate]')
    expect(tiles).toHaveLength(4)
    expect(tiles[0]!.textContent).toContain('均值线 + 误差带')
    expect(tiles[0]!.textContent).toContain('推荐')
    await allThumbs()
    expect(q('[data-draft-lead]')!.textContent).toBe('点一张，放进画布接着改。')
    expect(qa<HTMLImageElement>('[data-candidate] img').map((i) => i.getAttribute('src'))).toEqual(['/t/1?pj=pa', '/t/2?pj=pa', '/t/3?pj=pa', '/t/4?pj=pa'])
    expect(q('[data-data-strip]')).not.toBeNull()
    expect(q('[data-data-panel] h2')!.textContent).toContain('growth.csv')
    expect(q('[data-data-panel-meta]')!.textContent).toBe('144 行 · 3 列')
  })

  it('文字先到：名字 / 推荐 / 说明立刻上屏，图没到的磁贴不可点、上方「正在画…」；到齐后「点一张…」', async () => {
    propose.mockResolvedValue(cands())
    await start()
    await open(0)
    expect(qa('[data-candidate]')).toHaveLength(4)
    expect(qa<HTMLButtonElement>('[data-candidate]').every((b) => b.disabled)).toBe(true)
    expect(q('[data-draft-lead]')!.textContent).toBe('正在画…')
    await thumb(0)
    expect(qa<HTMLButtonElement>('[data-candidate]')[0]!.disabled).toBe(false)
    expect(qa<HTMLButtonElement>('[data-candidate]')[1]!.disabled).toBe(true)
    expect(q('[data-draft-lead]')!.textContent).toBe('正在画…')
    await thumb(1); await thumb(2); await thumb(3)
    expect(q('[data-draft-lead]')!.textContent).toBe('点一张，放进画布接着改。')
  })

  it('取不到图（422）的那张静默去掉，推荐标记挪给剩下的第一张；全部取不到走「没画出来」', async () => {
    propose.mockResolvedValue(cands(3))
    await start()
    await open(0)
    await thumb(0, false)
    expect(qa('[data-candidate]')).toHaveLength(2)
    expect(qa('[data-candidate]')[0]!.textContent).toContain('推荐')
    expect(qa('[data-candidate]')[0]!.textContent).toContain('散点 + 拟合线')
    await thumb(0, false)
    await thumb(0, false)
    expect(q('[data-studio-problem]')!.textContent).toContain('这次没画出来，数据没问题。')
  })

  it('右栏：横轴 / 纵轴 / 分组三个下拉来自 roles；没有可选列的角色不显示；换一项重新出候选', async () => {
    propose.mockResolvedValue({ ...cands(), roles: { ...roles, options: { x: [], y: ['tumor_volume', 'day'], group: ['group'] } } })
    await start()
    await open(0)
    const labels = qa('[data-data-roles] [data-row-label], [data-data-roles] label').map((l) => l.textContent)
    expect(q('[data-data-roles]')!.textContent).toContain('纵轴')
    expect(q('[data-data-roles]')!.textContent).toContain('分组')
    expect(q('[data-data-roles]')!.textContent).not.toContain('横轴')
    expect(labels).not.toContain('横轴')
    propose.mockResolvedValueOnce(cands(2))
    await act(async () => useDataFigureStore.getState().setRole('y', 'day'))
    await flush()
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ answers: { y: 'day' } })
    expect(q('[data-data-roles]')).not.toBeNull() // 重新出候选后下拉仍在
  })

  it('有非数字格：右栏一行「3 格不是数字」+ 下拉（合计）；改成「只用里面的数字」重新出候选', async () => {
    const m = (mode: 'skip' | 'extract') => [{ column: 'value', non_numeric: 2, rows: 36, mode }, { column: 'v2', non_numeric: 1, rows: 36, mode }]
    propose.mockResolvedValueOnce({ ...cands(), mixed_cells: m('skip') }).mockResolvedValueOnce({ ...cands(3), mixed_cells: m('extract') })
    await start()
    await open(0)
    expect(q('[data-data-panel]')!.textContent).toContain('3 格不是数字')
    expect(q('[data-studio-mixed]')).toBeNull() // 弹窗不再出现
    await act(async () => useDataFigureStore.getState().setMixedMode('extract'))
    await flush()
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ answers: { cells: { value: { mode: 'extract' }, v2: { mode: 'extract' } } } })
    expect(qa('[data-candidate]')).toHaveLength(3)
  })

  it('只有说明行、没有单位行（猜不出表头）：不出候选，只一句「点一下每列名字所在的那一行。」，数据条里没有猜的那一行；点一行才出', async () => {
    files.mockResolvedValue({ files: [{ path: 'r.txt', size: 1, table: { status: 'unreadable', code: 'data_ragged', delimiter: null, header_row: null, columns: null, rows: null, preview: [{ line: 1, text: 'row 1' }] } }], truncated: false })
    const raw = [1, 2, 3].map((n) => ({ line: n, text: `row ${n}` }))
    overview.mockResolvedValue(ov({ status: 'unreadable', code: 'data_ragged', header_row: 1, raw_lines: raw, fields: [], preview_rows: [] }))
    propose.mockResolvedValue(cands())
    await start()
    await open(0)
    expect(propose).not.toHaveBeenCalled()
    expect(q('[data-studio-pickrow]')!.textContent).toBe('点一下每列名字所在的那一行。')
    expect(q('[data-data-strip-info]')!.textContent).toBe('点一下每列名字所在的那一行。')
    expect(qa('label[data-checked]')).toHaveLength(0)
    expect(qa('[data-candidate]')).toHaveLength(0)
  })

  it('读不了：一句话、没有数据条，抽屉里该行置灰；全是文字：一句话、不出候选、数据条展开', async () => {
    await start()
    await open(1)
    expect(propose).not.toHaveBeenCalled()
    expect(q('[data-studio-unreadable]')!.textContent).toBe('这个文件不是表格，读不了。')
    expect(q('[data-data-strip]')).toBeNull()
    expect(qa('[data-studio-file]')[1]!.querySelector('[data-tree-label], span')).not.toBeNull()
    overview.mockResolvedValue(ov({ fields: [{ name: 'name', kind: 'text', numeric: 0, text: 3, empty: 0, nan: 0, id_like: false }], preview_rows: [{ line: 2, cells: ['a'] }] }))
    await open(2)
    expect(q('[data-studio-text-only]')!.textContent).toBe('这张表里都是文字，没有能画的数字。')
    expect(propose).not.toHaveBeenCalled()
    expect(q('[data-data-strip]')!.hasAttribute('data-open')).toBe(true)
    expect(qa('[data-candidate]')).toHaveLength(0)
  })

  it('列名没认准：照样按猜的那一行出候选；数据条自动展开成原始行，一句「把第 N 行当作列名」；点别的行重来', async () => {
    files.mockResolvedValue({ files: [tbl('g.txt', 'unsure')], truncated: false })
    const raw = [1, 2, 3, 4].map((n) => ({ line: n, text: `row ${n}` }))
    overview.mockResolvedValue(ov({ status: 'unsure', header_row: 3, raw_lines: raw, fields: [], preview_rows: [] }))
    propose.mockResolvedValue({ ...cands(), header_row: 3 })
    await start()
    await open(0)
    expect(qa('[data-candidate]')).toHaveLength(4)
    expect(q('[data-data-strip]')!.hasAttribute('data-open')).toBe(true)
    expect(q('[data-data-strip-info]')!.textContent).toBe('把第 3 行当作列名。不对就点对的那一行。')
    await click(q('[data-look-line="2"] input'))
    await flush()
    expect(useDataFigureStore.getState().headerRows['g.txt']).toBe(2)
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ header_rows: { 'g.txt': 2 } })
  })

  it('点一张：出图（被点的带扫光、其余淡掉不可点、上方「正在画…」）；成功后临时标签收掉、进编辑器、给「换个画法」胶囊', async () => {
    propose.mockResolvedValue(cands())
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const openScript = vi.fn(async () => {})
    const { configureDataFigureHost } = await import('@/store/dataFigureStore')
    configureDataFigureHost({ openDrawer: () => {}, openScript, projectWritable: () => true, activeCanvasId: () => 'c0', notify: () => {}, dismissAddedNote: () => {} })
    useDocumentStore.setState({ activeCanvasId: 'c0' })
    await start(<><DataDrawer /><DataDraft /><RedrawPill /></>)
    await open(0)
    await allThumbs()
    await click(qa('[data-candidate]')[1]!)
    expect(commit.mock.calls[0]![0]).toMatchObject({ session: 'S1', id: 'i2' })
    expect(qa('[data-candidate]')[1]!.hasAttribute('data-drawing')).toBe(true)
    expect(qa('[data-candidate]')[0]!.className).toContain('opacity-35')
    expect(qa<HTMLButtonElement>('[data-candidate]')[0]!.disabled).toBe(true)
    expect(q('[data-draft-lead]')!.textContent).toBe('正在画…')
    release({ task: { outcome: 'completed' }, script: 'tavotto_figures/g.py', stem: 'g', data_check: 'verified' })
    await flush()
    await flush()
    expect(openScript).toHaveBeenCalledWith('tavotto_figures/g.py', expect.any(Function))
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: false, fresh: { from: 'c0', canvasId: null } })
    expect(q('[data-redraw-pill]')).toBeNull() // 新图还没放上去（激活的画布还是旧的）
    act(() => useDocumentStore.setState({ activeCanvasId: 'c9' }))
    expect(useDataFigureStore.getState().fresh?.canvasId).toBe('c9')
    expect(q('[data-redraw-pill]')!.textContent).toContain('用 growth.csv 画的')
    expect(q('[data-redraw-undo]')).toBeNull() // 没有「加入」那一步在栈顶时不给撤销
    await click(q('[data-redraw-again]'))
    await flush()
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: true, draftActive: true, fresh: null })
    expect(q('[data-redraw-pill]')).toBeNull()
  })

  it('没画出来：「这次没画出来，数据没问题。」+「再试一次」；点了复用同一个请求号', async () => {
    propose.mockResolvedValue(cands())
    commit.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({ task: { outcome: 'completed' }, script: 's.py', stem: 's', data_check: 'verified' })
    await start()
    await open(0)
    await allThumbs()
    await click(qa('[data-candidate]')[0]!)
    await flush()
    expect(q('[data-studio-problem]')!.textContent).toContain('这次没画出来，数据没问题。')
    expect(q('[data-studio-problem] svg')).toBeNull()
    const rid = commit.mock.calls[0]![0].client_request_id
    await click(q('[data-studio-retry]'))
    await flush()
    expect(commit.mock.calls[1]![0].client_request_id).toBe(rid)
  })

  it('同名的图占满（figure_name_exhausted）：不出「再试一次」', async () => {
    propose.mockResolvedValue(cands())
    commit.mockRejectedValueOnce(new ApiError('figure_name_exhausted', 422, { code: 'figure_name_exhausted' }))
    await start()
    await open(0)
    await allThumbs()
    await click(qa('[data-candidate]')[0]!)
    await flush()
    expect(q('[data-studio-problem]')).not.toBeNull()
    expect(q('[data-studio-retry]')).toBeNull()
  })

  it('出图中关掉临时标签只是收起；没成的结局再把它翻出来', async () => {
    propose.mockResolvedValue(cands())
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    await start()
    await open(0)
    await allThumbs()
    await click(qa('[data-candidate]')[0]!)
    act(() => useDataFigureStore.getState().closeDraft())
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: false, drawingId: 'i1' })
    release({ task: { outcome: 'failed' } } as AiDataCommitResult)
    await flush()
    await flush()
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: true, draftActive: true })
    expect(q('[data-studio-problem]')!.textContent).toContain('这次没画出来')
  })

  it('网格里方向键移动、Enter 选；Esc 关临时标签；切到别的画布，临时标签留着但不激活', async () => {
    propose.mockResolvedValue(cands())
    commit.mockReturnValue(new Promise(() => {}))
    await start()
    await open(0)
    const tiles = qa<HTMLButtonElement>('[data-candidate]')
    tiles[0]!.focus()
    const key = async (el: HTMLElement, k: string) => act(async () => { el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })) })
    await key(tiles[0]!, 'ArrowRight')
    expect(document.activeElement).toBe(tiles[1])
    await key(tiles[1]!, 'ArrowDown')
    expect(document.activeElement).toBe(tiles[3])
    await key(tiles[3]!, 'ArrowLeft')
    expect(document.activeElement).toBe(tiles[2])
    act(() => useDocumentStore.setState({ activeCanvasId: 'other' }))
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: true, draftActive: false })
    expect(q('[data-data-draft]')).toBeNull()
    act(() => useDataFigureStore.getState().activateDraft())
    await flush()
    await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })) })
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: false, selected: null })
  })

  it('抽屉里 ↑↓ 换文件，临时标签跟着换；Enter 把焦点交给候选网格', async () => {
    propose.mockResolvedValue(cands())
    await start()
    await open(0)
    const rows = qa<HTMLElement>('[data-studio-file]')
    rows[0]!.focus()
    await act(async () => { rows[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })) })
    await flush()
    expect(useDataFigureStore.getState().selected).toEqual({ path: 'run42.dat', sheet: null })
    qa<HTMLElement>('[data-studio-file]')[0]!.focus()
    await flush()
    await flush()
    expect(useDataFigureStore.getState().selected).toEqual({ path: 'growth.csv', sheet: null })
    await allThumbs()
    await act(async () => { qa<HTMLElement>('[data-studio-file]')[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) })
    expect(document.activeElement).toBe(qa('[data-candidate]')[0])
  })

  it('数据条：默认表头 + 3 行，点「展开」到 20 行、「收起」回来', async () => {
    propose.mockResolvedValue(cands())
    const rows = Array.from({ length: 20 }, (_, i) => ({ line: i + 2, cells: [String(i), 'g', '1.5'] }))
    overview.mockResolvedValue(ov({ preview_rows: rows, rows: 144, columns: 3, truncated: true }))
    await start()
    await open(0)
    expect(qa('[data-data-strip] tbody tr')).toHaveLength(3)
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 3 行，共 144 行 · 3 列')
    await click(q('[data-data-strip-toggle]'))
    expect(qa('[data-data-strip] tbody tr')).toHaveLength(20)
    expect(q('[data-data-strip-toggle]')!.textContent).toBe('收起')
    await click(q('[data-data-strip-toggle]'))
    expect(qa('[data-data-strip] tbody tr')).toHaveLength(3)
  })

  it('抽屉里多选：⌘ 点两份 → 底部出现「各画一张」、进入多选态；多选态下单击是加选 / 取消；点「选多份」退出（清选择）后单击才开临时标签', async () => {
    propose.mockResolvedValue(cands())
    useDataBatchStore.getState().clear()
    await start(<><DataDrawer /><DataDraft /></>)
    const meta = (i: number) => qa('[data-studio-file]')[i]!
    const cmdClick = (el: Element) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true })))
    const btn = () => q<HTMLButtonElement>('[data-studio-pick-mode]')!
    expect(btn().textContent).toBe('多选')
    await cmdClick(meta(0))
    expect(btn().textContent).toBe('取消') // 手势选中第一份 = 进入多选态，按钮同步成「取消」
    expect(q<HTMLButtonElement>('[data-studio-batch-run]')!.disabled).toBe(true) // 1 份：两个按钮都禁用
    await cmdClick(meta(2))
    expect(q<HTMLButtonElement>('[data-studio-batch-run]')!.disabled).toBe(false)
    expect(q('[data-studio-batch-run]')!.textContent).toBe('分别作图')
    expect(q('[data-studio-batch-merge]')!.textContent).toBe('合并作图')
    expect(q('[data-data-draft]')).toBeNull() // 多选不开临时标签
    await click(meta(0)) // 多选态下普通单击 = 取消这一份，不开临时标签
    await flush()
    expect(useDataBatchStore.getState().selection).toHaveLength(1)
    expect(useDataFigureStore.getState().draftOpen).toBe(false)
    await click(btn()) // 点「取消」：退出并清掉选择
    expect(btn().textContent).toBe('多选')
    expect(useDataBatchStore.getState()).toMatchObject({ pickMode: false, selection: [] })
    await click(meta(0))
    await flush()
    expect(useDataFigureStore.getState().draftOpen).toBe(true)
  })

  it('「选多份」按钮：至少 2 份表才出现；点它进入多选态（文字变「取消」、出现动作区）；批量进行中禁用；多选态下读不了的行单击不进选择', async () => {
    propose.mockResolvedValue(cands())
    useDataBatchStore.getState().clear()
    files.mockResolvedValue({ files: [tbl('growth.csv'), tbl('run42.dat', 'unreadable')], truncated: false })
    await start(<><DataDrawer /><DataDraft /></>)
    expect(q('[data-studio-pick-mode]')).toBeNull() // 只有 1 份表
    useDataFigureStore.getState().clear()
    files.mockResolvedValue({ files: [tbl('growth.csv'), tbl('run42.dat', 'unreadable'), tbl('names.csv')], truncated: false })
    await act(async () => useDataFigureStore.getState().loadFiles())
    await flush()
    const btn = () => q<HTMLButtonElement>('[data-studio-pick-mode]')!
    expect(btn().textContent).toBe('多选')
    await click(btn())
    expect(btn().textContent).toBe('取消')
    expect(q('[data-studio-batch-zone]')!.textContent).toContain('未选择')
    // 行尾灰字行数在多选态下仍在（不再有「＋」）
    expect(qa('[data-studio-file]')[0]!.textContent).toMatch(/行/)
    expect(q('[data-studio-pick-toggle]')).toBeNull()
    await click(qa('[data-studio-file]')[1]!) // 读不了的行
    expect(useDataBatchStore.getState().selection).toHaveLength(0)
    expect(useDataBatchStore.getState().pickMode).toBe(true)
    act(() => useDataBatchStore.setState({ running: true }))
    expect(btn().disabled).toBe(true)
    act(() => useDataBatchStore.setState({ running: false }))
    // 切项目 / clear：复位
    act(() => useDataBatchStore.getState().clear())
    expect(btn().textContent).toBe('多选')
    await act(async () => void (await new Promise((r) => setTimeout(r, 200)))) // 动作区走完退场过渡
    expect(q('[data-studio-batch-zone]')).toBeNull()
  })

  it('「选多份」：焦点在按钮上按 Esc 退出多选态；清单变到少于 2 份能画的表时自动退出并清选择', async () => {
    propose.mockResolvedValue(cands())
    useDataBatchStore.getState().clear()
    await start(<><DataDrawer /><DataDraft /></>)
    const btn = () => q<HTMLButtonElement>('[data-studio-pick-mode]')!
    await click(btn())
    await click(qa('[data-studio-file]')[0]!)
    expect(useDataBatchStore.getState()).toMatchObject({ pickMode: true })
    btn().focus()
    act(() => void btn().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })))
    expect(useDataBatchStore.getState()).toMatchObject({ pickMode: false, selection: [] })
    // 清单变化：只剩 1 份能画的表
    await click(btn())
    await click(qa('[data-studio-file]')[0]!)
    files.mockResolvedValue({ files: [tbl('growth.csv'), tbl('run42.dat', 'unreadable')], truncated: false })
    await act(async () => useDataFigureStore.getState().loadFiles())
    await flush()
    expect(q('[data-studio-pick-mode]')).toBeNull()
    expect(useDataBatchStore.getState()).toMatchObject({ pickMode: false, selection: [] })
  })

  const groupTwo = async () => {
    await start(<><DataDrawer /><DataDraft /><DataPanel /></>)
    const cmd = (el: Element) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true })))
    await cmd(qa('[data-studio-file]')[0]!)
    await cmd(qa('[data-studio-file]')[2]!)
    await click(q('[data-studio-batch-merge]'))
    await flush()
    await flush()
  }

  it('合画一张：点「合画一张」退出多选态、开临时标签（标签名「2 份数据」），请求带 tables，候选与单份同形；右栏标题也是「2 份数据」、角色下拉照常；数据条显示第一份', async () => {
    propose.mockResolvedValue(cands())
    await groupTwo()
    expect(useDataBatchStore.getState()).toMatchObject({ pickMode: false, selection: [] })
    expect(propose.mock.calls.at(-1)![0]).toEqual({ tables: [{ path: 'growth.csv' }, { path: 'names.csv' }], render: 'lazy' })
    expect(qa('[data-candidate]')).toHaveLength(4)
    expect(q('[data-data-draft]')!.getAttribute('aria-label')).toBe('2 个文件')
    expect(q('[data-data-panel] h2')!.textContent).toBe('2 个文件')
    expect(q('[data-data-panel-meta]')).toBeNull() // 第一份的行列数不代表整组
    expect(q('[data-data-roles]')).not.toBeNull()
    expect(q('[data-data-strip]')).not.toBeNull()
  })

  it('合画：点一张 → 和单份一样提交；右栏改角色仍带 tables 重来', async () => {
    propose.mockResolvedValue(cands())
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/g.py', stem: 'g', data_check: 'verified' })
    await groupTwo()
    await allThumbs()
    await click(qa('[data-candidate]')[0]!)
    await flush()
    expect(commit).toHaveBeenCalledWith(expect.objectContaining({ session: 'S1', id: 'i1' }), expect.anything())
  })

  it('tables_columns_differ：临时标签里一句话 + 主按钮「各画一张」；点它回到抽屉、恢复选择并直接开始各画一张', async () => {
    propose.mockRejectedValueOnce(new ApiError('tables_columns_differ', 400, { code: 'tables_columns_differ' }))
    await groupTwo()
    const p = q('[data-studio-group-problem]')!
    expect(p.textContent).toContain('列名不一致，无法合并作图。')
    expect(p.querySelector('button')!.textContent).toBe('分别作图')
    expect(q('[data-studio-retry]')).toBeNull() // 再试一次不是出路
    propose.mockImplementation(async (b) => ({ ...cands(1), session: `S-${b.table}` }))
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/x.py', stem: 'x', data_check: 'verified' })
    await click(p.querySelector('button'))
    await flush()
    expect(q('[data-data-draft]')).toBeNull()
    for (let i = 0; i < 20 && useDataBatchStore.getState().running; i++) await flush()
    expect(propose.mock.calls.slice(-2).map((c) => c[0].table)).toEqual(['growth.csv', 'names.csv'])
  })

  it('tables_limit_exceeded：后端那句话 + 同一个出路「各画一张」', async () => {
    propose.mockRejectedValueOnce(new ApiError('tables_limit_exceeded', 400, { code: 'tables_limit_exceeded', params: { max: 8 } }))
    await groupTwo()
    const p = q('[data-studio-group-problem="mergeFailed"]')!
    expect(p.textContent).toContain('一次最多合并 8 个文件。')
    expect(p.querySelector('button')!.textContent).toBe('分别作图')
  })

  const tabs = () => qa<HTMLElement>('[data-data-strip-tabs] [role=radio]')

  it('合画的数据条：标题行里「数据」后面一排文件标签（没有 sources 就按路径取名去扩展名），默认第一个；单份流程没有这排标签', async () => {
    propose.mockResolvedValue(cands())
    overview.mockImplementation(async ({ path }) => ov({ path, rows: path === 'names.csv' ? 7 : 144, columns: 3 }))
    await groupTwo()
    expect(tabs().map((b) => b.textContent)).toEqual(['growth', 'names'])
    expect(tabs()[0]!.getAttribute('aria-checked')).toBe('true')
    // 标题行不撑高、文件名不进 data-*
    expect(q('[data-data-strip-tabs]')!.className).toContain('overflow-x-auto')
    for (const b of tabs()) for (const a of b.getAttributeNames()) if (a.startsWith('data-')) expect(b.getAttribute(a)).not.toMatch(/growth|names|csv/)
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 144 行 · 3 列')
    // 单份流程
    act(() => useDataFigureStore.getState().closeDraft())
    await click(qa('[data-studio-file]')[0]!)
    await flush()
    await flush()
    expect(q('[data-data-strip]')).not.toBeNull()
    expect(q('[data-data-strip-tabs]')).toBeNull()
  })

  it('合画的数据条：标签用响应的 sources[].label；点哪个，表与「前 N 行，共 M 行」就是哪个文件的；按需读，读过的不重读', async () => {
    propose.mockResolvedValue({ ...cands(), sources: [{ path: 'growth.csv', sheet: null, label: 'growth', header_row: 1, units_row: false }, { path: 'names.csv', sheet: null, label: 'names-x', header_row: 1, units_row: false }] })
    overview.mockImplementation(async ({ path }) => ov({ path, rows: path === 'names.csv' ? 7 : 144, columns: path === 'names.csv' ? 2 : 3 }))
    await groupTwo()
    expect(tabs().map((b) => b.textContent)).toEqual(['growth', 'names-x'])
    const reads = () => overview.mock.calls.map((c) => c[0].path)
    expect(reads()).not.toContain('names.csv') // 没点之前不读第二个
    await click(tabs()[1]!)
    await flush()
    expect(reads()).toContain('names.csv')
    expect(tabs()[1]!.getAttribute('aria-checked')).toBe('true')
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 7 行 · 2 列')
    const n = overview.mock.calls.length
    await click(tabs()[0]!)
    await click(tabs()[1]!)
    await flush()
    expect(overview.mock.calls.length).toBe(n) // 读过的不重读
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 7 行 · 2 列')
  })

  it('合画的数据条：展开的是当前选中的文件；在某个文件的预览里点一行指表头，只影响这个文件，重新出候选时 header_rows 只带它', async () => {
    propose.mockResolvedValue(cands())
    const raw = [1, 2, 3].map((n) => ({ line: n, text: `row ${n}` }))
    overview.mockImplementation(async ({ path }) => (path === 'names.csv' ? ov({ path, status: 'unsure', header_row: 2, raw_lines: raw, fields: [], preview_rows: [] }) : ov({ path })))
    await groupTwo()
    await click(tabs()[1]!)
    await flush()
    propose.mockClear()
    await click(q('label[data-look-line="3"] input'))
    await flush()
    await flush()
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ tables: [{ path: 'growth.csv' }, { path: 'names.csv' }], header_rows: { 'names.csv': 3 } })
    expect(propose.mock.calls.at(-1)![0].header_rows).not.toHaveProperty('growth.csv')
    expect(tabs()[1]!.getAttribute('aria-checked')).toBe('true') // 重新出候选后仍停在这个文件
  })

  it('合画的成对候选：响应里 items[].variant 不同的两张同名候选，磁贴上各有一句小字（按文件 +「列」分组 / 只按文件分组）；列名只当文本、不进 data-*；最多 8 张照常排', async () => {
    const col = '组别<b>x</b>'
    const eight = Array.from({ length: 8 }, (_, i) => ({
      ...items(1)[0]!,
      id: `v${i}`,
      recommended: i === 0,
      thumbnail_url: `/t/v${i}`,
      variant: i % 2 === 0 ? { kind: 'split' as const, column: col } : { kind: 'by_file' as const },
    }))
    propose.mockResolvedValue({ ...cands(), items: eight })
    await groupTwo()
    const tiles = qa('[data-candidate]')
    expect(tiles).toHaveLength(8)
    expect(tiles[0]!.querySelector('[data-candidate-variant]')!.textContent).toBe(`按文件 +「${col}」分组`)
    expect(tiles[1]!.querySelector('[data-candidate-variant]')!.textContent).toBe('只按文件分组')
    expect(tiles[0]!.querySelector('b')).toBeNull() // 文本渲染，不当 HTML
    for (const el of document.querySelectorAll('[data-candidate-grid] *')) for (const a of el.getAttributeNames()) if (a.startsWith('data-')) expect(el.getAttribute(a)).not.toContain('组别')
    expect(tiles[0]!.textContent).toContain('推荐') // 推荐小标仍在第一张
    // 单份响应没有 variant：磁贴上没有这一行
    act(() => useDataFigureStore.getState().closeDraft())
    propose.mockResolvedValue(cands())
    await click(qa('[data-studio-file]')[0]!)
    await flush()
    await flush()
    expect(qa('[data-candidate-variant]')).toHaveLength(0)
  })

  it('合并标签的概览新鲜度：看过第二个文件 → 关标签 → 外部改了文件 → 再合并，切到它会重新读并显示新内容；同一标签内来回切不重复请求', async () => {
    propose.mockResolvedValue(cands())
    overview.mockImplementation(async ({ path }) => ov({ path, rows: path === 'names.csv' ? 7 : 144, columns: 3 }))
    await groupTwo()
    const reads = (p: string) => overview.mock.calls.filter((c) => c[0].path === p).length
    await click(tabs()[1]!)
    await flush()
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 7 行 · 3 列')
    const n1 = reads('names.csv')
    await click(tabs()[0]!)
    await click(tabs()[1]!)
    await flush()
    expect(reads('names.csv')).toBe(n1) // 同一标签内来回切不重复
    // 关标签；外部改了 names.csv（现在 99 行）；清单重读之后再合并
    act(() => useDataFigureStore.getState().closeDraft())
    overview.mockImplementation(async ({ path }) => ov({ path, rows: path === 'names.csv' ? 99 : 144, columns: 3 }))
    const cmd = (el: Element) => act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true })))
    await click(q('[data-studio-pick-mode]'))
    await cmd(qa('[data-studio-file]')[0]!)
    await cmd(qa('[data-studio-file]')[2]!)
    await click(q('[data-studio-batch-merge]'))
    await flush()
    await flush()
    await click(tabs()[1]!)
    await flush()
    expect(reads('names.csv')).toBeGreaterThan(n1) // 重新请求了
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 99 行 · 3 列')
  })

  it('合并标签里磁盘事件（invalidateFiles）之后，当前显示的那个文件的概览重读', async () => {
    propose.mockResolvedValue(cands())
    overview.mockImplementation(async ({ path }) => ov({ path, rows: 144, columns: 3 }))
    await groupTwo()
    await click(tabs()[1]!)
    await flush()
    const reads = () => overview.mock.calls.filter((c) => c[0].path === 'names.csv').length
    const before = reads()
    overview.mockImplementation(async ({ path }) => ov({ path, rows: 5, columns: 3 }))
    await act(async () => useDataFigureStore.getState().invalidateFiles())
    await flush()
    await flush()
    expect(reads()).toBeGreaterThan(before)
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 2 行，共 5 行 · 3 列')
  })

  it('tables_labels_collide：同一条出路——后端那一句 + 「分别作图」，不给「再试一次」', async () => {
    propose.mockRejectedValueOnce(new ApiError('tables_labels_collide', 400, { code: 'tables_labels_collide' }))
    await groupTwo()
    const p = q('[data-studio-group-problem="mergeFailed"]')!
    expect(p.textContent).toContain('这几个文件的名字分不开，没法合并作图。')
    expect(p.querySelector('button')!.textContent).toBe('分别作图')
    expect(q('[data-studio-retry]')).toBeNull()
  })

  it('合画里某份表不在了（200 nothing + table_not_found）：走 nothing 的话，也给「各画一张」这个出路', async () => {
    propose.mockResolvedValueOnce({ kind: 'nothing', reasons: ['table_not_found'] })
    await groupTwo()
    const p = q('[data-studio-group-problem]')!
    expect(p.textContent).toContain('找不到你选的那张表。')
    expect(p.querySelector('button')!.textContent).toBe('分别作图')
  })

  it('动作区：多选态下在标题行正下方、清单上方；高度类名从「已选 0 份」到进行中、没画成全程不变；退出后消失', async () => {
    propose.mockResolvedValue(cands())
    useDataBatchStore.getState().clear()
    await start(<><DataDrawer /><DataDraft /></>)
    expect(q('[data-studio-batch-zone]')).toBeNull()
    await click(q('[data-studio-pick-mode]'))
    const zone = () => q('[data-studio-batch-zone]')!
    const tree = q('[role=tree]')!
    // 在清单之前、不在清单里、不是浮层
    expect(zone().compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(tree.contains(zone())).toBe(false)
    expect(zone().className).not.toContain('absolute')
    // 固定高度：同一个类名贯穿所有状态（jsdom 没有排版，真浏览器里的行位置另用截图脚本量）
    const cls = zone().className
    const lines = () => zone().querySelector('p')!.textContent
    expect(lines()).toBe('未选择')
    await click(qa('[data-studio-file]')[0]!)
    await click(qa('[data-studio-file]')[2]!)
    expect(lines()).toBe('已选 2 个')
    act(() => useDataBatchStore.setState({ running: true, progress: { done: 1, total: 2 } }))
    expect(lines()).toBe('作图中 2/2')
    act(() => useDataBatchStore.setState({ running: false, rows: { k: { state: 'done' }, j: { state: 'failed', reason: null } } }))
    expect(lines()).toBe('已完成 1 张，1 张失败')
    expect(zone().className).toBe(cls)
    act(() => useDataBatchStore.getState().clear())
    await act(async () => void (await new Promise((r) => setTimeout(r, 200))))
    expect(q('[data-studio-batch-zone]')).toBeNull()
  })
})
