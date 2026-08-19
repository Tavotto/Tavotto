/**
 * 出版规范 profile 的加载与合并。
 *
 * 规则本身来自 `src/tavotto/profiles/publication.json`（`@profiles` 别名），
 * 与 Python 的 `engine/profiles.py` 是**同一个文件**。这里盯的是取用侧：
 * 旧文档没有 profile 字段、存了一个已经删掉的 id、期刊覆盖的合并语义。
 */
import { describe, expect, it } from 'vitest'
import {
  columnOf,
  DEFAULT_PROFILE_ID,
  hasProfile,
  listProfiles,
  loadProfile,
  profileStamp,
  severityOf,
} from './profile'
import { readExportDefaults, writeExportDefaults } from './exportDefaults'
import { migrateToProject, type FigureDocument } from '@/types/document'

describe('规范文件', () => {
  it('默认 profile 就是课题组那套，硬数字与 Python 侧同源', () => {
    expect(DEFAULT_PROFILE_ID).toBe('lab-publication-v1')
    const p = loadProfile()
    expect(p.widths_mm.single).toBe(80)
    expect(p.widths_mm.double).toBe(150)
    expect(p.default_font_size_pt).toBe(9)
    expect(p.min_effective_font_size_pt).toBe(8.5)
    expect(p.absolute_min_font_size_pt).toBe(8)
    expect(p.min_raster_dpi).toBe(300)
    expect(p.line_widths_pt).toEqual([0.5, 0.75, 1.0, 1.5])
    expect(p.axis_policy.tick_direction).toBe('in')
    expect(p.legend_policy.frame).toBe(false)
    expect(p.font_family.latin).toBe('Times New Roman')
    expect(p.preferred_formats.vector).toEqual(['pdf', 'svg'])
    // 绝不默认替用户的图重新配色
    expect(p.palette_policy.auto_recolor).toBe(false)
  })

  it('下拉列表把默认 profile 排在最前', () => {
    expect(listProfiles()[0].profile_id).toBe(DEFAULT_PROFILE_ID)
    expect(listProfiles().length).toBeGreaterThan(1)
  })
})

describe('journal 覆盖', () => {
  it('只覆盖点名的键，其余继承，且不换身份', () => {
    const p = loadProfile('lab-publication-v1', { widths_mm: { double: 178 } })
    expect(p.widths_mm.double).toBe(178)
    expect(p.widths_mm.single).toBe(80)
    expect(p.profile_id).toBe('lab-publication-v1')
    expect(p.derived_from).toBe('lab-publication-v1')
    expect(profileStamp(p).journal).toEqual({ widths_mm: { double: 178 } })
  })

  it('覆盖后 178mm 算双栏了', () => {
    expect(columnOf(loadProfile(), 178)).toBeNull()
    expect(columnOf(loadProfile('lab-publication-v1', { widths_mm: { double: 178 } }), 178)).toBe(
      'double',
    )
  })

  it('不改原始 profile（同一份文档被多个组件读）', () => {
    loadProfile('lab-publication-v1', { widths_mm: { double: 178 } })
    expect(loadProfile().widths_mm.double).toBe(150)
  })
})

describe('旧文档兼容', () => {
  it('schema 2 的旧文档没有 profile 字段——迁移不发明一个，取用侧退默认', () => {
    const legacy: FigureDocument = {
      schema: 2,
      name: 'old',
      page: { w: 150, h: 100 },
      objects: [],
      guides: [],
    }
    const project = migrateToProject(legacy)!
    expect(project.canvases[0].profile).toBeUndefined()
    // 取用时退到默认 profile，而不是崩掉
    expect(loadProfile(project.canvases[0].profile?.id).profile_id).toBe(DEFAULT_PROFILE_ID)
  })

  it('存着一个已经删掉的 profile id 时退回默认，而不是让对话框卡死', () => {
    expect(hasProfile('journal-that-was-removed')).toBe(false)
    expect(loadProfile('journal-that-was-removed').profile_id).toBe(DEFAULT_PROFILE_ID)
    writeExportDefaults({ profileId: 'journal-that-was-removed' })
    expect(readExportDefaults().profileId).toBe(DEFAULT_PROFILE_ID)
  })

  it('文档带 profile 时，schema 3 迁移原样带过去', () => {
    const doc: FigureDocument = {
      schema: 2,
      name: 'x',
      page: { w: 80, h: 60 },
      objects: [],
      guides: [],
      profile: { id: 'free-form-v1' },
    }
    const project = migrateToProject(doc)!
    expect(project.canvases[0].profile).toEqual({ id: 'free-form-v1' })
  })
})

describe('等级', () => {
  it('没登记的检查项按 warn 兜底，绝不静默降成 suggestion', () => {
    const p = loadProfile()
    expect(severityOf(p, 'brand-new-check-nobody-registered')).toBe('warn')
    expect(severityOf(p, 'font-too-small')).toBe('error')
    expect(severityOf(p, 'raster-text-not-verifiable')).toBe('not_verifiable')
  })

  it('free-form 把版式类降级，技术性检查照旧', () => {
    const p = loadProfile('free-form-v1')
    expect(severityOf(p, 'page-width')).toBe('suggestion')
    expect(severityOf(p, 'missing-asset')).toBe('warn') // 没登记 → 兜底
    expect(severityOf(p, 'font-too-small')).toBe('warn')
  })
})
