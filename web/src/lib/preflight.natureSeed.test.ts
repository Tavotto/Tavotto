import { expect, it } from 'vitest'
import type { Manifest } from './api'
import { journalOverride, journalProfile, loadCatalog } from './journals'
import { runSpec, type PreflightSpec } from './preflight'

/**
 * Nature 种子记着 7 pt 字号上限（qualifier recommended）：它不折进 override，预检不按 7 pt 判。
 * 原文要求 8 pt 粗体的面板标号不会被报 `font-too-large`（面板标号另记在 `info.panel_label`）。
 * 与 `tests/test_journal_catalog.py::test_nature_seed_max_font_is_recommended_and_not_a_preflight_limit` 同一件事的前端一侧。
 */
function specOf(sizes: Record<string, number>): PreflightSpec {
  const manifest = {
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: Object.entries(sizes).map(([gid, size]) => ({
      gid,
      role: 'text',
      label: gid,
      draggable: false,
      bbox: [0.1, 0.1, 0.2, 0.2],
      editable: [{ prop: 'fontsize', type: 'number', value: size }],
    })),
  } as unknown as Manifest
  const rect: [number, number, number, number] = [0, 0, 80, 60]
  return {
    page: { w_mm: 80, h_mm: 60, margin_mm: 0 },
    panels: [
      { id: 'p1', name: 'p1', kind: 'pdf', rect_mm: rect, scale: 1, manifest, px_w: null, missing: false, stale: false, render_error: null, unapplied_overrides: 0, hidden: false },
    ],
    texts: [],
    objects: [{ id: 'p1', type: 'panel', rect_mm: rect, hidden: false }],
  } as unknown as PreflightSpec
}

it('Nature：8 pt 面板标号 + 6 pt 正文不出 font-too-large / 字号下限类问题', () => {
  const cat = loadCatalog()
  const rule = cat.journals.nature.rules?.max_font_size_pt
  expect(rule?.value).toBe(7)
  expect(rule?.qualifier).toBe('recommended')
  expect(journalOverride('nature', cat)).not.toHaveProperty('max_font_size_pt')
  const ids = new Set(runSpec(specOf({ panel_label_a: 8, body_text: 6 }), journalProfile('nature', cat)).map((i) => i.id))
  for (const id of ['font-too-large', 'font-too-small', 'font-below-absolute-floor']) expect(ids.has(id)).toBe(false)
  // 反证：用例里的文字元素确实进了求值器（超过兜底上限 72 pt 会报）
  const big = runSpec(specOf({ huge: 90 }), journalProfile('nature', cat)).map((i) => i.id)
  expect(big).toContain('font-too-large')
})
