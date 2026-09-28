/**
 * `scripts/i18n-args.mjs`：i18n-check 第 7 组「调用点插值实参齐全」认哪些键算传了。
 * 只认顶层键——i18next 插值只看顶层，嵌套对象里的同名键不会填进 `{{product}}`（Codex #695）。
 */
import { describe, expect, it } from 'vitest'
import { topLevelKeys } from '../../scripts/i18n-args.mjs'

const keys = (text?: string) => {
  const r = topLevelKeys(text)
  return r && [...r].sort()
}

describe('topLevelKeys', () => {
  it('顶层的普通键、省略写法、引号键都算传了', () => {
    expect(keys("{ name: target.name, product: PRODUCT_NAME }")).toEqual(['name', 'product'])
    expect(keys("{ name, 'count': n, \"x\": 1 }")).toEqual(['count', 'name', 'x'])
    expect(keys("{ ns: 'project', total: drop.ignored + 1 }")).toEqual(['ns', 'total'])
  })

  it('嵌套对象里的同名键不算；值里碰巧同名的标识符也不算', () => {
    expect(keys('{ meta: { product: X }, name }')).toEqual(['meta', 'name'])
    expect(keys('{ name: product }')).toEqual(['name'])
    expect(keys('{ name: f({ product: 1 }) }')).toEqual(['name'])
  })

  it('没传实参 = 什么都没传', () => {
    expect(keys(undefined)).toEqual([])
    expect(keys('  ')).toEqual([])
    expect(keys('{}')).toEqual([])
  })

  it('看不全的实参不判（null）：变量、调用、三元、展开、计算键', () => {
    for (const text of ['values', 'makeValues()', 'a ? { x } : { y }', '{ ...base, name }', '{ [k]: 1 }']) {
      expect(topLevelKeys(text)).toBeNull()
    }
  })
})
