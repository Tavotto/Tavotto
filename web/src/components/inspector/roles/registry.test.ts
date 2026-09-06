/**
 * 属性显示注册表：引擎只给英文 prop 名与中文散文，显示名与顺序全在这一层。
 *
 * 漏一条的表现很轻微也很难被发现——属性页里冒出一行 `spine_bottom_linewidth`
 * 或者一个写着 `logit` 的下拉项，功能全对，只是没人看得懂；换成英文界面之后
 * 还会冒出中文。所以这里把**引擎当前会发出来的那些 prop / enum / 分组 / 元素名**
 * 抄一份，对着**两种语言**逐条查，而不是靠肉眼。
 *
 * 这份清单要跟着 `engine/manifest.py` 的字段表走：加了新属性却忘了补翻译，
 * 这条用例会红。
 */
import { describe, expect, it } from 'vitest'

import { setLocale } from '@/i18n'
import enInspector from '@/i18n/locales/en-US/inspector.json'
import zhInspector from '@/i18n/locales/zh-CN/inspector.json'
import { engineLabel, groupLabel, groupRank, optionLabel, propLabel, roleName } from './registry'

/** 引擎会发出来的 prop → 它属于哪个角色（只列需要显示名的那些） */
const ENGINE_PROPS: [string, string][] = [
  // axes · 数据范围
  ['xlim', 'axes'], ['ylim', 'axes'], ['xscale', 'axes'], ['yscale', 'axes'],
  ['invert_x', 'axes'], ['invert_y', 'axes'], ['aspect', 'axes'],
  // axes · 网格与边框
  ['grid_x', 'axes'], ['grid_y', 'axes'], ['grid_color', 'axes'],
  ['grid_linestyle', 'axes'], ['grid_linewidth', 'axes'], ['grid_alpha', 'axes'],
  ['spine_top', 'axes'], ['spine_right', 'axes'], ['spine_bottom', 'axes'],
  ['spine_left', 'axes'], ['spine_color', 'axes'], ['spine_linewidth', 'axes'],
  // axes · 边框（逐条）
  ['spine_top_color', 'axes'], ['spine_top_linewidth', 'axes'],
  ['spine_right_color', 'axes'], ['spine_right_linewidth', 'axes'],
  ['spine_bottom_color', 'axes'], ['spine_bottom_linewidth', 'axes'],
  ['spine_left_color', 'axes'], ['spine_left_linewidth', 'axes'],
  // ticks · 刻度线与刻度定位
  ['direction', 'ticks'], ['length', 'ticks'], ['width', 'ticks'], ['format', 'ticks'],
  ['major_mode', 'ticks'], ['major_step', 'ticks'], ['major_values', 'ticks'],
  ['minor_visible', 'ticks'], ['minor_mode', 'ticks'], ['minor_step', 'ticks'],
  ['minor_format', 'ticks'],
  // colorbar
  ['orientation', 'colorbar'], ['extend', 'colorbar'], ['cmap', 'colorbar'],
  ['vmin', 'colorbar'], ['vmax', 'colorbar'], ['tick_fontsize', 'colorbar'],
  ['tick_color', 'colorbar'], ['outline_visible', 'colorbar'],
  ['outline_width', 'colorbar'],
  // patch（脚本 add_patch 的独立形状）
  ['facecolor', 'patch'], ['edgecolor', 'patch'], ['linewidth', 'patch'],
  ['linestyle', 'patch'], ['fill', 'patch'], ['alpha', 'patch'], ['zorder', 'patch'],
]

/**
 * enum 字段 → 引擎会给出的选项（同样抄自 manifest 的字段表）。
 *
 * **这张表一度漏掉了 linestyle / marker 那两族**——于是「每个枚举选项都有
 * 显示名」在整整一年里都是绿的，而线型 `:` 在界面上显示的就是一个冒号
 * （审计 T15）。手抄的清单天生是子集，所以下面另有一条**反向**用例：
 * 凡是翻译表里登记过显示名的枚举值，查出来必须**就是**那个显示名。
 */
