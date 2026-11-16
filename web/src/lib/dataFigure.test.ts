import { describe, expect, it } from 'vitest'
import type { AiDataFileEntry, AiDataOption, AiDataOverview } from '@/lib/api'
import {
  cellModeOf,
  fileRows,
  isStaleProposeCode,
  mergeAnswers,
  previewCardWidth,
  sameTable,
  tableKey,
  usableOptions,
  withOverview,
} from './dataFigure'

describe('tableKey：结构化键，不用文件名、不拼字符串', () => {
  it('同名文件在不同目录是两张表；工作表名里有 # 或引号也不撞车', () => {
    expect(tableKey({ path: 'a/x.csv', sheet: null })).not.toBe(tableKey({ path: 'b/x.csv', sheet: null }))
    expect(tableKey({ path: 'w.xlsx', sheet: 'a#b' })).not.toBe(tableKey({ path: 'w.xlsx#a', sheet: 'b' }))
    expect(tableKey({ path: 'w.xlsx', sheet: null })).not.toBe(tableKey({ path: 'w.xlsx', sheet: 'null' }))
    expect(sameTable({ path: 'a', sheet: 's' }, { path: 'a', sheet: 's' })).toBe(true)
    expect(sameTable(null, { path: 'a', sheet: null })).toBe(false)
  })
})

