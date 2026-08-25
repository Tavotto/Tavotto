import { describe, expect, it } from 'vitest'
import type { EditableField } from '@/lib/api'
import { controlKindOf, presentFields } from './registry'

const f = (prop: string, type: EditableField['type'] = 'number', group?: string): EditableField =>
  ({ prop, type, value: 0, ...(group ? { group } : {}) }) as EditableField

const opts = (overridden: string[] = [], values: Record<string, unknown> = {}) => ({
  isOverridden: (p: string) => overridden.includes(p),
  read: (p: string) => values[p],
})

describe('presentFields：角色模板分桶', () => {
  it('line：颜色/线宽/线型/marker 在首屏，alpha 在更多，zorder 在高级', () => {
    const fields = [
      f('color', 'color'), f('linewidth'), f('linestyle', 'enum'),
      f('marker', 'enum'), f('markersize'), f('alpha'),
      f('zorder', 'number', '排列'), f('visible', 'bool'),
    ]
    const b = presentFields('line', fields, opts())
    expect(b.primary.map((x) => x.field.prop)).toEqual([
      'color', 'linewidth', 'linestyle', 'marker', 'markersize',
    ])
    expect(b.more.map((x) => x.field.prop)).toEqual(['alpha', 'visible'])
    expect(b.advanced.map((x) => x.field.prop)).toEqual(['zorder'])
  })

  it('manifest 没有的属性绝不发明：模板点名而字段缺席的不出现', () => {
    const b = presentFields('line', [f('color', 'color')], opts())
    expect(b.primary.map((x) => x.field.prop)).toEqual(['color'])
    expect(b.more).toHaveLength(0)
  })

  it('模板没点名的未知字段进「更多」，不丢失', () => {
    const fields = [f('color', 'color'), f('mystery_prop'), f('grouped_mystery', 'number', '样式')]
    const b = presentFields('line', fields, opts())
    const all = [...b.primary, ...b.more, ...b.advanced].map((x) => x.field.prop)
    expect(all).toContain('mystery_prop')
    expect(all).toContain('grouped_mystery')
    expect(b.more.map((x) => x.field.prop)).toEqual(
      expect.arrayContaining(['mystery_prop', 'grouped_mystery']),
    )
  })

  it('未建档角色：无 group 平铺进首屏、有 group 进更多、「高级/排列」组进高级', () => {
    const fields = [
      f('foo'), f('bar', 'color'),
      f('styled', 'number', '样式'),
      f('deep', 'number', '高级'),
    ]
    const b = presentFields('some_future_role', fields, opts())
    expect(b.primary.map((x) => x.field.prop)).toEqual(['foo', 'bar'])
    expect(b.more.map((x) => x.field.prop)).toEqual(['styled'])
    expect(b.advanced.map((x) => x.field.prop)).toEqual(['deep'])
  })

  it('字段一进一出：三个桶的并集 == 输入（无条件显示时）', () => {
    const fields = [
      f('a'), f('b', 'color'), f('c', 'enum'), f('d', 'bool', '样式'),
      f('e', 'number', '高级'),
    ]
    const b = presentFields('another_unknown', fields, opts())
    const all = [...b.primary, ...b.more, ...b.advanced]
    expect(all).toHaveLength(fields.length)
  })

  it('ticks：major_step 只在 step 模式显示；改过的即使模式不符也显示', () => {
    const fields = [f('major_mode', 'enum'), f('major_step'), f('major_values', 'number_list')]
    const auto = presentFields('ticks', fields, opts([], { major_mode: 'auto' }))
    expect(auto.primary.map((x) => x.field.prop)).toEqual(['major_mode'])

    const step = presentFields('ticks', fields, opts([], { major_mode: 'step' }))
    expect(step.primary.map((x) => x.field.prop)).toEqual(['major_mode', 'major_step'])

    // override 存在时条件让路：不能因隐藏而不可发现
    const orphan = presentFields('ticks', fields, opts(['major_values'], { major_mode: 'auto' }))
    expect(orphan.primary.map((x) => x.field.prop)).toContain('major_values')
  })

  it('axes：裸 position rect 进高级（manifest-first 泄漏，审计 P6）', () => {
    const fields = [f('position', 'rect'), f('xlim', 'pair'), f('grid_x', 'bool')]
    const b = presentFields('axes', fields, opts())
    expect(b.advanced.map((x) => x.field.prop)).toEqual(['position'])
    expect(b.primary.map((x) => x.field.prop)).toEqual(['xlim', 'grid_x'])
  })
})

describe('controlKindOf：enum 不再无条件落成 Select', () => {
  it('线型 / marker / hatch / cmap / 字体 / 箭头样式各归各的视觉控件', () => {
    expect(controlKindOf('line', f('linestyle', 'enum'))).toBe('line-style')
    expect(controlKindOf('axes', f('grid_linestyle', 'enum'))).toBe('line-style')
    expect(controlKindOf('line', f('marker', 'enum'))).toBe('marker')
    expect(controlKindOf('patch', f('hatch', 'enum'))).toBe('hatch')
    expect(controlKindOf('image', f('cmap', 'enum'))).toBe('colormap')
    expect(controlKindOf('title', f('fontfamily', 'enum'))).toBe('font')
    expect(controlKindOf('arrow', f('arrowstyle', 'enum'))).toBe('arrow-style')
  })

  it('图例 loc 是角色专属：legend 走 3×3 网格，别的角色回落 Select', () => {
    expect(controlKindOf('legend', f('loc', 'enum'))).toBe('legend-position')
    expect(controlKindOf('some_role', f('loc', 'enum'))).toBe('select')
  })

  it('未知 enum 回落成带标签的 Select；基础类型按类型走', () => {
    expect(controlKindOf('line', f('exotic_enum', 'enum'))).toBe('select')
    expect(controlKindOf('line', f('linewidth', 'number'))).toBe('number')
    expect(controlKindOf('line', f('color', 'color'))).toBe('color')
    expect(controlKindOf('axes', f('grid_x', 'bool'))).toBe('toggle')
    expect(controlKindOf('legend', f('entry_order', 'order'))).toBe('order')
  })

  it('prop 名撞车但类型不是 enum 时不误判：数值型的 marker 仍是 number', () => {
    expect(controlKindOf('x', f('marker', 'number'))).toBe('number')
  })
})