const ENGINE_ENUMS: [string, string[]][] = [
  ['xscale', ['linear', 'log', 'symlog', 'logit']],
  ['yscale', ['linear', 'log', 'symlog', 'logit']],
  ['major_mode', ['auto', 'step', 'fixed']],
  ['minor_mode', ['auto', 'step']],
  ['format', ['auto', 'sci']],
  ['minor_format', ['none', 'auto', 'sci']],
  ['orientation', ['vertical', 'horizontal']],
  ['extend', ['neither', 'both', 'min', 'max']],
  // `_line_fields` / `_collection_fields` / `_patch_fields` 的线型与标记
  ['linestyle', ['-', '--', '-.', ':']],
  ['grid_linestyle', ['-', '--', '-.', ':']],
  ['marker', ['None', 'o', 's', 'D', '^', 'v', '<', '>', 'x', '+', '*', '.', 'p', 'h']],
]

/**
 * 引擎发过来的分组字面量（manifest 的 `group` 字段）。
 *
 * **同样是手抄的子集**，而且同样漏过：`线条与填充`（散点 / 填充族的线型与
 * 花纹）与 `标记`（stem 图）当时不在表里，于是它们在英文界面上会原样冒出
 * 中文标题——`groupLabel` 查不到就原样透出，那条回退对**脚本自定义**的分组
 * 是对的，对引擎自己发的分组是漏译。取值的真源是
 * `src/tavotto/engine/manifest.py` 里所有 `"group"` 常量（AST 扫一遍就有），
 * 跨语言没有自动同步的门禁——加新分组时两边都要动。
 */
const ENGINE_GROUPS = [
  '位置与尺寸', '视角', '数据范围', '坐标轴', '轴箭头', '刻度', '刻度线',
  '刻度定位', '网格与边框', '边框（逐条）', '线条与标记', '线条与填充',
  '标记', '渐变填充', '颜色映射', '文字', '排版', '背景', '描边', '图例',
  '图例项', '样式', '布局', '排列', '高级',
]

const HAN = /[一-鿿]/

/** 某个语言的枚举译文表——判据的集合边界取自这里，不手抄 */
const enumTable = (locale: 'zh-CN' | 'en-US'): Record<string, Record<string, string>> =>
  (locale === 'zh-CN' ? zhInspector : enInspector).enum as Record<string, Record<string, string>>

/** 切到某个语言跑一段断言，跑完必定切回来（vitest 把默认语言钉在 zh-CN） */
async function inLocale(locale: 'zh-CN' | 'en-US', fn: () => void) {
  await setLocale(locale)
  try {
    fn()
  } finally {
    await setLocale('zh-CN')
  }
}

