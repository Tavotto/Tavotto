import { describe, expect, it } from 'vitest'
import type { EditableField } from '@/lib/api'
import { controlKindOf, fieldVisible, isPercentField, presentFields } from './registry'

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
    const b = presentFields('line', fields, opts([], { marker: 'o' }))
    expect(b.primary.map((x) => x.field.prop)).toEqual([
      'color', 'linewidth', 'linestyle', 'marker', 'markersize',
    ])
    expect(b.more.map((x) => x.field.prop)).toEqual(['alpha', 'visible'])
    expect(b.advanced.map((x) => x.field.prop)).toEqual(['zorder'])
  })

  it('line：标记为无（None / none / 空串）时标记大小与填充 / 描边色收起；改过的照样在（审计 T15）', () => {
    const fields = [
      f('marker', 'enum'), f('markersize'),
      f('markerfacecolor', 'color'), f('markeredgecolor', 'color'),
    ]
    for (const none of ['None', 'none', '']) {
      const b = presentFields('line', fields, opts([], { marker: none }))
      expect(b.primary.map((x) => x.field.prop), none).toEqual(['marker'])
    }
    const on = presentFields('line', fields, opts([], { marker: 's' }))
    expect(on.primary.map((x) => x.field.prop)).toEqual([
      'marker', 'markersize', 'markerfacecolor', 'markeredgecolor',
    ])
    const orphan = presentFields('line', fields, opts(['markersize'], { marker: 'None' }))
    expect(orphan.primary.map((x) => x.field.prop)).toEqual(['marker', 'markersize'])
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

  it('ticks：次刻度关着时长度 / 线宽 / 方式 / 间距 / 格式都收起；开了才出；改过的照样在（审计 T13）', () => {
    const fields = [
      f('minor_visible', 'bool'), f('minor_length'), f('minor_width'),
      f('minor_mode', 'enum'), f('minor_step'), f('minor_format', 'enum'),
    ]
    const off = presentFields('ticks', fields, opts([], { minor_visible: false, minor_mode: 'step' }))
    expect([...off.primary, ...off.more].map((x) => x.field.prop)).toEqual(['minor_visible'])
    const on = presentFields('ticks', fields, opts([], { minor_visible: true, minor_mode: 'step' }))
    expect([...on.primary, ...on.more].map((x) => x.field.prop)).toEqual(
      expect.arrayContaining(['minor_length', 'minor_width', 'minor_mode', 'minor_step', 'minor_format']),
    )
    // 次刻度开着但方式不是 step：间距仍收起
    const auto = presentFields('ticks', fields, opts([], { minor_visible: true, minor_mode: 'auto' }))
    expect([...auto.primary, ...auto.more].map((x) => x.field.prop)).not.toContain('minor_step')
    // 改过的必须能看到
    const kept = presentFields('ticks', fields, opts(['minor_length'], { minor_visible: false }))
    expect([...kept.primary, ...kept.more].map((x) => x.field.prop)).toContain('minor_length')
  })

  it('fieldVisible 与 presentFields 是同一条判据（刻度卡不走桶也问它）', () => {
    const o = opts([], { minor_visible: false, major_mode: 'auto' })
    expect(fieldVisible('ticks', 'minor_length', o)).toBe(false)
    expect(fieldVisible('ticks', 'major_step', o)).toBe(false)
    expect(fieldVisible('ticks', 'length', o)).toBe(true)
    expect(fieldVisible('ticks', 'minor_length', opts(['minor_length'], { minor_visible: false }))).toBe(true)
    expect(fieldVisible('ticks', 'minor_length', opts([], { minor_visible: true }))).toBe(true)
    // 没建档的角色 / 没条件的字段：一律显示
    expect(fieldVisible('some_role', 'anything', o)).toBe(true)
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

describe('图内文字的背景 / 描边：开关 + 从属字段（审计 T14）', () => {
  const textFields = () => [
    f('text', 'text'),
    f('fontsize'),
    f('bbox_visible', 'bool', '背景'),
    f('bbox_facecolor', 'color', '背景'),
    f('bbox_alpha', 'number', '背景'),
    f('bbox_edgecolor', 'color', '背景'),
    f('bbox_linewidth', 'number', '背景'),
    f('bbox_pad', 'number', '背景'),
    f('bbox_rounded', 'bool', '背景'),
    f('stroke_enabled', 'bool', '描边'),
    f('stroke_color', 'color', '描边'),
    f('stroke_width', 'number', '描边'),
  ]
  const present = (role: string, values: Record<string, unknown>, overridden: string[] = []) =>
    presentFields(role, textFields(), opts(overridden, values))
  const primaryProps = (role: string, values: Record<string, unknown>, overridden: string[] = []) =>
    present(role, values, overridden).primary.map((x) => x.field.prop)
  const moreProps = (role: string, values: Record<string, unknown>, overridden: string[] = []) =>
    present(role, values, overridden).more.map((x) => x.field.prop)

  // 背景在首屏（紧跟字体行，2026-09-11），描边仍在「更多」
  it.each(['title', 'text', 'axis_label', 'legend_text'])(
    '%s：开关关着时从属字段收起，首屏只剩背景开关、「更多」只剩描边开关',
    (role) => {
      expect(primaryProps(role, { bbox_visible: false, stroke_enabled: false })).toEqual([
        'text',
        'fontsize',
        'bbox_visible',
      ])
      expect(moreProps(role, { bbox_visible: false, stroke_enabled: false })).toEqual([
        'stroke_enabled',
      ])
    },
  )

  it('背景开了铺背景的参数，描边仍收着；反之亦然', () => {
    expect(primaryProps('title', { bbox_visible: true, stroke_enabled: false })).toEqual([
      'text', 'fontsize', 'bbox_visible', 'bbox_facecolor', 'bbox_alpha', 'bbox_edgecolor',
      'bbox_linewidth', 'bbox_pad', 'bbox_rounded',
    ])
    expect(moreProps('title', { bbox_visible: true, stroke_enabled: false })).toEqual(['stroke_enabled'])
    expect(moreProps('title', { bbox_visible: false, stroke_enabled: true })).toEqual([
      'stroke_enabled', 'stroke_color', 'stroke_width',
    ])
  })

  it('用户改过的从属字段照旧显示（override 不因折叠而不可发现）', () => {
    expect(primaryProps('title', { bbox_visible: false, stroke_enabled: false }, ['bbox_pad'])).toEqual([
      'text', 'fontsize', 'bbox_visible', 'bbox_pad',
    ])
  })

  it('开关的控件形态是 effect（关着画「＋添加」），别的 bool 仍是开关', () => {
    expect(controlKindOf('title', f('bbox_visible', 'bool'))).toBe('effect')
    expect(controlKindOf('legend_text', f('stroke_enabled', 'bool'))).toBe('effect')
    expect(controlKindOf('title', f('bbox_rounded', 'bool'))).toBe('toggle')
    expect(controlKindOf('title', f('visible', 'bool'))).toBe('toggle')
  })
})

describe('controlKindOf：透明度按百分比（审计 T16 / T20）', () => {
  it('alpha / framealpha / grid_alpha / bbox_alpha 的数值字段分派到 percent', () => {
    for (const prop of ['alpha', 'framealpha', 'grid_alpha', 'bbox_alpha']) {
      expect(controlKindOf('line', f(prop))).toBe('percent')
      expect(isPercentField(f(prop))).toBe(true)
    }
  })

  it('不按取值范围猜：别的 0–1 数值字段仍是普通数字，非数值的 alpha 也不是', () => {
    expect(controlKindOf('line', f('linewidth'))).toBe('number')
    expect(controlKindOf('legend', { ...f('handlelength'), min: 0, max: 1 })).toBe('number')
    expect(controlKindOf('line', f('alpha', 'text'))).toBe('text')
  })
})
