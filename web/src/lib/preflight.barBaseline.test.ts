import { describe, expect, it } from 'vitest'
import real from '../../../tests/golden/bar_baseline_real.json'
import type { Manifest, ManifestElement } from './api'
import { axisNum, runSpec, type PreflightSpec } from './preflight'
import { loadProfile } from './profile'
import { formatMessage, setLocale } from '@/i18n'

/**
 * V-002 `bar-baseline-truncated` 吃**真 worker 的输出**（错题本 L-042：协议两端至少一条用例直接消费对方的实际产物）。
 * `tests/golden/bar_baseline_real.json` 由 `tests/test_preflight_bar_baseline.py` 从真实 matplotlib 渲染重出并逐字比对，
 * 带着 `bar_baseline` 与同一 axes 元素的 `[xy]lim` / `[xy]scale` 字段；golden 向量（`bar-baseline-*`）管两侧判据一致，
 * 这里管「真实的事实形状进得了 TS 求值器」。
 */

interface RealElement {
  title: string
  gid: string
  role: string
  editable: ManifestElement['editable']
  bar_baseline: ManifestElement['bar_baseline']
}

const SNAPSHOT = real as unknown as { elements: RealElement[]; expected_gids: string[] }

function specOf(elements: RealElement[]): PreflightSpec {
  const manifest = {
    stem: 'BarCases',
    size_mm: [80, 60],
    elements: elements.map((e) => ({
      gid: e.gid,
      role: e.role,
      label: e.title,
      draggable: false,
      bbox: [0.1, 0.1, 0.2, 0.2],
      editable: e.editable,
      bar_baseline: e.bar_baseline,
    })),
  } as unknown as Manifest
  const rect: [number, number, number, number] = [0, 0, 80, 60]
  return {
    page: { w_mm: 80, h_mm: 60, margin_mm: 0 },
    panels: [
      {
        id: 'p1',
        name: 'p1',
        kind: 'pdf',
        rect_mm: rect,
        scale: 1,
        manifest,
        px_w: null,
        missing: false,
        stale: false,
        render_error: null,
        unapplied_overrides: 0,
        hidden: false,
      },
    ],
    texts: [],
    objects: [{ id: 'p1', type: 'panel', rect_mm: rect, hidden: false }],
  } as unknown as PreflightSpec
}

const hitsOf = (elements: RealElement[]) =>
  runSpec(specOf(elements), loadProfile('lab-publication-v1', null)).filter(
    (i) => i.id === 'bar-baseline-truncated',
  )

describe('bar-baseline-truncated：真 worker 的 bar_baseline', () => {
  it('快照非空，且带着该报与不该报的轴', () => {
    expect(SNAPSHOT.elements.length).toBeGreaterThan(10)
    expect(SNAPSHOT.expected_gids.length).toBeGreaterThan(5)
  })

  it('只报真截掉柱底的那几根轴，gid 与 Python 侧一致', () => {
    const hits = hitsOf(SNAPSHOT.elements)
    expect(hits).toHaveLength(1)
    expect(hits[0].severity).toBe('warn')
    expect([...hits[0].gids].sort()).toEqual(SNAPSHOT.expected_gids)
    // 每根轴的每一维各一条命中，定位到那一维的范围字段（横柱 / 横向直方图在 x）
    const props = new Set(hits[0].occurrences.map((o) => o.prop))
    expect(props).toEqual(new Set(['xlim', 'ylim']))
  })

  it('文案一句话：柱子从几起、轴显示的范围；竖柱说纵轴、横柱说横轴', () => {
    const one = (title: string) => hitsOf(SNAPSHOT.elements.filter((e) => e.title === title))[0]
    setLocale('zh-CN')
    expect(formatMessage(one('cut').message)).toBe(
      '柱子从 0 起，纵轴却只显示 4 到 9，柱长不再和数值成比例',
    )
    expect(formatMessage(one('barh_cut').message)).toBe(
      '柱子从 0 起，横轴却只显示 4 到 9，柱长不再和数值成比例',
    )
    setLocale('en-US')
    expect(formatMessage(one('cut').message)).toBe(
      'The bars start at 0, but the vertical axis only shows 4 to 9, so bar lengths no longer match the values',
    )
    setLocale('zh-CN')
  })

  it('不该报的轴（浮动 / 堆叠上层 / 差值 / log / symlog / 反转但可见 / twin 左轴）一条都不报', () => {
    const clean = SNAPSHOT.elements.filter((e) => !SNAPSHOT.expected_gids.includes(e.gid))
    expect(clean.map((e) => e.title).sort()).toEqual(
      expect.arrayContaining(['floating', 'gantt', 'stacked_upper', 'diff', 'log', 'symlog_cut']),
    )
    expect(hitsOf(clean)).toEqual([])
  })
})

describe('axisNum（与 engine/preflight.py 的 _axis_num 同源）', () => {
  it.each([
    [0, '0'],
    [-0, '0'],
    [4, '4'],
    [8.65, '8.65'],
    [123456.7, '123457'],
    [1e7, '10000000'],
    [1e-7, '0.0000001'],
    [0.1 + 0.2, '0.3'],
    [1000.5, '1001'],
    [-1000.5, '-1001'],
    [2.5e-13, '0.00000000000025'],
  ])('%s → %s', (v, text) => {
    expect(axisNum(v)).toBe(text)
  })
})
