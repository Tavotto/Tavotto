/**
 * 属性页顶部那条「面板 / 子图 / 元素」面包屑。
 *
 * 为什么单独看护：引擎发来的 `label` 是**中文散文**（`子图 1` /
 * `标题 “Reaction kinetics”`），必须过 `engineLabel` 才是当前语言。
 * 元素树一直这么做，这条面包屑曾经直接用了原串——于是英文界面下
 * 一选中元素，右栏标题就冒出中文，而画面其余部分全是英文。
 *
 * `pnpm i18n:check` 拦不住这一类：它查的是 key 与译文，而这里漏的是
 * **运行时数据**没过翻译函数，一个 key 都没少。
 */
import { describe, expect, it } from 'vitest'

import { setLocale } from '@/i18n'
import { identityCrumbs } from './identityCrumbs'

async function inLocale(locale: 'zh-CN' | 'en-US', fn: () => void) {
  await setLocale(locale)
  try {
    fn()
  } finally {
    await setLocale('zh-CN')
  }
}

describe('属性页面包屑', () => {
  it('中文界面：引擎原串就是显示串', async () => {
    await inLocale('zh-CN', () => {
      expect(identityCrumbs('Fig1_kinetics', '子图 1', '标题 “Reaction kinetics”', 1)).toEqual([
        'Fig1_kinetics',
        '子图 1',
        '标题 “Reaction kinetics”',
      ])
    })
  })

  it('英文界面：结构部分翻成英文，引号里的用户文字一个字不动', async () => {
    await inLocale('en-US', () => {
      const crumbs = identityCrumbs('Fig1_kinetics', '子图 1', '标题 “Reaction kinetics”', 1)
      expect(crumbs[0]).toBe('Fig1_kinetics')
      for (const c of crumbs) expect(c, `「${c}」还是中文`).not.toMatch(/[一-鿿]/)
      expect(crumbs.at(-1)).toContain('Reaction kinetics')
    })
  })

  it('多选时最后一段是「选中 N 个」，同样跟着语言走', async () => {
    await inLocale('en-US', () => {
      const crumbs = identityCrumbs('Fig1_kinetics', undefined, undefined, 3)
      expect(crumbs).toHaveLength(2)
      expect(crumbs.at(-1)).not.toMatch(/[一-鿿]/)
      expect(crumbs.at(-1)).toContain('3')
    })
  })

  it('单选却没解析到元素时不摆一个空段', () => {
    expect(identityCrumbs('Fig1_kinetics', undefined, undefined, 1)).toEqual(['Fig1_kinetics'])
  })
})
