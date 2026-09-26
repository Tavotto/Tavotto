/**
 * 样式 → 文档的映射（2026-09-24 用户实测后的三处修复）。
 *
 * 1. 字体能落到刻度与图例上：`ticks.fontfamily`、`legend_text.fontfamily`；图例标题没有
 *    override 入口，如实进 `unmappable`。
 * 2. 样式里的数字是**页面上**的 pt：写入 ÷ `panelScale`，提取 × `panelScale`——
 *    面板缩到 60% 时写 9 pt，读者量到的是 9 pt。
 * 3. 「恢复原样」清的范围与样式能写的范围同一张表。
 *
 * manifest 夹具的字段与真引擎对 `examples/figures/fig1_kinetics.py` 报的一致
 * （刻度组有 `fontfamily`、图例容器没有、图例项有；图例有 `title` 字段）。
 */
import { describe, expect, it } from 'vitest'
import type { Manifest } from './api'
import { panelScale } from './preflight'
import {
  extractFromManifest,
  planStyle,
  presetDelta,
  STYLE_ROLE_PROPS,
  styleOverrideTargets,
  toPageValue,
  toScriptValue,
  type StylePreset,
} from './stylePresets'
import type { PanelObject } from '@/types/document'

const field = (prop: string, value: unknown, type = typeof value === 'number' ? 'number' : 'enum') => ({
  prop,
  type,
  value,
})

const el = (gid: string, role: string, editable: ReturnType<typeof field>[]) => ({
  gid,
  role,
  label: gid,
  bbox: [0, 0, 0.1, 0.1],
  draggable: false,
  editable,
})

const manifest = (legendTitle = ''): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [80, 57.6],
    elements: [
      el('axes_0', 'axes', [field('spine_linewidth', 0.6)]),
      el('axes_0.title', 'title', [field('fontsize', 9), field('fontfamily', 'serif')]),
      el('axes_0.xlabel', 'axis_label', [field('fontsize', 9), field('fontfamily', 'serif')]),
      el('axes_0.lines_0', 'line', [field('linewidth', 1.2)]),
      el('axes_0.legend', 'legend', [field('fontsize', 8), field('title', legendTitle, 'text')]),
      el('axes_0.legend.texts_0', 'legend_text', [field('fontsize', 8), field('fontfamily', 'serif')]),
      el('axes_0.xticks', 'ticks', [field('fontsize', 8), field('fontfamily', 'serif'), field('direction', 'in')]),
      el('axes_0.yticks', 'ticks', [field('fontsize', 8), field('fontfamily', 'serif'), field('direction', 'in')]),
    ],
  }) as unknown as Manifest

/** 原生 80 mm 宽、页面上 w mm 宽的面板（缩放比 = w / 80） */
const panelAt = (w: number, overrides: PanelObject['overrides'] = []): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  script: 'fig1.py',
  name: 'Fig1',
  nativeW: 80,
  nativeH: 57.6,
  x: 0,
  y: 0,
  w,
  h: (57.6 * w) / 80,
  overrides,
})

const plan = (preset: StylePreset, panel: PanelObject, m = manifest()) =>
  planStyle(preset, [panel], () => m, { objects: [] } as never, false)
    .panels[0]

describe('字体落到刻度与图例上（ticks / legend_text 的 fontfamily）', () => {
  it('白名单按 manifest 真实暴露的位置登记：刻度组与图例项有，图例容器没有', () => {
    expect(STYLE_ROLE_PROPS.ticks).toContain('fontfamily')
    expect(STYLE_ROLE_PROPS.legend_text).toEqual(['fontfamily'])
    expect(STYLE_ROLE_PROPS.legend).not.toContain('fontfamily')
  })

  it('一份统一字体的样式写到标题、轴标题、两条刻度组与每条图例项上，没有一条进 unmappable', () => {
    const fam = 'Times New Roman'
    const preset: StylePreset = {
      name: 'Times',
      element: {
        title: { fontfamily: fam },
        axis_label: { fontfamily: fam },
        ticks: { fontfamily: fam },
        legend_text: { fontfamily: fam },
      },
    }
    const p = plan(preset, panelAt(80))
    const hit = p.patches.filter((x) => x.prop === 'fontfamily').map((x) => x.gid).sort()
    expect(hit).toEqual(
      ['axes_0.legend.texts_0', 'axes_0.title', 'axes_0.xlabel', 'axes_0.xticks', 'axes_0.yticks'].sort(),
    )
    expect(p.unmappable).toEqual([])
  })

  it('图例有标题时，标题的字体如实记一条「改不到」（引擎没有这个 override 入口）', () => {
    const preset: StylePreset = { name: 'Times', element: { legend_text: { fontfamily: 'Times New Roman' } } }
    expect(plan(preset, panelAt(80), manifest('Samples')).unmappable).toHaveLength(1)
    expect(plan(preset, panelAt(80), manifest('')).unmappable, '没有标题就没什么没改到').toEqual([])
  })
})

