import { describe, expect, it } from 'vitest'
import real from '../../../tests/golden/legend_style_mismatch_real.json'
import type { Manifest, ManifestElement } from './api'
import { runSpec, type PreflightSpec } from './preflight'
import { loadProfile } from './profile'
import { formatMessage, setLocale } from '@/i18n'

/**
 * `legend-style-mismatch` 吃**真 worker 的输出**（错题本 L-042：协议两端至少一条用例直接消费对方的实际产物）。
 * `tests/golden/legend_style_mismatch_real.json` 由 `tests/test_legend_style_mismatch.py` 从真实 matplotlib 渲染重出并逐字比对；
 * golden 向量（`legend-style-*`）管两侧判据一致，这里管「真实的事实形状进得了 TS 求值器」，以及问题文案中英都能渲染。
 */

type RealElement = Pick<
  ManifestElement,
  'gid' | 'role' | 'label' | 'editable' | 'legend_entry' | 'legend_style_mismatch' | 'legend_style_source'
>

const SNAPSHOT = real as unknown as {
  elements: RealElement[]
  expected: { gids: string[]; message: { key: string; params: Record<string, unknown> }; detail: unknown }
}

function specOf(elements: RealElement[]): PreflightSpec {
  const manifest = {
    stem: 'Cases',
    size_mm: [80, 60],
    elements: elements.map((e) => ({ ...e, draggable: false, bbox: [0.1, 0.1, 0, 0] })),
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
    (i) => i.id === 'legend-style-mismatch',
  )

describe('legend-style-mismatch：真 worker 的 legend_style_mismatch', () => {
  it('快照里有该报与不该报的图例项', () => {
    const withFact = SNAPSHOT.elements.filter((e) => e.legend_style_mismatch)
    expect(withFact.length).toBeGreaterThan(3)
    expect(SNAPSHOT.elements.length).toBeGreaterThan(withFact.length + 5)
  })

  it('只报真不一致的那几项，gid、文案与 detail 与 Python 侧一致', () => {
    const hits = hitsOf(SNAPSHOT.elements)
    expect(hits).toHaveLength(1)
    expect(hits[0].severity).toBe('warn')
    expect([...hits[0].gids].sort()).toEqual(SNAPSHOT.expected.gids)
    expect(hits[0].message.key).toBe(`preflight.${SNAPSHOT.expected.message.key}`)
    expect(hits[0].message.values).toEqual(SNAPSHOT.expected.message.params)
    expect(hits[0].detail).toEqual(SNAPSHOT.expected.detail)
  })

  it('多个图例项各占一行：每一行说的是自己那一项、定位到自己（问题面板按 occurrence 出行）', () => {
    const [hit] = hitsOf(SNAPSHOT.elements)
    const byGid = new Map(SNAPSHOT.elements.map((e) => [e.gid, e]))
    expect(hit.occurrences.map((o) => o.gid).sort()).toEqual(SNAPSHOT.expected.gids)
    setLocale('zh-CN')
    const lines = new Map(hit.occurrences.map((o) => [o.gid, formatMessage(o.message)]))
    const text = (gid: string) =>
      String(byGid.get(gid)?.editable.find((f) => f.prop === 'text')?.value ?? '')
    for (const [gid, line] of lines) expect(line).toContain(`图例「${text(gid!)}」`)
    // 不同种类的不一致各说各的：线色那一项说线条颜色、柱那一项说颜色与图中对象
    const kinds = [...lines.values()]
    expect(kinds.some((l) => l.includes('线条颜色与曲线'))).toBe(true)
    expect(kinds.some((l) => l.includes('标记形状与曲线'))).toBe(true)
    expect(kinds.some((l) => l.includes('颜色与图中对象'))).toBe(true)
  })

  it('只认 worker 确证的来源：拿掉 legend_style_source（只剩 0034 猜的 source_gid）就不报（ADR 0219）', () => {
    const withFact = SNAPSHOT.elements.filter((e) => e.legend_style_mismatch)
    expect(withFact.every((e) => typeof e.legend_style_source === 'string')).toBe(true)
    const unverified = SNAPSHOT.elements.map((e) => {
      const { legend_style_source: _drop, ...rest } = e
      return rest
    })
    expect(unverified.some((e) => e.legend_entry?.source_gid && e.legend_style_mismatch)).toBe(true)
    expect(hitsOf(unverified)).toEqual([])
  })

  it('问题文案中英都渲染得出来（不是 key 原样）', () => {
    const [hit] = hitsOf(SNAPSHOT.elements)
    setLocale('zh-CN')
    expect(formatMessage(hit.message)).toBe('图例「B」的标记颜色与曲线不一致')
    setLocale('en-US')
    expect(formatMessage(hit.message)).toBe(
      'The legend entry “B” has a different marker color from its curve',
    )
    setLocale('zh-CN')
  })
})
