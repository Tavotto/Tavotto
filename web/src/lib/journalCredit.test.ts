import { describe, expect, it } from 'vitest'
import { CREDIT_MAX, cleanCredit } from './journalCredit'

/** 与引擎 `contactsend.clean_credit` 同一条规则；两侧各跑同一张表（`tests/test_contact_send.py` 的署名用例） */
describe('cleanCredit', () => {
  it.each([
    ['李明', '李明'],
    ['  Ｌｉ Ming  ', 'Li Ming'], // NFKC + 去首尾空白
    ['x'.repeat(CREDIT_MAX), 'x'.repeat(CREDIT_MAX)],
    ['Li Ming-Wei Jr.', 'Li Ming-Wei Jr.'],
    ['𠮷'.repeat(32), '𠮷'.repeat(32)], // 按码点数，不按 UTF-16 单元
  ])('收：%j', (raw, out) => expect(cleanCredit(raw)).toBe(out))

  it.each([
    '',
    '   ',
    'x'.repeat(CREDIT_MAX + 1),
    '𠮷'.repeat(33),
    'a\nb',
    'a\tb',
    'a‮b', // 从右到左覆盖
    'a​b', // 零宽空格
    'a b',
    'a b',
    'me@example.org',
    'https://evil.example',
    'HTTPS://evil.example',
    'see www.evil.example',
    'WWW.evil.example',
    'ｗｗｗ.evil.example', // NFKC 之后是 www.
  ])('拒：%j', (raw) => expect(cleanCredit(raw)).toBeNull())

  it('不是字符串一律拒', () => {
    for (const v of [null, undefined, 3, {}, ['a']]) expect(cleanCredit(v)).toBeNull()
  })
})