describe.each(['zh-CN', 'en-US'] as const)('%s', (locale) => {
  it('每一条引擎属性都有显示名，不是英文原名', async () => {
    await inLocale(locale, () => {
      for (const [prop, role] of ENGINE_PROPS) {
        const label = propLabel(prop, role)
        expect(label, `${prop}（${role}）还在显示英文原名`).not.toBe(prop)
        expect(label).not.toBe('')
      }
    })
  })

  it('每个枚举选项都有显示名', async () => {
    const table = enumTable(locale)
    await inLocale(locale, () => {
      for (const [prop, values] of ENGINE_ENUMS) {
        for (const v of values) {
          // 判据是**登记过而且查得出来**，不是「与原值不同」：英文界面下
          // `marker.None` 的译文就是 "None"，拿「不等于原值」当判据会把一条
          // 正确的译文报成漏翻（而真正的漏翻是查表落空、原样回退）。
          const registered = table[prop]?.[v]
          expect(registered, `${prop}.${v} 没登记显示名`).toBeTruthy()
          expect(optionLabel(prop, v), `${prop}.${v} 查不出登记的显示名`).toBe(registered)
        }
      }
    })
  })

  /**
   * 反向门禁：**翻译表是这条判据的集合边界**，不是手抄的清单。
   *
   * 抓的是「登记了却查不出来」——键里带 i18next 的分隔符时（线型的 `:` 是
   * 命名空间分隔符）查表会静默落空，回退成原始代码。手抄的 ENGINE_ENUMS
   * 看不见这一类：它只列了几个安全的英文单词。
   */
  it('翻译表里登记过的枚举值，查出来就是那条译文（键里带分隔符也不例外）', async () => {
    const table = enumTable(locale)
    await inLocale(locale, () => {
      const broken: string[] = []
      for (const [prop, values] of Object.entries(table)) {
        for (const [value, expected] of Object.entries(values)) {
          if (typeof expected !== 'string') continue
          const got = optionLabel(prop, value)
          if (got !== expected) broken.push(`${prop}[${JSON.stringify(value)}] → ${JSON.stringify(got)}`)
        }
      }
      expect(broken, '这些枚举值登记了译文却查不出来').toEqual([])
    })
  })

  it('每个分组都有显示名——没登记的会原样透出引擎那串中文', async () => {
    await inLocale(locale, () => {
      for (const g of ENGINE_GROUPS) {
        const label = groupLabel(g)
        expect(label, `分组「${g}」没登记`).not.toBe('')
        if (locale === 'en-US') {
          expect(label, `分组「${g}」在英文界面下漏译`).not.toMatch(HAN)
        }
      }
    })
  })

  it('新元素名「形状 N」有译文，序号原样带过去', async () => {
    await inLocale(locale, () => {
      const label = engineLabel('形状 3')
      expect(label).toContain('3')
      if (locale === 'en-US') expect(label).not.toMatch(HAN)
    })
  })

  it('双生轴标签「子图 N（右轴）」两种语言都拆得开，同侧序号原样带过去', async () => {
    await inLocale(locale, () => {
      const plain = engineLabel('子图 2（右轴）')
      const ordinal = engineLabel('子图 1（右轴 2）')
      const top = engineLabel('子图 3（上轴）')
      if (locale === 'en-US') {
        expect(plain).not.toMatch(HAN)
        expect(plain).toContain('2')
        expect(plain).toContain('right axis')
        expect(ordinal).toContain('right axis 2')
        expect(top).toContain('top axis')
      } else {
        // 中文界面下是恒等映射：引擎原串就是译文
        expect(plain).toBe('子图 2（右轴）')
        expect(ordinal).toBe('子图 1（右轴 2）')
        expect(top).toBe('子图 3（上轴）')
      }
    })
  })

  it('刻度组叫「轴刻度」（刻度线 + 文字都在它身上），不再叫「刻度文字」（审计 T13）', async () => {
    await inLocale(locale, () => {
      const y = engineLabel('Y 刻度文字')
      if (locale === 'en-US') {
        expect(y).not.toMatch(HAN)
        expect(y).toBe('Y axis ticks')
      } else {
        expect(y).toBe('Y 轴刻度')
      }
      expect(engineLabel('Z 刻度文字')).toContain('Z')
    })
  })

  it('新角色 patch 有名字', async () => {
    await inLocale(locale, () => {
      const name = roleName('patch')
      expect(name).not.toBe('')
      if (locale === 'en-US') expect(name).not.toMatch(HAN)
    })
  })
})

describe('中文界面下的具体措辞', () => {
  it('facecolor：图元自己的填充叫「填充色」，figure/axes 的背景才叫「背景色」', () => {
    for (const role of ['bar', 'bar_series', 'scatter', 'fill', 'patch']) {
      expect(propLabel('facecolor', role), role).toBe('填充色')
    }
    expect(propLabel('facecolor', 'figure')).toBe('背景色')
    expect(propLabel('facecolor', 'axes')).toBe('背景色')
  })

  it('刻度的「自动」要说明白是回到脚本原样，别让人以为我们另挑了一套', () => {
    expect(optionLabel('major_mode', 'auto')).toContain('脚本原样')
    expect(optionLabel('format', 'auto')).toContain('脚本原样')
    expect(optionLabel('minor_format', 'auto')).toContain('脚本原样')
  })

  it('次刻度默认「不标数字」——那是常态，不是一个异常档', () => {
    expect(optionLabel('minor_format', 'none')).toBe('不标数字')
  })

  it('格式串保持原文（%.1f 这类是 matplotlib 的标识符，翻译反而对不上文档）', () => {
    expect(optionLabel('format', '%.2f')).toBe('%.2f')
    expect(optionLabel('minor_format', '%g')).toBe('%g')
  })
})

describe('分组顺序', () => {
  it('引擎用到的每个分组都在排序表里（不在的话会被甩到最后）', () => {
    const last = groupRank('这个分组不存在')
    for (const g of ENGINE_GROUPS) {
      expect(groupRank(g), `分组「${g}」没进排序表`).toBeLessThan(last)
    }
  })

  it('逐条边框紧跟「网格与边框」，刻度定位紧跟「刻度线」', () => {
    expect(groupRank('边框（逐条）')).toBe(groupRank('网格与边框') + 1)
    expect(groupRank('刻度定位')).toBe(groupRank('刻度线') + 1)
  })

  it('排序按**引擎名**而不是显示名——否则换英文界面版面顺序会跟着字母序漂', async () => {
    const zh = ENGINE_GROUPS.map(groupRank)
    await inLocale('en-US', () => {
      expect(ENGINE_GROUPS.map(groupRank)).toEqual(zh)
    })
  })
})