describe('样式里的数字 = 页面上读者量到的 pt', () => {
  it('面板缩到 60%：9 pt 的样式写成 15 pt 的脚本值，读者（预检）量到的是 9 pt', () => {
    const panel = panelAt(48)
    expect(panelScale(panel)).toBeCloseTo(0.6)
    const preset: StylePreset = {
      name: 'x',
      pt_basis: 'page',
      element: { title: { fontsize: 9 }, axes: { spine_linewidth: 0.75 }, ticks: { direction: 'in' } },
    }
    const p = plan(preset, panel)
    const value = (gid: string, prop: string) => p.patches.find((x) => x.gid === gid && x.prop === prop)?.value
    expect(value('axes_0.title', 'fontsize')).toBe(15)
    expect(Number(value('axes_0.title', 'fontsize')) * panelScale(panel)).toBeCloseTo(9, 6)
    expect(value('axes_0', 'spine_linewidth')).toBe(1.25)
    // 没有尺寸的量不换算
    expect(value('axes_0.xticks', 'direction')).toBe('in')
  })

  it('缩放比为 1 时原样写（不引入一次多余的取整）', () => {
    const p = plan({ name: 'x', element: { title: { fontsize: 8.75 } } }, panelAt(80))
    expect(p.patches[0].value).toBe(8.75)
  })

  it('换算取两位小数：manifest 按两位回报，多写的位数下一轮就被截掉', () => {
    // 70 / 80 = 0.875 → 9 / 0.875 = 10.2857…
    expect(toScriptValue('fontsize', 9, 0.875)).toBe(10.29)
    expect(toPageValue('fontsize', 10.29, 0.875)).toBe(9)
    expect(toScriptValue('color', '#000', 0.5)).toBe('#000')
  })

  it('提取也是页面上的值：同一张图缩到 60% 时提取出的字号是 9 × 0.6', () => {
    const got = extractFromManifest(manifest(), 0.6)
    expect(got.title.fontsize).toBe(5.4)
    expect(got.axes.spine_linewidth).toBe(0.36)
    expect(got.ticks.direction).toBe('in')
    // 「从图提取」也拿得到刻度与图例项的字体——此前白名单里没有，提取出来的样式天生缺这两格
    expect(got.ticks.fontfamily).toBe('serif')
    expect(got.legend_text.fontfamily).toBe('serif')
  })

  it('覆盖计数按换算后的值比：已经是同一个页面值的 override 不算「覆盖」', () => {
    const panel = panelAt(48, [{ gid: 'axes_0.title', prop: 'fontsize', value: 15 }])
    expect(plan({ name: 'x', pt_basis: 'page', element: { title: { fontsize: 9 } } }, panel).overwrites).toBe(0)
  })
})

describe('「恢复原样」清的范围 = 样式能写的范围', () => {
  it('只挑样式管得到的 role × prop（含配色那一格），别的修改原样留着', () => {
    const panel = panelAt(80, [
      { gid: 'axes_0.title', prop: 'fontsize', value: 12 },
      { gid: 'axes_0.xticks', prop: 'fontfamily', value: 'Arial' },
      { gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000' },
      { gid: 'axes_0.title', prop: 'text', value: '改过的标题' },
      { gid: 'axes_0.legend', prop: 'loc', value: 'upper left' },
    ])
    // 曲线此刻暴露 color（真 manifest 的样子）：配色那一格要求元素确实暴露这条属性
    const m = manifest()
    m.elements.find((e) => e.gid === 'axes_0.lines_0')!.editable.push(field('color', '#1f77b4') as never)
    expect(styleOverrideTargets(panel, m)).toEqual([
      { gid: 'axes_0.title', prop: 'fontsize' },
      { gid: 'axes_0.xticks', prop: 'fontfamily' },
      { gid: 'axes_0.lines_0', prop: 'color' },
    ])
  })

  it('Codex #547 r4109745746：角色白名单里有、但此刻元素不暴露的属性不挑（用户的孤儿 override 不被恢复原样删掉）', () => {
    const panel = panelAt(80, [
      { gid: 'axes_0.xticks', prop: 'direction', value: 'out' },
      { gid: 'axes_0.xticks', prop: 'length', value: 5 },
    ])
    // xticks 此刻暴露 direction、不暴露 length（夹具里没有这个字段）
    expect(styleOverrideTargets(panel, manifest())).toEqual([{ gid: 'axes_0.xticks', prop: 'direction' }])
  })
})

describe('Codex #547 评审', () => {
  it('P1 旧版存下的样式（没有 pt_basis）数字是脚本值：套在缩到 60% 的图上照旧原样写 9，不静默变成 15', () => {
    const legacy: StylePreset = { name: '老样式', element: { title: { fontsize: 9 } } }
    expect(plan(legacy, panelAt(48)).patches[0].value).toBe(9)
    const marked: StylePreset = { ...legacy, pt_basis: 'page' }
    expect(plan(marked, panelAt(48)).patches[0].value).toBe(15)
  })

  it('P1 标注 / 序号标签的变化量逐个属性算：只改了字号时不把颜色、字体一起带上', () => {
    const prev = { element: {}, annotation: { sizePt: 9, color: '#000000', fontFamily: 'serif' as const } }
    const next = { element: {}, annotation: { sizePt: 10, color: '#000000', fontFamily: 'serif' as const } }
    expect(presetDelta(prev, next).annotation).toEqual({ sizePt: 10 })
    expect(presetDelta(prev, prev).annotation, '没变就不出现').toBeUndefined()
    expect(presetDelta(null, next).annotation, '刚绑定时是整份').toEqual(next.annotation)
  })
})
