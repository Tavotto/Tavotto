/**
 * 扫描快照 → 人话与「条要不要出现」（T02）。
 *
 * 守两件事：**按后端枚举查句子**（不从原始计数里自己推结论）；**没有需要注意的事就不出现**，但
 * 「没看全」「失败」「取消」「有目标」一定出现，且用户主动重新打开时照常显示。
 */
import { describe, expect, it } from 'vitest'
import type { ProjectScan } from '@/lib/api'
import { hasPartialIssues, issueLine, scanBarVisible, scanLine, scanNeedsAttention } from './projectScanText'

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
const flags = { dismissedScanId: null, forced: false, slow: false }

describe('条要不要出现', () => {
  it.each([
    ['static_source', false],
    ['already_connected', false],
    ['nothing_found', false],
    ['target_found', true],
    ['choose_target', true],
    ['unchecked', true],
  ] as const)('完整扫描 outcome=%s → %s', (kind, visible) => {
    expect(scanBarVisible(scan({ outcome: { kind } }), flags)).toBe(visible)
  })

  it('运行中：只有慢扫描才出现（静态小项目不闪一下）', () => {
    const running = scan({ state: 'running', phase: 'scanning', outcome: { kind: 'scanning' } })
    expect(scanBarVisible(running, flags)).toBe(false)
    expect(scanBarVisible(running, { ...flags, slow: true })).toBe(true)
  })

  it.each(['failed', 'cancelled'] as const)('%s 一定出现', (state) => {
    expect(scanNeedsAttention(scan({ state }), false)).toBe(true)
  })

  it('静态项目看不全时必须出现（不能当「没有脚本」）', () => {
    const partial = scan({
      state: 'partial',
      issues: [{ code: 'unreadable_dir', severity: 'partial', scope: 'dir', path: 'x', count: 1 }],
    })
    expect(hasPartialIssues(partial)).toBe(true)
    expect(scanBarVisible(partial, flags)).toBe(true)
  })

  it('设计内的 note（层级太深）本身不触发提示', () => {
    const note = scan({
      issues: [{ code: 'depth_limit', severity: 'note', scope: 'dir', path: 'a/b', count: 1 }],
    })
    expect(scanBarVisible(note, flags)).toBe(false)
  })

  it('关闭只隐藏这一轮；新的一轮（新 scan_id）自然再出现；重新打开强制显示', () => {
    const s = scan({ outcome: { kind: 'target_found' } })
    expect(scanBarVisible(s, { ...flags, dismissedScanId: 's1' })).toBe(false)
    expect(scanBarVisible({ ...s, scan_id: 's2' }, { ...flags, dismissedScanId: 's1' })).toBe(true)
    expect(scanBarVisible(scan(), { ...flags, forced: true })).toBe(true)
    expect(scanBarVisible(null, { ...flags, forced: true })).toBe(false)
  })
})

describe('句子', () => {
  it('运行中只说已发现的计数，没有百分比', () => {
    const line = scanLine(
      scan({
        state: 'running',
        phase: 'scanning',
        outcome: { kind: 'scanning' },
        found: { scripts: 3, assets: 12 },
      }),
    )
    expect(line).toContain('3')
    expect(line).toContain('12')
    expect(line).not.toMatch(/%|％/)
  })

  it('有目标时点名脚本；看不全时追加一句', () => {
    const line = scanLine(
      scan({
        outcome: { kind: 'target_found' },
        default_target: 'plot.py',
        state: 'partial',
        issues: [{ code: 'symlinked_dir', severity: 'partial', scope: 'dir', path: 'a', count: 1 }],
      }),
    )
    expect(line).toContain('plot.py')
    expect(line).toContain('没有检查完')
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