describe('fileRows：清单里每一行怎么说（全由后端的清单给）', () => {
  const entries: AiDataFileEntry[] = [
    { path: 'ok.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 3, rows: 12 } },
    { path: 'guess.txt', size: 1, table: { status: 'unsure', code: null, delimiter: 'space', header_row: 2, columns: 2, rows: null, preview: [{ line: 1, text: 'x' }] } },
    { path: 'bad.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null } },
    { path: 'over-budget.csv', size: 1, table: null },
    { path: 'no-table-key.csv', size: 1 },
    {
      path: 'book.xlsx',
      size: 1,
      sheets: [
        { name: 'a', hidden: false, rows: 4, columns: 2, status: 'ok', code: null },
        { name: 'hid', hidden: true, rows: 1, columns: 1, status: 'ok', code: null },
        { name: 'b', hidden: false, rows: null, columns: null, status: null, code: null },
        { name: 'c', hidden: false, rows: null, columns: null, status: 'unreadable', code: 'data_unreadable' },
      ],
    },
    { path: 'unopened.xlsx', size: 1, sheets: null },
  ]
  const rows = fileRows(entries)
  const status = (i: number) => rows[i]!.rows.map((r) => r.status)

  it('认得准 → 行数 · 列数；没认准 / 读不了各是各的', () => {
    expect(status(0)).toEqual([{ kind: 'table', rows: 12, columns: 3 }])
    expect(status(1)).toEqual([{ kind: 'unsure' }])
    expect(status(2)).toEqual([{ kind: 'unreadable', code: 'data_binary' }])
  })

  it('table 为 null（超出读取预算）是「没读」，不是「读不了」；字段缺失也按没读，不当成空', () => {
    expect(status(3)).toEqual([{ kind: 'unread' }])
    expect(status(4)).toEqual([{ kind: 'unread' }])
  })

  it('xlsx：每张可见工作表一行；四项全空是「没读」、读不了是读不了；整本没读（sheets 为 null）没有子行', () => {
    expect(rows[5]!.workbook).toBe(true)
    expect(status(5)).toEqual([{ kind: 'table', rows: 4, columns: 2 }, { kind: 'unread' }, { kind: 'unreadable', code: 'data_unreadable' }])
    expect(rows[5]!.rows.map((r) => r.ref.sheet)).toEqual(['a', 'b', 'c'])
    expect(rows[6]).toMatchObject({ workbook: true, unread: true, rows: [] })
  })

  it('用户指了表头行并重读成功后，这一行改说重读后的行列数；别的表的概览不串', () => {
    const row = rows[1]!.rows[0]!
    const ov = { path: 'guess.txt', sheet: null, status: 'ok', rows: 40, columns: 2, code: null } as AiDataOverview
    expect(withOverview(row, ov)).toEqual({ kind: 'table', rows: 40, columns: 2 })
    expect(withOverview(row, { ...ov, path: 'other.txt' })).toEqual({ kind: 'unsure' })
    expect(withOverview(row, undefined)).toEqual({ kind: 'unsure' })
    expect(withOverview(row, { ...ov, status: 'unreadable', code: 'data_header_row_invalid' })).toEqual({ kind: 'unreadable', code: 'data_header_row_invalid' })
  })
})

describe('mergeAnswers：cells 按列深合并（后端一次问多列，浅合并只剩最后一列）', () => {
  it('两列的处理方式都保留；别的键后来的覆盖前面的', () => {
    const a = mergeAnswers({ y: 'v', cells: { c1: { mode: 'skip' } } }, { cells: { c2: { mode: 'extract' } } })
    expect(a).toEqual({ y: 'v', cells: { c1: { mode: 'skip' }, c2: { mode: 'extract' } } })
    expect(mergeAnswers(a, { y: 'w' })).toMatchObject({ y: 'w' })
  })
  it('不改入参', () => {
    const cur = { cells: { c1: { mode: 'skip' } } }
    mergeAnswers(cur, { cells: { c2: { mode: 'skip' } } })
    expect(cur).toEqual({ cells: { c1: { mode: 'skip' } } })
  })
})

describe('选项过滤与处理方式', () => {
  const opt = (mode: string, free = false): AiDataOption => ({ code: mode, params: {}, answer: { cells: { col: { mode } } }, ...(free ? { free_text: true } : {}) })
  it('带文字输入的选项（「我来说明」）按 free_text 滤掉，不按码名', () => {
    expect(usableOptions([opt('skip'), opt('extract'), opt('describe', true), opt('whatever', true)])).toHaveLength(2)
    expect(usableOptions(undefined)).toEqual([])
  })
  it('cellModeOf 只认 skip / extract', () => {
    expect(cellModeOf(opt('skip'))).toBe('skip')
    expect(cellModeOf(opt('extract'))).toBe('extract')
    expect(cellModeOf(opt('describe', true))).toBeNull()
    expect(cellModeOf({ code: 'x', params: {}, answer: { y: 'a' } })).toBeNull()
  })
  it('会话过期 / 数据变了 / 备选没了 是静默重来的三个码，别的不是', () => {
    for (const c of ['propose_session_expired', 'propose_data_changed', 'propose_candidate_unknown']) expect(isStaleProposeCode(c)).toBe(true)
    expect(isStaleProposeCode('propose_input_invalid')).toBe(false)
    expect(isStaleProposeCode(undefined)).toBe(false)
  })
})

describe('previewCardWidth：卡片宽度跟内容走，夹在 [360, 640]', () => {
  const ov = (names: string[], cell = '1', over: Partial<AiDataOverview> = {}): AiDataOverview =>
    ({ path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: names.length, layout: null,
      fields: names.map((name) => ({ name, kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false })),
      preview_rows: [{ line: 2, cells: names.map(() => cell) }], truncated: false, lead_lines: [], raw_lines: [], ...over }) as AiDataOverview
  it('列少 = 最小 360；列多 = 最大 640；中间随列数与内容变', () => {
    expect(previewCardWidth(ov(['a', 'b']))).toBe(360)
    expect(previewCardWidth(ov(Array.from({ length: 20 }, (_, i) => `col_${i}`)))).toBe(640)
    const mid = previewCardWidth(ov(['time_s', 'voltage_v', 'current_a', 'temp']))
    expect(mid).toBeGreaterThan(360)
    expect(mid).toBeLessThan(640)
    expect(previewCardWidth(ov(['a', 'b', 'c'], '0.123456789012345'))).toBeGreaterThan(360)
  })
  it('没认准的原文行固定 560；没有概览 / 读不了 / 无列给默认宽', () => {
    expect(previewCardWidth(ov([], '1', { status: 'unsure', raw_lines: [{ line: 1, text: 'x' }] }))).toBe(560)
    expect(previewCardWidth(null)).toBe(420)
    expect(previewCardWidth(ov([], '1', { status: 'unreadable', raw_lines: [] }))).toBe(420)
  })
})
