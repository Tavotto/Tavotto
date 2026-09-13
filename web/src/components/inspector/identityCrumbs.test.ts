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
import { displayLabel, identityCrumbs, untruncatedLabel } from './identityCrumbs'

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

describe('身份头标题不带引擎的截断省略号（2026-09-12 critique P3）', () => {
  it('名字以 …” 收尾且有完整文字时，引号里换成完整文字，角色前缀原样', () => {
    expect(untruncatedLabel('X 轴 “Reaction time (mi…”', 'Reaction time (min)')).toBe(
      'X 轴 “Reaction time (min)”',
    )
  })

  it('换出来的名字仍过得了 engineLabel：英文界面翻结构、留文字', async () => {
    await inLocale('en-US', () => {
      const full = untruncatedLabel('标题 “Reaction kinetics of…”', 'Reaction kinetics of catalysis')
      expect(identityCrumbs('F', undefined, full, 1)).toEqual(['F', 'Title “Reaction kinetics of catalysis”'])
    })
  })

  it('没截过的名字、没有 text 字段、或文字是空白：原样返回', () => {
    expect(untruncatedLabel('标题 “Reaction kinetics”', 'Reaction kinetics')).toBe('标题 “Reaction kinetics”')
    expect(untruncatedLabel('X 轴 “Reaction time (mi…”', undefined)).toBe('X 轴 “Reaction time (mi…”')
    expect(untruncatedLabel('X 轴 “Reaction time (mi…”', '   ')).toBe('X 轴 “Reaction time (mi…”')
  })

  it('mathtext 里的 $ 原样进标题：$$ 不并成一个、$& 不换成被截的旧名', () => {
    expect(untruncatedLabel('标题 “Rate $k_1$ vs $k_…”', 'Rate $k_1$ vs $k_2$ $$ $& done')).toBe(
      '标题 “Rate $k_1$ vs $k_2$ $$ $& done”',
    )
  })

  it('完整文字里的换行折成空格（引擎给名字时也是这么做的）', () => {
    expect(untruncatedLabel('文字 “first line second l…”', 'first line\nsecond line')).toBe(
      '文字 “first line second line”',
    )
  })
})

describe('displayLabel：mathtext → 可读文本（审计 B48）', () => {
  it('图例标签里的单位：字体命令剥掉、上标换成字符', () => {
    expect(displayLabel('Catalyst (k = 0.125 $\\mathrm{min^{-1}}$)')).toBe('Catalyst (k = 0.125 min⁻¹)')
    expect(displayLabel('Current density (A cm$^{-2}$)')).toBe('Current density (A cm⁻²)')
  })

  it('希腊字母与下标', () => {
    expect(displayLabel('$\\alpha$-Fe$_2$O$_3$')).toBe('α-Fe₂O₃')
    expect(displayLabel('$\\Delta T$ (K)')).toBe('ΔT (K)')
  })

  it('换不成字符的上下标与不认识的命令原样保留；`$` 不成对整段不动', () => {
    expect(displayLabel('$T_{\\mathrm{c}}$')).toBe('T_{c}')
    expect(displayLabel('$\\frac{a}{b}$')).toBe('\\frac{a}{b}')
    expect(displayLabel('price $5')).toBe('price $5')
  })

  it('没有 `$` 的原样返回（含引擎给的角色前缀）', () => {
    expect(displayLabel('曲线 “Blank (k = 0.042 $\\mathrm{min^{-1}}$)”')).toBe('曲线 “Blank (k = 0.042 min⁻¹)”')
    expect(displayLabel('标题 “Reaction kinetics”')).toBe('标题 “Reaction kinetics”')
  })
})
