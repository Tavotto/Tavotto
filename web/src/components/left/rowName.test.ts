import { describe, expect, it } from 'vitest'
import { splitTypePrefix } from './rowName'

describe('splitTypePrefix', () => {
  it.each([
    ['标题 “Reaction kinetics”', '标题 ', '“Reaction kinetics”'],
    ['X 轴 “Reaction time (min)”', 'X 轴 ', '“Reaction time (min)”'],
    ['曲线 “Catalyst (k = 0.125 min⁻¹)”', '曲线 ', '“Catalyst (k = 0.125 min⁻¹)”'],
    ['图例项 “Blank (k = 0.042 min⁻¹)”', '图例项 ', '“Blank (k = 0.042 min⁻¹)”'],
    ['Title “Reaction kinetics”', 'Title ', '“Reaction kinetics”'],
    ['X axis "time"', 'X axis ', '"time"'],
  ])('%s', (label, prefix, name) => {
    expect(splitTypePrefix(label)).toEqual({ prefix, name })
    expect(prefix + name).toBe(label)
  })

  it('没有引号：整个是名字', () => {
    for (const l of ['子图 1', '整张图', '0.25', 'Axes 1', '']) expect(splitTypePrefix(l)).toEqual({ prefix: '', name: l })
  })

  it('引号不在类型词之后（名字本身以引号开头）：不切', () => {
    expect(splitTypePrefix('“quoted”')).toEqual({ prefix: '', name: '“quoted”' })
  })
})
