import { describe, expect, it } from 'vitest'
import { isTextEffectSwitch, TEXT_EFFECTS, textEffectSwitchOf } from './textEffects'

describe('textEffects：开关与从属字段的表', () => {
  it('两个开关各管一组从属字段，从属字段反查得到开关', () => {
    expect(isTextEffectSwitch('bbox_visible')).toBe(true)
    expect(isTextEffectSwitch('stroke_enabled')).toBe(true)
    expect(isTextEffectSwitch('bbox_facecolor')).toBe(false)
    for (const [sw, deps] of Object.entries(TEXT_EFFECTS)) {
      for (const d of deps) expect(textEffectSwitchOf(d)).toBe(sw)
    }
    expect(textEffectSwitchOf('fontsize')).toBeNull()
  })

  it('从属字段互不重叠：一个字段只归一个开关', () => {
    const all = Object.values(TEXT_EFFECTS).flat()
    expect(new Set(all).size).toBe(all.length)
  })
})
