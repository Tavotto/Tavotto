/**
 * 扫描快照 → 人话与「引导卡要不要露出来」（T02 / T13b）。
 *
 * 守两件事：**按后端枚举查句子**（不从原始计数里自己推结论）；**没有需要注意的事就不露**，但
 * 「没看全」「失败」「取消」「有目标」一定露出来，且用户主动「显示项目检查结果」时照常显示。
 */
import { describe, expect, it } from 'vitest'
import type { ProjectScan } from '@/lib/api'
import { hasPartialIssues, issueLine, scanCard, scanLine } from './projectScanText'

const scan = (over: Partial<ProjectScan> = {}): ProjectScan => ({
  scan_version: 1,
  project_id: 'pj',
  scan_id: 's1',
  epoch: 1,
  observation_seq: 1,
  reason: 'claim',
  state: 'complete',
  phase: 'completed',
  outcome: { kind: 'static_source' },
  budget: { entries: 3, scripts: 0, assets: 2, elapsed_s: 0.01 },
  issues: [],
  checks: [],
  actions: [],
  ...over,
})
const flags = { forced: false, slow: false }
const kindOf = (s: ProjectScan | null, f = flags) => scanCard(s, f)?.kind ?? null

describe('引导卡要不要露出来（T13b）', () => {
  it.each([
    ['static_source', null],
    ['already_connected', null],
    ['nothing_found', null],
    ['target_found', 'discover'],
    ['choose_target', 'choose'],
    ['unchecked', 'stuck'],
  ] as const)('完整扫描 outcome=%s → %s', (kind, card) => {
    expect(kindOf(scan({ outcome: { kind } }))).toBe(card)
  })

  it('运行中：只有慢扫描才出角标（静态小项目不闪一下）', () => {
    const running = scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } })
    expect(kindOf(running)).toBe(null)
    expect(kindOf(running, { ...flags, slow: true })).toBe('scanning')
  })

  it.each(['failed', 'cancelled'] as const)('%s 一定露出来', (state) => {
    expect(kindOf(scan({ state }))).toBe('stuck')
  })

  it('静态项目看不全时必须露出来（不能当「没有脚本」）', () => {
    const partial = scan({
      state: 'partial',
      issues: [{ code: 'unreadable_dir', severity: 'partial', scope: 'dir', path: 'x', count: 1 }],
    })
    expect(hasPartialIssues(partial)).toBe(true)
    expect(kindOf(partial)).toBe('stuck')
  })

  it('设计内的 note（层级太深）本身不触发提示', () => {
    const note = scan({
      issues: [{ code: 'depth_limit', severity: 'note', scope: 'dir', path: 'a/b', count: 1 }],
    })
    expect(kindOf(note)).toBe(null)
  })

  it('用户主动「显示项目检查结果」：没什么要说的也露出那一句', () => {
    expect(kindOf(scan(), { ...flags, forced: true })).toBe('quiet')
    expect(kindOf(null, { ...flags, forced: true })).toBe(null)
  })
})

describe('句子', () => {
  it('卡片标题一句话、不带句号：有目标时点名脚本；多个时说个数', () => {
    const one = scanCard(scan({ outcome: { kind: 'target_found' }, default_target: 'plot.py' }), flags)!
    expect(scanLine(one)).toBe('发现绘图脚本 plot.py')
    const many = scanCard(
      scan({
        outcome: { kind: 'choose_target' },
        targets: [
          { script: 'a.py', role: 'plot' },
          { script: 'b.py', role: 'plot' },
          { script: 'c.py', role: 'auxiliary' },
        ] as never,
      }),
      flags,
    )!
    expect(scanLine(many)).toBe('发现 2 个绘图脚本')
    expect(scanLine(scanCard(scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } }), { ...flags, slow: true })!)).not.toMatch(/%|％/)
  })

  it('账本每个 code 都有一句话，路径作为插值出现', () => {
    for (const code of [
      'unreadable_dir',
      'unreadable_file',
      'symlinked_dir',
      'placeholder_file',
      'file_too_large',
      'parse_budget',
      'depth_limit',
      'entry_budget',
      'script_limit',
      'asset_limit',
      'source_byte_budget',
      'time_budget',
      'cancelled',
      'more_issues',
    ]) {
      const text = issueLine({ code, severity: 'partial', scope: 'dir', path: 'sub/dir', count: 2 })
      expect(text, code).toBeTruthy()
      expect(text, code).not.toContain('scan.issue')
    }
    expect(issueLine({ code: 'unreadable_dir', severity: 'partial', scope: 'dir', path: 'sub/dir', count: 1 })).toContain('sub/dir')
  })
})
