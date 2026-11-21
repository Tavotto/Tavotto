import { describe, expect, it } from 'vitest'
import real from '../../../tests/golden/scale_domain_real.json'
import type { Manifest, ManifestElement } from './api'
import { runSpec, type PreflightSpec } from './preflight'
import { loadProfile } from './profile'
import { formatMessage, setLocale } from '@/i18n'

/**
 * V-001 `log-axis-nonpositive` 吃**真 worker 的输出**（错题本 L-042：协议两端至少一条用例直接消费对方的实际产物）。
 * `tests/golden/scale_domain_real.json` 由 `tests/test_preflight_log_axis.py` 从真实 matplotlib 渲染重出并逐字比对；
 * golden 向量（`log-axis-*`）管的是两侧判据一致，这里管的是「真实的事实形状进得了 TS 求值器」。
 */

interface RealElement {
  title: string
  gid: string
  role: string
  scale_domain: ManifestElement['scale_domain']
}

const SNAPSHOT = real as unknown as { elements: RealElement[]; expected_gids: string[] }

function specOf(elements: RealElement[]): PreflightSpec {
  const manifest = {
    stem: 'LogCases',
    size_mm: [80, 60],
    elements: elements.map((e) => ({
      gid: e.gid,
      role: e.role,
      label: e.title,
      draggable: false,
      bbox: [0.1, 0.1, 0.2, 0.2],
      editable: [],
      scale_domain: e.scale_domain,
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

describe('log-axis-nonpositive：真 worker 的 scale_domain', () => {
  it('快照非空，且带着该报与不该报的轴', () => {
    expect(SNAPSHOT.elements.length).toBeGreaterThan(10)
    expect(SNAPSHOT.expected_gids.length).toBeGreaterThan(0)
  })

  it('只报真画不出来的那几根轴，gid 与 Python 侧一致', () => {
    const hits = runSpec(specOf(SNAPSHOT.elements), loadProfile('lab-publication-v1', null)).filter(
      (i) => i.id === 'log-axis-nonpositive',
    )
    expect(hits).toHaveLength(1)
    expect(hits[0].severity).toBe('warn')
    expect([...hits[0].gids].sort()).toEqual(SNAPSHOT.expected_gids)
    // 每根轴的每一维各一条命中，定位到那一维的尺度字段
    const props = new Set(hits[0].occurrences.map((o) => o.prop))
    expect(props).toEqual(new Set(['xscale', 'yscale']))
  })

  it('文案一句话：说哪根轴、几个值', () => {
    const hits = runSpec(specOf(SNAPSHOT.elements), loadProfile('lab-publication-v1', null)).filter(
      (i) => i.id === 'log-axis-nonpositive',
    )
    setLocale('zh-CN')
    expect(formatMessage(hits[0].message)).toBe('对数 y 轴上有 2 个 ≤ 0 的值画不出来')
    setLocale('en-US')
    expect(formatMessage(hits[0].message)).toBe("2 values ≤ 0 on the log y axis can't be drawn")
    setLocale('zh-CN')
  })

  it('全为正的轴（含 figurebench 那种写法）一条都不报', () => {
    const clean = SNAPSHOT.elements.filter((e) => !SNAPSHOT.expected_gids.includes(e.gid))
    expect(clean.length).toBeGreaterThan(5)
    const hits = runSpec(specOf(clean), loadProfile('lab-publication-v1', null)).filter(
      (i) => i.id === 'log-axis-nonpositive',
    )
    expect(hits).toEqual([])
  })
})
