/** 顶栏「高级版」胶囊（入口方案 B）写什么、什么时候不出现：提醒只走胶囊，已激活且不临近到期时平时不出现。 */
import { describe, expect, it } from 'vitest'

import { PRO_CHIP_WARN_DAYS, proChipText } from './ProChip'

const NOW = Date.UTC(2026, 10, 24)
const inDays = (n: number) => Math.floor((NOW + n * 86_400_000) / 1000)

describe('顶栏胶囊文案', () => {
  it('未激活：「高级版」；过期 / 作废：「已到期」', () => {
    expect(proChipText('none', null, NOW)).toEqual({ kind: 'none' })
    expect(proChipText('expired', inDays(-3), NOW)).toEqual({ kind: 'expired' })
    expect(proChipText('revoked', null, NOW)).toEqual({ kind: 'expired' })
  })
  it('已激活：到期前两周起说剩几天，其余时间不出现', () => {
    expect(proChipText('active', inDays(PRO_CHIP_WARN_DAYS), NOW)).toEqual({ kind: 'soon', days: PRO_CHIP_WARN_DAYS })
    expect(proChipText('active', inDays(9), NOW)).toEqual({ kind: 'soon', days: 9 })
    expect(proChipText('active', inDays(PRO_CHIP_WARN_DAYS + 1), NOW)).toBeNull()
    expect(proChipText('active', inDays(300), NOW)).toBeNull()
  })
  it('需要联网确认（lapsed）等其它状态：不出现', () => {
    expect(proChipText('lapsed', inDays(5), NOW)).toBeNull()
  })
})
