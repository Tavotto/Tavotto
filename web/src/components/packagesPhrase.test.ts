import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { packagesPhrase } from '@/components/DependencyRepairCard'

afterEach(async () => {
  await setLocale('zh-CN')
})

describe('packagesPhrase：那一句话与进度行说的是计划里真正要装的全部包', () => {
  it('一个 = 名字（去掉版本与 extras）；两个 = 「a 和 b」；更多 = 「a 等 N 个包」', async () => {
    await setLocale('zh-CN')
    expect(packagesPhrase(['openpyxl'])).toBe('openpyxl')
    expect(packagesPhrase(['tabulate[widechars]==0.9.0'])).toBe('tabulate')
    expect(packagesPhrase(['pandas', 'openpyxl'])).toBe('pandas 和 openpyxl')
    expect(packagesPhrase(['numpy', 'pandas', 'openpyxl'])).toBe('numpy 等 3 个包')
  })

  it('英文同理', async () => {
    await setLocale('en-US')
    expect(packagesPhrase(['pandas', 'openpyxl'])).toBe('pandas and openpyxl')
    expect(packagesPhrase(['numpy', 'pandas', 'openpyxl'])).toBe('numpy and others (3 packages)')
  })
})
