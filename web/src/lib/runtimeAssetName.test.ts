import { describe, expect, it } from 'vitest'
import { runtimeAssetName } from './api'

// ADR 0159：同名不同脚本、分开登记的图在界面上显示原图名，身份仍是 stem
describe('runtimeAssetName', () => {
  it('shows the raw saved name for a split figure', () => {
    expect(runtimeAssetName({ stem: 'sensitivity@plot_b', display_name: 'sensitivity' })).toBe('sensitivity')
  })
  it('falls back to the stem when there is no display name', () => {
    expect(runtimeAssetName({ stem: 'sensitivity' })).toBe('sensitivity')
    expect(runtimeAssetName({ stem: 'x', display_name: '' })).toBe('x')
  })
})
