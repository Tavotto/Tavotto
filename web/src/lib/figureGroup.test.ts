import { describe, expect, it } from 'vitest'
import type { PanelInfo } from '@/lib/api'
import type { ReadinessReport } from '@/lib/api'
import { figureMembers, inViewScope, isFigureCard, scopeReadiness, vectorPrimaryFor, visibleFigureCards } from './figureGroup'

const p = (id: string, extra: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/^.*\//, '').replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: id.endsWith('.pdf') ? 'pdf' : id.endsWith('.svg') ? 'svg' : 'raster',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 0,
  ...extra,
})

describe('figureGroup（ADR 0184）', () => {
  it('老后端不发 primary：每份文件都是一张卡、各成一组', () => {
    const panels = [p('a.pdf'), p('a.png')]
    expect(panels.every(isFigureCard)).toBe(true)
    expect(figureMembers(panels).size).toBe(2)
  })

  it('同一图键归成一组，只有主文件是卡', () => {
    const panels = [
      p('a.pdf', { figure_key: 'dir:a', primary: true }),
      p('png/a.png', { figure_key: 'dir:a', primary: false }),
      p('b.png', { figure_key: 'dir:b', primary: true }),
    ]
    expect(panels.filter(isFigureCard).map((x) => x.id)).toEqual(['a.pdf', 'b.png'])
    expect(figureMembers(panels).get('dir:a')?.map((x) => x.id)).toEqual(['a.pdf', 'png/a.png'])
  })

  it('有脚本的图：PNG 的矢量主文件；没脚本 / 主文件是位图 / 自己就是主文件时为 null', () => {
    const scripted = [
      p('out/a.pdf', { figure_key: 'script:a', primary: true, script: 'plot.py' }),
      p('a.png', { figure_key: 'script:a', primary: false, script: 'plot.py' }),
    ]
    expect(vectorPrimaryFor('a.png', scripted)?.id).toBe('out/a.pdf')
    expect(vectorPrimaryFor('out/a.pdf', scripted)).toBeNull()
    const loose = [
      p('a.pdf', { figure_key: 'dir:a', primary: true }),
      p('a.png', { figure_key: 'dir:a', primary: false }),
    ]
    expect(vectorPrimaryFor('a.png', loose)).toBeNull()
    const rasterOnly = [
      p('a.png', { figure_key: 'script:a', primary: true }),
      p('png/a.jpg', { figure_key: 'script:a', primary: false }),
    ]
    expect(vectorPrimaryFor('png/a.jpg', rasterOnly)).toBeNull()
    expect(vectorPrimaryFor('missing.png', scripted)).toBeNull()
  })
})

describe('视图范围（ADR 0184 §三）', () => {
  const folder = { mode: 'folder' as const, scripts: [] }
  const scripts = { mode: 'scripts' as const, scripts: ['plot.py'] }
  const panels = [
    p('fig1.pdf', { figure_key: 'script:fig1', primary: true, script: 'plot.py', formats: ['fig1.pdf', 'png/fig1.png'] }),
    p('png/fig1.png', { figure_key: 'script:fig1', primary: false, script: 'plot.py', formats: ['fig1.pdf', 'png/fig1.png'] }),
    p('other.pdf', { figure_key: 'script:other', primary: true, script: 'other.py' }),
    p('raw/a.png', { figure_key: 'dir:raw/a', primary: true }),
    p('raw/b.png', { figure_key: 'dir:raw/b', primary: true }),
  ]

  it('整个文件夹：每张图一张卡', () => {
    expect(visibleFigureCards(panels, folder).map((x) => x.id)).toEqual(['fig1.pdf', 'other.pdf', 'raw/a.png', 'raw/b.png'])
  })

  it('只看脚本的图：散图与范围外脚本的图都不显示', () => {
    expect(visibleFigureCards(panels, scripts).map((x) => x.id)).toEqual(['fig1.pdf'])
  })

  it('用着的绝不藏：排版里用到任一份文件就显示', () => {
    expect(visibleFigureCards(panels, scripts, new Set(['raw/b.png'])).map((x) => x.id)).toEqual(['fig1.pdf', 'raw/b.png'])
    // 老排版里放的是 PNG：主文件那张卡照样算「用着」
    expect(inViewScope(undefined, scripts, true)).toBe(true)
    expect(inViewScope('x.py', folder, false)).toBe(true)
  })

  const report = (): ReadinessReport =>
    ({
      project_id: 'A', fingerprint: 'f', generated_at: 0,
      summary: { total: 5, editable: 3, layout_only: 2 } as never,
      panels: [
        { id: 'fig1.pdf', status: 'editable' },
        { id: 'png/fig1.png', status: 'editable' },
        { id: 'other.pdf', status: 'editable' },
        { id: 'raw/a.png', status: 'layout_only' },
        { id: 'raw/b.png', status: 'layout_only' },
      ],
      conflicts: null, project: {} as never, issues: [],
    }) as unknown as ReadinessReport

  it('胶囊计数按看得见的图：同一张图算一张，范围外不算', () => {
    const f = scopeReadiness(report(), panels, folder).summary
    expect([f.total, f.editable, f.layout_only]).toEqual([4, 2, 2])
    const s = scopeReadiness(report(), panels, scripts).summary
    expect([s.total, s.editable, s.layout_only]).toEqual([1, 1, 0])
  })

  it('老后端（没有归组字段）+ 整个文件夹：报告原样', () => {
    const plain = panels.map(({ figure_key: _k, primary: _p, formats: _f, ...rest }) => rest)
    const r = report()
    expect(scopeReadiness(r, plain, folder)).toBe(r)
  })
})
