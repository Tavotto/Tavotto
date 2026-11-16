/**
 * 数据页（ADR 0178）的各屏：清单每种状态怎么说、预览 / 没认准 / 读不了、选择题、混了文字弹窗（两种选项、多列、右列跟着变、没有「我来说明」）、
 * 选画法（默认推荐、单选、真缩略图地址）、各个失败出口、以及整页接 store 走一遍。文案断言用中文界面（默认语言）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ApiError, aiDataFigureCommit, aiDataFigurePropose, aiDataFilesAll, aiDataOverview, type AiDataFileEntry, type AiDataMixedColumn, type AiDataOption, type AiDataOverview } from '@/lib/api'
import { fileRows } from '@/lib/dataFigure'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { AskScene } from './AskScene'
import { DataList } from './DataList'
import { DataDrawer } from './DataDrawer'
import { DataOverlay } from './DataOverlay'
import { MixedCellsDialog } from './MixedCellsDialog'
import { PickScene } from './PickScene'
import { DrawnScene, NothingScene, ProblemScene, WorkingScene } from './ResultScenes'
import { TablePreview } from './TablePreview'

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

describe('预览 TablePreview', () => {
  it('认得准：列名 + 每列类型 + 前几行；编号列写「编号」；超过只显示一部分时说明', async () => {
    await mount(<TablePreview ov={ov({ truncated: true, rows: 90 })} loading={false} onHeaderRow={() => {}} />)
    const t = q('[data-studio-preview]')!.textContent!
    expect(t).toContain('day')
    expect(t).toContain('数值')
    expect(t).toContain('文字')
    expect(t).toContain('编号')
    expect(t).toContain('显示前 2 行，共 90 行')
  })

  it('开头被跳过的说明行单独灰显并写明跳过了几行', async () => {
    await mount(<TablePreview ov={ov({ lead_lines: [{ line: 1, text: '# Keithley' }, { line: 2, text: '# date' }] })} loading={false} onHeaderRow={() => {}} />)
    expect(q('[data-studio-skipped]')!.textContent).toContain('# Keithley')
    expect(q('[data-studio-skipped]')!.textContent).toContain('开头 2 行是说明，已跳过。')
  })

  it('没认准：原文行里猜的那一行高亮并带「当作每列的名字」；点别的行 = 按那一行重读；点已选的那行也算确认', async () => {
    const onHeaderRow = vi.fn()
    const raw = [1, 2, 3, 4].map((n) => ({ line: n, text: `row ${n}` }))
    await mount(<TablePreview ov={ov({ status: 'unsure', header_row: 2, raw_lines: raw, fields: [], preview_rows: [] })} loading={false} onHeaderRow={onHeaderRow} />)
    expect(q('[data-header]')!.getAttribute('data-look-line')).toBe('2')
    expect(q('[data-studio-header-tag]')!.textContent).toBe('当作每列的名字')
    await click(q('[data-look-line="4"] input'))
    expect(onHeaderRow).toHaveBeenLastCalledWith(4)
    await click(q('[data-look-line="2"] input'))
    expect(onHeaderRow).toHaveBeenLastCalledWith(2)
  })

  it('读不了：一句话（后端稳定码翻出的原因），没有表', async () => {
    await mount(<TablePreview ov={ov({ status: 'unreadable', code: 'data_binary', fields: [], preview_rows: [] })} loading={false} onHeaderRow={() => {}} />)
    expect(q('[data-studio-unreadable]')!.textContent).toContain('读不了')
    expect(q('table')).toBeNull()
  })
})

describe('选择题 AskScene', () => {
  const opts: AiDataOption[] = [
    { code: 'column', params: { column: 'y', kind: 'numeric' }, answer: { y: 'y' } },
    { code: 'column', params: { column: 'z', kind: 'numeric' }, answer: { y: 'z' } },
    { code: 'describe', params: {}, answer: {}, free_text: true },
  ]
  it('一屏一道题：题目来自后端的码；选项点一下就是回答；没有文字输入的选项；「不确定」与返回都在', async () => {
    const onAnswer = vi.fn()
    const onBack = vi.fn()
    const unsure = { code: 'unsure', params: {}, answer: { y: 'unsure' } }
    await mount(<AskScene code="pick_value" options={opts} unsure={unsure} onAnswer={onAnswer} onBack={onBack} />)
    expect(q('[data-studio-ask] h2')!.textContent).toBe('要画的是哪一列的数值？')
    expect(qa('[data-studio-choice]')).toHaveLength(2)
    await click(qa('[data-studio-choice]')[1]!)
    expect(onAnswer).toHaveBeenCalledWith(opts[1])
    await click(q('[data-studio-unsure]'))
    expect(onAnswer).toHaveBeenLastCalledWith(unsure)
    await click(q('[data-studio-back]'))
    expect(onBack).toHaveBeenCalled()
  })

  it('不认识的题 / 选项码：走一句通用的话，不显示内部码', async () => {
    await mount(<AskScene code="pick_future" options={[{ code: 'brand_new', params: {}, answer: { x: '1' } }]} unsure={null} onAnswer={() => {}} onBack={() => {}} />)
    expect(q('[data-studio-ask]')!.textContent).not.toContain('pick_future')
    expect(q('[data-studio-ask]')!.textContent).not.toContain('brand_new')
  })
})

describe('混了文字的弹窗 MixedCellsDialog', () => {
  const col = (name: string): AiDataMixedColumn => ({
    column: name,
    non_numeric: 3,
    rows: 36,
    more: 0,
    preview: [
      { row: 17, text: '<0.1', others: { group: 'treated' }, extract: 0.1 },
      { row: 31, text: 'n/a', others: { group: 'washout' }, extract: null },
    ],
    options: [
      { code: 'skip', params: {}, answer: { cells: { [name]: { mode: 'skip' } } } },
      { code: 'extract', params: {}, answer: { cells: { [name]: { mode: 'extract' } } } },
      { code: 'describe', params: {}, answer: { cells: { [name]: { mode: 'describe' } } }, free_text: true },
    ],
  })
  const uses = () => qa('[data-dirty-use]').map((e) => e.textContent)

  it('默认「不画这 N 行」；右列「画图时用」跟着选项变（取不出数字的仍是不画）；左下角一句：原文件不会改', async () => {
    await mount(<MixedCellsDialog columns={[col('value')]} onContinue={() => {}} onCancel={() => {}} />)
    expect(document.body.textContent).toContain('原文件不会改。')
    expect(document.body.textContent).toContain('不画这 3 行')
    expect(document.body.textContent).toContain('只用里面的数字')
    expect(document.body.textContent).not.toContain('我来说明')
    expect(qa('input[type=radio]')).toHaveLength(2)
    expect(uses()).toEqual(['不画', '不画'])
    await click(qa('input[type=radio]')[1]!)
    expect(uses()).toEqual(['0.1', '不画'])
    await click(qa('input[type=radio]')[0]!)
    expect(uses()).toEqual(['不画', '不画'])
  })

  it('后端一次问多列：每列各自选，继续时每列各交一个回答', async () => {
    const onContinue = vi.fn()
    await mount(<MixedCellsDialog columns={[col('c1'), col('c2')]} onContinue={onContinue} onCancel={() => {}} />)
    expect(qa('[data-studio-mixed-column]')).toHaveLength(2)
    await click(qa('[data-studio-mixed-column="1"] input[type=radio]')[1]!)
    await click(q('[data-studio-mixed-continue]'))
    const picks = onContinue.mock.calls[0]![0] as { column: string; option: { answer: unknown } }[]
    expect(picks.map((p) => p.column)).toEqual(['c1', 'c2'])
    expect(picks[0]!.option.answer).toEqual({ cells: { c1: { mode: 'skip' } } })
    expect(picks[1]!.option.answer).toEqual({ cells: { c2: { mode: 'extract' } } })
  })

  it('取消 = 不回答（交给调用方回到浏览）', async () => {
    const onCancel = vi.fn()
    await mount(<MixedCellsDialog columns={[col('value')]} onContinue={() => {}} onCancel={onCancel} />)
    await click([...document.querySelectorAll('button')].find((b) => b.textContent === '取消')!)
    expect(onCancel).toHaveBeenCalled()
  })
})

describe('选画法 PickScene', () => {
  const items = [
    { id: 'i1', title_code: 'line_band', why_code: 'line_band', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', thumb: '/t/1?pj=pa' },
    { id: 'i2', title_code: 'scatter', why_code: 'scatter', recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: '/t/2', thumb: '/t/2?pj=pa' },
    { id: 'i3', title_code: 'brand_new_code', why_code: 'brand_new_code', recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: '/t/3', thumb: '/t/3?pj=pa' },
  ]
  it('标题 + 来源小标签 + 行标签「画法」+ 真缩略图（带项目标识）+ 推荐默认选中 + 每张一句话；码不认识用通用名', async () => {
    const onChoose = vi.fn()
    await mount(<PickScene items={items} selectedId="i1" source="growth.csv" onChoose={onChoose} onDraw={() => {}} onBack={() => {}} />)
    const t = q('[data-studio-pick]')!.textContent!
    expect(t).toContain('这份数据可以这样画')
    expect(q('[data-studio-source]')!.textContent).toBe('growth.csv')
    expect(t).toContain('画法')
    expect(t).toContain('均值线 + 误差带')
    expect(t).toContain('推荐')
    expect(t).not.toContain('brand_new_code')
    expect(qa<HTMLImageElement>('img').map((i) => i.getAttribute('src'))).toEqual(['/t/1?pj=pa', '/t/2?pj=pa', '/t/3?pj=pa'])
    expect(qa<HTMLInputElement>('input[type=radio]').map((r) => r.checked)).toEqual([true, false, false])
    await click(qa('input[type=radio]')[1]!)
    expect(onChoose).toHaveBeenCalledWith('i2')
  })
  it('没有配色行（本 PR 不做）', async () => {
    await mount(<PickScene items={items} selectedId="i1" source="a.csv" onChoose={() => {}} onDraw={() => {}} onBack={() => {}} />)
    expect(q('[data-studio-pick]')!.textContent).not.toContain('配色')
  })
})

describe('结果与失败出口', () => {
  it('等待态是不定进度：没有 aria-valuenow，不装百分比', async () => {
    await mount(<WorkingScene kind="drawing" />)
    expect(q('[data-studio-working]')!.textContent).toContain('正在画')
    expect(q('[role=progressbar]')?.getAttribute('aria-valuenow') ?? null).toBeNull()
  })

  it('nothing：按原因码说一句人话 + 换一张表；渲染失败的原因码也认；不认识的码走通用句', async () => {
    const onBack = vi.fn()
    await mount(<NothingScene reasons={['no_numeric_column']} onBack={onBack} />)
    expect(q('[data-studio-nothing]')!.textContent).toContain('这张表里没有数值列。')
    await click(q('[data-studio-back]'))
    expect(onBack).toHaveBeenCalled()
    act(() => root.unmount())
    host.remove()
    await mount(<NothingScene reasons={['candidate_render_failed']} onBack={() => {}} />)
    expect(q('[data-studio-nothing]')!.textContent).toContain('没能画出来')
    act(() => root.unmount())
    host.remove()
    await mount(<NothingScene reasons={['some_future_code']} onBack={() => {}} />)
    expect(q('[data-studio-nothing]')!.textContent).not.toContain('some_future_code')
    expect(q('[data-studio-nothing]')!.textContent).toContain('这张表没能画出图')
  })

  it('画好但有话要说：哪几行没画出来 + 数据没核对（不写成已核对）+ 打开这张图', async () => {
    const onOpen = vi.fn()
    await mount(<DrawnScene notes={{ unplotted: { count: 12, rows: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12] }, check: 'unverifiable' }} onOpen={onOpen} />)
    const t = q('[data-studio-drawn]')!.textContent!
    expect(t).toContain('有 12 行')
    expect(t).toContain('第 3、4、5、6、7、8、9、10 行 等')
    expect(t).toContain('没法逐项核对')
    expect(t).not.toContain('逐项对上')
    await click(q('[data-studio-primary]'))
    expect(onOpen).toHaveBeenCalled()
  })

  it('每种没成的结局都至少有一个能走的出口', async () => {
    const cb = { onRetry: vi.fn(), onBack: vi.fn(), onChooseFolder: vi.fn() }
    await mount(<ProblemScene problem={{ kind: 'commit', error: null, retryable: true }} {...cb} />)
    await click(q('[data-studio-retry]'))
    expect(cb.onRetry).toHaveBeenCalled()
    await click(q('[data-studio-back]'))
    expect(cb.onBack).toHaveBeenCalled()
    act(() => root.unmount())
    host.remove()
    await mount(<ProblemScene problem={{ kind: 'unwritable', error: { key: 'dataFigureV2.problem.unwritable', ns: 'ai' } }} {...cb} />)
    expect(q('[data-studio-problem]')!.textContent).toContain('写不进')
    await click(q('[data-studio-primary]'))
    expect(cb.onChooseFolder).toHaveBeenCalled()
    act(() => root.unmount())
    host.remove()
    await mount(<ProblemScene problem={{ kind: 'mismatch', script: 'tavotto_figures/a.py' }} {...cb} />)
    expect(q('[data-studio-problem]')!.textContent).toContain('对不上')
    await click(q('[data-studio-primary]'))
    expect(cb.onBack).toHaveBeenCalledTimes(2)
  })
})

describe('抽屉 + 浮层接 store 走一遍', () => {
  const files = vi.mocked(aiDataFilesAll)
  const overview = vi.mocked(aiDataOverview)
  const propose = vi.mocked(aiDataFigurePropose)
  const commit = vi.mocked(aiDataFigureCommit)
  const prepOpen = vi.fn(async (..._a: unknown[]) => {})

  beforeEach(() => {
    for (const f of [files, overview, propose, commit, prepOpen]) f.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
    useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
    useProjectPreparationStore.setState({ open: prepOpen })
    files.mockResolvedValue({
      files: [
        { path: 'a.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } },
        { path: 'g.txt', size: 1, table: { status: 'unsure', code: null, delimiter: 'space', header_row: 2, columns: 2, rows: null } },
        { path: 'b.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null } },
      ],
      truncated: false,
    })
  })

  const start = async () => {
    await act(async () => useDataFigureStore.getState().loadFiles())
    await mount(<><DataDrawer /><DataOverlay narrow={false} /></>)
    await flush()
  }
  const open = async (i: number) => {
    await click(qa('[data-studio-file]')[i]!)
    await flush()
  }

  it('抽屉：清单来自后端；没点文件时没有浮层；点一个文件才出浮层（标题就是文件名）', async () => {
    await start()
    expect(files).toHaveBeenCalledWith('pa')
    expect(qa('[data-studio-file]')).toHaveLength(3)
    expect(q('[data-data-overlay]')).toBeNull()
    overview.mockResolvedValue(ov())
    await open(0)
    expect(q('[data-data-overlay]')).not.toBeNull()
    expect(q('[data-data-overlay] h2')!.textContent).toContain('a.csv')
    // 点 × 关；Esc 也关；浮层外按下也关（抽屉里不算）
    await click(q('[data-studio-close]'))
    expect(useDataFigureStore.getState().overlay).toBe(false)
  })

  it('Esc 与点浮层外关浮层；点抽屉里别的文件是换文件不是关', async () => {
    overview.mockResolvedValue(ov())
    await start()
    await open(0)
    await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })) })
    expect(useDataFigureStore.getState().overlay).toBe(false)
    await open(0)
    await act(async () => { document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })) })
    expect(useDataFigureStore.getState().overlay).toBe(false)
    await open(0)
    await open(1)
    expect(useDataFigureStore.getState().overlay).toBe(true)
    expect(useDataFigureStore.getState().selected).toEqual({ path: 'g.txt', sheet: null })
  })

  it('焦点：打开时进浮层，关闭后还给打开它的那一行', async () => {
    overview.mockResolvedValue(ov())
    await start()
    const rowEl = qa<HTMLElement>('[data-studio-file]')[0]!
    rowEl.focus()
    await click(rowEl)
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(q('[data-data-overlay]')!.contains(document.activeElement)).toBe(true)
    await click(q('[data-studio-close]'))
    expect(document.activeElement).toBe(rowEl)
  })

  it('认得准的表：预览 + 开始；没认准的：点哪一行当列名之后才有开始；读不了的：没有开始', async () => {
    overview.mockImplementation(async (qy) =>
      qy.path === 'g.txt'
        ? qy.headerRow == null
          ? ov({ path: 'g.txt', status: 'unsure', header_row: 2, raw_lines: [{ line: 1, text: 'a' }, { line: 2, text: 'b' }, { line: 3, text: 'c' }], fields: [], preview_rows: [] })
          : ov({ path: 'g.txt', status: 'ok', header_row: qy.headerRow })
        : qy.path === 'b.dat'
          ? ov({ path: 'b.dat', status: 'unreadable', code: 'data_binary', fields: [], preview_rows: [] })
          : ov(),
    )
    await start()
    await open(0)
    expect(q('[data-studio-start]')).not.toBeNull()
    await open(1)
    expect(q('[data-studio-start]')).toBeNull()
    expect(q('[data-studio-look]')).not.toBeNull()
    await click(q('[data-look-line="3"] input'))
    await flush()
    expect(overview).toHaveBeenLastCalledWith({ path: 'g.txt', sheet: null, headerRow: 3 }, 'pa')
    expect(q('[data-studio-start]')).not.toBeNull()
    await open(2)
    expect(q('[data-studio-start]')).toBeNull()
    expect(q('[data-studio-unreadable]')).not.toBeNull()
  })

  it('开始 → 选择题 → 选画法 → 画这张 → 进编辑器（准备会话的「打开并运行」）', async () => {
    overview.mockResolvedValue(ov())
    propose.mockResolvedValueOnce({ kind: 'ask', question_code: 'pick_value', unsure: null, options: [{ code: 'column', params: { column: 'day', kind: 'numeric' }, answer: { y: 'day' } }] })
    propose.mockResolvedValueOnce({
      kind: 'candidates', session: 'S1', dropped: [],
      items: [{ id: 'i1', title_code: 'hist', why_code: 'hist', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/api/ai/data-figure/thumbnail/S1/i1' }],
    })
    commit.mockResolvedValueOnce({ task: { outcome: 'completed' }, script: 'tavotto_figures/a_figure.py', stem: 'a_figure', data_check: 'verified' })
    await start()
    await open(0)
    await click(q('[data-studio-start]'))
    await flush()
    expect(q('[data-studio-ask]')).not.toBeNull()
    await click(q('[data-studio-choice]'))
    await flush()
    expect(q('[data-studio-pick] img')!.getAttribute('src')).toBe('/api/ai/data-figure/thumbnail/S1/i1?pj=pa')
    await click(q('[data-studio-primary]'))
    await flush()
    expect(prepOpen).toHaveBeenCalledTimes(1)
    expect(useDataFigureStore.getState().overlay).toBe(false)
  })

  it('出备选失败：一句话 + 再试一次 / 换一张表，不卡住', async () => {
    overview.mockResolvedValue(ov())
    propose.mockRejectedValueOnce(new ApiError('x', 400, { code: 'data_not_found' }))
    await start()
    await open(0)
    await click(q('[data-studio-start]'))
    await flush()
    expect(q('[data-studio-problem]')).not.toBeNull()
    expect(q('[data-studio-retry]')).not.toBeNull()
    expect(q('[data-studio-back]')).not.toBeNull()
  })

  it('文件夹里没有数据文件：一句话 + 换个文件夹', async () => {
    files.mockResolvedValue({ files: [], truncated: false })
    await start()
    expect(q('[data-studio-no-files]')!.textContent).toContain('没找到数据文件')
  })

  it('清单读失败：一句话 + 再试一次（再试一次真的重读）', async () => {
    files.mockRejectedValueOnce(new ApiError('x', 500, {}))
    await start()
    expect(q('[data-studio-data]')!.textContent).toContain('没能看清')
    files.mockResolvedValueOnce({ files: [], truncated: false })
    await click([...document.querySelectorAll('button')].find((b) => b.textContent === '再试一次')!)
    await flush()
    expect(files).toHaveBeenCalledTimes(2)
  })
})

describe('读不了但能补救的文本表（仪器说明行触发 data_ragged）', () => {
  const raw = [1, 2, 3, 4, 5].map((n) => ({ line: n, text: `line ${n}` }))
  const ragged = (over: Partial<AiDataOverview> = {}) => ov({ status: 'unreadable', code: 'data_ragged', header_row: 3, raw_lines: raw, fields: [], preview_rows: [], ...over })

  it('侧栏：清单里 unreadable 且带 preview → 「没认准」；没有 preview 的才是「读不了」', async () => {
    const rows = fileRows([
      { path: 'inst.txt', size: 1, table: { status: 'unreadable', code: 'data_ragged', delimiter: 'comma', header_row: 3, columns: null, rows: null, preview: [{ line: 1, text: 'Temperature,25' }] } },
      { path: 'bin.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null, preview: [] } },
    ])
    expect(rows[0]!.rows[0]!.status).toEqual({ kind: 'unsure' })
    expect(rows[1]!.rows[0]!.status).toEqual({ kind: 'unreadable', code: 'data_binary' })
    await mount(<DataList rows={rows} selected={null} overviews={{}} onSelect={() => {}} />)
    const txt = qa('[data-studio-nav]').map((r) => r.textContent)
    expect(txt.find((x) => x!.includes('inst.txt'))).toContain('没认准')
    expect(txt.find((x) => x!.includes('bin.dat'))).toContain('读不了')
  })

  it('点行重读：成功 → 清单变行数；仍读不了但有原文行 → 仍是「没认准」；没有原文行 → 读不了', async () => {
    const row = fileRows([{ path: 'inst.txt', size: 1, table: { status: 'unreadable', code: 'data_ragged', delimiter: 'comma', header_row: 3, columns: null, rows: null, preview: [{ line: 1, text: 'x' }] } }])[0]!.rows[0]!
    const { withOverview } = await import('@/lib/dataFigure')
    expect(withOverview(row, ov({ path: 'inst.txt', rows: 7, columns: 2 }))).toEqual({ kind: 'table', rows: 7, columns: 2 })
    expect(withOverview(row, ragged({ path: 'inst.txt', code: 'data_header_row_invalid' }))).toEqual({ kind: 'unsure' })
    expect(withOverview(row, ragged({ path: 'inst.txt', raw_lines: [] }))).toEqual({ kind: 'unreadable', code: 'data_ragged' })
  })

  it('预览：和没认准同一套（原文行、猜的行高亮、当作每列的名字、点别的行重读）；还没点过时不说原因', async () => {
    const onHeaderRow = vi.fn()
    await mount(<TablePreview ov={ragged()} loading={false} onHeaderRow={onHeaderRow} />)
    expect(q('[data-header]')!.getAttribute('data-look-line')).toBe('3')
    expect(q('[data-studio-header-tag]')!.textContent).toBe('当作每列的名字')
    expect(q('[data-studio-pick-why]')).toBeNull()
    expect(q('[data-studio-unreadable]')).toBeNull()
    await click(q('[data-look-line="5"] input'))
    expect(onHeaderRow).toHaveBeenCalledWith(5)
  })

  it('点的那一行读不成表：留在这一屏，用稳定码说一句为什么（中文，不露英文原文）；码不认识就不说', async () => {
    await mount(<TablePreview ov={ragged({ code: 'data_header_row_invalid' })} loading={false} picked onHeaderRow={() => {}} />)
    expect(q('[data-studio-pick-why]')!.textContent).toBe('指定的那一行不能当列名，换一行试试。')
    expect(qa('[data-look-line]').length).toBe(5)
    act(() => root.unmount())
    host.remove()
    await mount(<TablePreview ov={ragged({ code: 'data_future_code' })} loading={false} picked onHeaderRow={() => {}} />)
    expect(q('[data-studio-pick-why]')).toBeNull()
    expect(q('[data-studio-look]')).not.toBeNull()
  })

  it('真读不了（没有原文行）：保持那一句说明，没有行可点', async () => {
    await mount(<TablePreview ov={ragged({ raw_lines: [], code: 'data_binary' })} loading={false} onHeaderRow={() => {}} />)
    expect(q('[data-studio-unreadable]')!.textContent).toContain('读不了')
    expect(q('[data-studio-look]')).toBeNull()
  })

  it('整页：点开 → 原文行（没有开始）→ 点错行（仍读不了 + 原因，还能点）→ 点对行（预览 + 开始）→ 出图带 header_rows', async () => {
    const overview = vi.mocked(aiDataOverview)
    const propose = vi.mocked(aiDataFigurePropose)
    const files = vi.mocked(aiDataFilesAll)
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
    useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
    files.mockResolvedValue({ files: [{ path: 'inst.txt', size: 1, table: { status: 'unreadable', code: 'data_ragged', delimiter: 'comma', header_row: 3, columns: null, rows: null, preview: [{ line: 1, text: 'Temperature,25' }] } }], truncated: false })
    overview.mockImplementation(async (qy) => (qy.headerRow === 4 ? ov({ path: 'inst.txt', header_row: 4, rows: 7, columns: 2 }) : qy.headerRow === 2 ? ragged({ path: 'inst.txt', code: 'data_header_row_invalid', header_row: 2 }) : ragged({ path: 'inst.txt' })))
    propose.mockResolvedValue({ kind: 'nothing', reasons: ['no_usable_table'] })
    await act(async () => useDataFigureStore.getState().loadFiles())
    await mount(<><DataDrawer /><DataOverlay narrow={false} /></>)
    await flush()
    expect(qa('[data-studio-nav]')[0]!.textContent).toContain('没认准')
    await click(qa('[data-studio-file]')[0]!)
    await flush()
    expect(q('[data-studio-look]')).not.toBeNull()
    expect(q('[data-studio-start]')).toBeNull()
    await click(q('[data-look-line="2"] input'))
    await flush()
    expect(q('[data-studio-pick-why]')).not.toBeNull()
    expect(q('[data-studio-start]')).toBeNull()
    expect(qa('[data-studio-nav]')[0]!.textContent).toContain('没认准')
    await click(q('[data-look-line="4"] input'))
    await flush()
    expect(q('[data-studio-start]')).not.toBeNull()
    expect(qa('[data-studio-nav]')[0]!.textContent).toContain('7 行')
    await click(q('[data-studio-start]'))
    await flush()
    expect(propose.mock.calls[0]![0]).toMatchObject({ table: 'inst.txt', header_rows: { 'inst.txt': 4 } })
  })
})
