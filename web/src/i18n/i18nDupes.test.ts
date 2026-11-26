/**
 * `scripts/i18n-dupes.mjs`：i18n-check 第 8 组「同一个对象里不许有重复键」。
 * `JSON.parse` 对重复键不报错、后一个盖掉前一个——git 自动合并重复了一整段时没有任何检查会响。
 */
import { describe, expect, it } from 'vitest'
import { duplicateKeys } from '../../scripts/i18n-dupes.mjs'

describe('duplicateKeys', () => {
  it('没有重复键', () => {
    expect(duplicateKeys('{}')).toEqual([])
    expect(duplicateKeys('{"a": "1", "b": {"a": "2", "c": {"a": "3"}}}')).toEqual([])
  })

  it('同一个对象里的重复键按点分路径报，每条只报一次', () => {
    expect(duplicateKeys('{"a": "1", "a": "2", "a": "3"}')).toEqual(['a'])
    expect(duplicateKeys('{"x": {"spec": {"line": "新"}, "other": "o", "spec": {"line": "旧"}}}')).toEqual(['x.spec'])
  })

  it('不同对象里的同名键不算；重复的那一段里面的键也不重复计', () => {
    const text = '{"p": {"k": "1"}, "q": {"k": "2"}, "p": {"k": "3", "k": "4"}}'
    expect(duplicateKeys(text)).toEqual(['p', 'p.k'])
  })

  it('值里的引号、转义、花括号不会把它带偏', () => {
    expect(duplicateKeys('{"a": "说 \\"{b}\\" 和 }", "b": "x: {\\"a\\": 1}", "c": "\\\\"}')).toEqual([])
    expect(duplicateKeys('{"a\\"b": "1", "a\\"b": "2"}')).toEqual(['a"b'])
  })

  it('数组里的对象带下标；数、布尔、null 当值跳过', () => {
    expect(duplicateKeys('{"l": [{"k": 1, "k": 2}, {"k": true}], "n": null, "m": -1.5e3}')).toEqual(['l[0].k'])
  })
})
