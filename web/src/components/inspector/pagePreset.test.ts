import { describe, expect, it } from 'vitest'
import { activePreset, pagePresets } from './pagePreset'

const cols = [
  { id: 'col1', w: 57, label: { zh: '1 栏', en: '1 column' } },
  { id: 'col2', w: 121, label: { zh: '2 栏', en: '2 columns' } },
]

describe('activePreset', () => {
  it('没按期刊：宽高都对上才算（第一档 60 高）', () => {
    const presets = pagePresets(cols)
    expect(activePreset(presets, { w: 57, h: 60 }, false)?.id).toBe('col1')
    expect(activePreset(presets, { w: 57, h: 100 }, false)).toBeUndefined()
  })

  it('按期刊选的：期刊的栏只看宽度，选栏保持当前高度', () => {
    const presets = pagePresets(cols, 77)
    expect(presets.filter((p) => p.column).every((p) => p.h === 77)).toBe(true)
    expect(activePreset(presets, { w: 121, h: 33 }, true)?.id).toBe('col2')
    expect(activePreset(presets, { w: 121.02, h: 77 }, true)?.id).toBe('col2')
    // 整页 / 正方形这两档仍要宽高都对上
    expect(activePreset(presets, { w: 180, h: 240 }, true)?.id).toBe('full')
    expect(activePreset(presets, { w: 180, h: 100 }, true)).toBeUndefined()
  })
})
