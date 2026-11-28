/** 激活码胶囊的码规整（2026-11-28）：粘进来什么都认，交给 activate() 的只有标准写法 */
import { describe, expect, it } from 'vitest'
import { formatCode, normalizeCode } from './CodeCapsule'

describe('normalizeCode', () => {
  it('带前缀、短横、空格、小写都认成 12 位本体', () => {
    expect(normalizeCode('TVK-7Q2M-K9XD-4HRT')).toBe('7Q2MK9XD4HRT')
    expect(normalizeCode('  tvk 7q2m k9xd 4hrt\n')).toBe('7Q2MK9XD4HRT')
    expect(normalizeCode('7Q2M-K9XD-4HRT')).toBe('7Q2MK9XD4HRT')
  })
  it('本体自己以 TVK 开头时不误删（没有分隔符、也不超长）', () => {
    expect(normalizeCode('TVKA')).toBe('TVKA')
    expect(normalizeCode('TVKAAAABBBBC')).toBe('TVKAAAABBBBC')
  })
  it('超过 12 位截断；只有前缀时为空', () => {
    expect(normalizeCode('AAAABBBBCCCCDDDD')).toBe('AAAABBBBCCCC')
    expect(normalizeCode('TVK-')).toBe('')
  })
  it('formatCode 给出 TVK-XXXX-XXXX-XXXX', () => {
    expect(formatCode('7Q2MK9XD4HRT')).toBe('TVK-7Q2M-K9XD-4HRT')
  })
})
