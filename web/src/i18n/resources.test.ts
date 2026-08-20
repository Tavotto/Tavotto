/**
 * 两份翻译资源的结构一致性。
 *
 * `scripts/i18n-check.mjs` 在 CI 里也查这几条，但那是**构建期**的检查：它读
 * 磁盘上的 JSON，跑不进 vitest 的失败列表，也覆盖不到「i18next 真的按这份
 * 资源初始化之后会怎样」。这里查的是**运行期实例**——两者互为补充：
 * 检查脚本挡住漏翻进仓库，这批用例挡住「资源没进 bundle / ns 名字写错 /
 * 复数后缀 i18next 不认」这类只有跑起来才暴露的问题。
 */
import { describe, expect, it } from 'vitest'

import { DEFAULT_LOCALE, NAMESPACES, SUPPORTED_LOCALES, i18n, resources } from './index'
import type { Locale, Namespace } from './index'

type Json = { [k: string]: string | Json }

/** 把嵌套资源摊平成 `a.b.c` → 文本 */
function flatten(node: Json, prefix = '', out = new Map<string, string>()): Map<string, string> {
  for (const [k, v] of Object.entries(node)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out.set(key, v)
    else flatten(v, key, out)
  }
  return out
}

const flatNs = (locale: Locale, ns: Namespace) =>
  flatten(resources[locale][ns] as unknown as Json)

/** `{{name}}` / `{{count}}`，同一条里出现多次只算一个 */
const varsOf = (text: string) =>
  new Set(Array.from(text.matchAll(/\{\{\s*([\w.]+)[^}]*\}\}/g), (m) => m[1]))

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/

describe('两份资源的 key 集合完全一致', () => {
  it('命名空间一一对应', () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(Object.keys(resources[locale]).sort()).toEqual([...NAMESPACES].sort())
    }
  })

  /**
   * 比的是**去掉复数后缀之后**的基名。逐字节比 key 会把「中文本来就不该有
   * _one」报成漏翻——形态齐不齐是下面那组复数用例的事。
   */
  const basesOf = (locale: Locale, ns: Namespace) =>
    [...new Set([...flatNs(locale, ns).keys()].map((k) => k.replace(PLURAL_SUFFIX, '')))].sort()

  it.each(NAMESPACES)('%s：中英文 key 一个不多一个不少', (ns) => {
    const zh = basesOf('zh-CN', ns)
    const en = basesOf('en-US', ns)
    expect(en.filter((k) => !zh.includes(k))).toEqual([]) // 英文多出来的
    expect(zh.filter((k) => !en.includes(k))).toEqual([]) // 英文缺的
  })

  it.each(NAMESPACES)('%s：没有空翻译（空串等于什么都没翻，界面上是个空按钮）', (ns) => {
    for (const locale of SUPPORTED_LOCALES) {
      const empty = [...flatNs(locale, ns)].filter(([, v]) => v.trim() === '').map(([k]) => k)
      expect(empty).toEqual([])
    }
  })

  it('总量不为零——资源没进 bundle 时上面几条会全绿地空转', () => {
    const total = NAMESPACES.reduce((n, ns) => n + flatNs(DEFAULT_LOCALE, ns).size, 0)
    expect(total).toBeGreaterThan(1000)
  })
})

describe('插值变量一致', () => {
  it.each(NAMESPACES)('%s：同一条 key 的 {{变量}} 两种语言必须相同', (ns) => {
    const zh = flatNs('zh-CN', ns)
    const en = flatNs('en-US', ns)
    const bad: string[] = []
    for (const [key, zhText] of zh) {
      const enText = en.get(key)
      if (enText === undefined) continue // 上一组用例已经管了
      const a = [...varsOf(zhText)].sort()
      const b = [...varsOf(enText)].sort()
      if (a.join(',') !== b.join(',')) bad.push(`${ns}:${key} zh=[${a}] en=[${b}]`)
    }
    expect(bad).toEqual([])
  })
})

describe('复数规则', () => {
  /**
   * i18next 的复数是 key 后缀，**哪些后缀有意义由语言决定**：英文要
   * `_one` + `_other`，中文只有 `_other`。中文里写个 `_one` 不会报错——它
   * 只是永远命中不了，于是那句译文是死的，评审时看着还挺齐整。
   *
   * 所需形态问 `Intl.PluralRules` 而不是写死：写死的话加语言时漏改的表现
   * 正是「翻译齐全，就是永远选不中」。
   */
  const requiredForms = (locale: Locale): Set<string> =>
    new Set<string>(new Intl.PluralRules(locale).resolvedOptions().pluralCategories)

  const pluralBases = (locale: Locale, ns: Namespace) => {
    const bases = new Map<string, Set<string>>()
    for (const key of flatNs(locale, ns).keys()) {
      const m = key.match(PLURAL_SUFFIX)
      if (!m) continue
      const base = key.slice(0, -m[0].length)
      if (!bases.has(base)) bases.set(base, new Set())
      bases.get(base)!.add(m[1])
    }
    return bases
  }

  it('英文要 one+other，中文只要 other——这条前提本身先钉住', () => {
    expect([...requiredForms('en-US')].sort()).toEqual(['one', 'other'])
    expect([...requiredForms('zh-CN')]).toEqual(['other'])
  })

  it.each(NAMESPACES)('%s：带复数的 key 两种语言成对出现，且形态不多不少', (ns) => {
    const zh = pluralBases('zh-CN', ns)
    const en = pluralBases('en-US', ns)
    expect([...en.keys()].sort()).toEqual([...zh.keys()].sort())

    const bad: string[] = []
    for (const [locale, bases] of [
      ['zh-CN', zh],
      ['en-US', en],
    ] as const) {
      const want = requiredForms(locale)
      for (const [base, forms] of bases) {
        const missing = [...want].filter((f) => !forms.has(f))
        const dead = [...forms].filter((f) => !want.has(f))
        if (missing.length || dead.length) {
          bad.push(`${locale} ${ns}:${base} 缺[${missing}] 多[${dead}]`)
        }
      }
    }
    expect(bad).toEqual([])
  })

  it('复数 key 在两种语言下都真的选得中：1 用单数，2/0 用复数', async () => {
    // 真跑 i18next 的复数解析，而不是只看 JSON 长什么样
    const probe = (count: number) =>
      i18n.t('count.selectedObjects', { ns: 'common', count })

    await i18n.changeLanguage('en-US')
    expect(probe(1)).toBe('1 object selected')
    expect(probe(2)).toBe('2 objects selected')
    expect(probe(0)).toBe('0 objects selected')

    await i18n.changeLanguage('zh-CN')
    expect(probe(1)).toBe('已选 1 个对象')
    expect(probe(2)).toBe('已选 2 个对象')
  })

  it('「单数是另一句话」的必须自己分 key，别指望中文的复数档', async () => {
    // 中文没有 one 档：塞进 _one 的「删除 折线图.pdf」永远选不中，
    // 用户看到的会是「删除 1 个对象」。这四对是按数量选 key 的。
    for (const locale of SUPPORTED_LOCALES) {
      await i18n.changeLanguage(locale)
      // 名字里不带数字，才断言得了「这句没有把数量说出来」
      const name = 'kinetics.pdf'
      const single = i18n.t('history.deleteObject', { ns: 'workspace', name })
      expect(single).toContain(name)
      expect(single).not.toMatch(/\d/)

      expect(i18n.t('history.moveObject', { ns: 'workspace' })).not.toMatch(/\d/)
      expect(i18n.t('status.objectCopied', { ns: 'workspace', name })).toContain(name)
      expect(i18n.t('element.scaleAxes', { ns: 'inspector' })).not.toContain('{{')
    }
    await i18n.changeLanguage(DEFAULT_LOCALE)
  })
})

describe('资源里没有把内容当 key 的写法', () => {
  /**
   * 唯一豁免是 `inspector:enum.<属性>.<取值>`——最后一段是 matplotlib 自己的
   * 取值字面量（`->`、`*`、`upper left`），它**就是** key 的一部分，不是中文
   * 文案被当成了 key。
   */
  const isEnumValueKey = (ns: Namespace, key: string) =>
    ns === 'inspector' && key.startsWith('enum.')

  it('key 里不出现中文——中文是文案，不是标识符', () => {
    const bad: string[] = []
    for (const locale of SUPPORTED_LOCALES) {
      for (const ns of NAMESPACES) {
        for (const key of flatNs(locale, ns).keys()) {
          if (/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/.test(key)) {
            bad.push(`${locale} ${ns}:${key}`)
          }
        }
      }
    }
    expect(bad).toEqual([])
  })

  it('除 matplotlib 取值段外，key 全是语义化的 ASCII 标识符', () => {
    const bad: string[] = []
    for (const locale of SUPPORTED_LOCALES) {
      for (const ns of NAMESPACES) {
        for (const key of flatNs(locale, ns).keys()) {
          if (isEnumValueKey(ns, key)) continue
          if (!/^[A-Za-z0-9_.\-[\]]+$/.test(key)) bad.push(`${locale} ${ns}:${key}`)
        }
      }
    }
    expect(bad).toEqual([])
  })
})

describe('文案里不许写不会被渲染的 Markdown', () => {
  /**
   * 绝大多数文案是**纯文本插值**（`{t('x')}` 直接进 `<p>`/`<span>`），没有
   * Markdown 渲染器。文案里写 `**强调**`，用户看到的就是两个字面星号。
   *
   * 真实发生过（2026-08-20，遥测那段隐私说明）：`**多次启动之间保持不变**`
   * 原样显示在设置里，而且随受管的 Codex 画布产物一起发了出去。
   * 需要强调时按仓库既有写法拆成 before/strong/after 三个 key，用 JSX 的
   * `<strong>`（见 `settings.about.diagnosticsHint*` 与 `telemetry.sends*`）。
   *
   * 例外只有一处：AI 面板里 **模型自己的输出**走 react-markdown，但那不是
   * 翻译资源，是运行期内容，不经过这里。
   */
  const MARKDOWN = [
    { name: '粗体 **…**', re: /\*\*[^*]+\*\*/ },
    { name: '标题 # ', re: /(^|\n)#{1,6}\s/ },
    { name: '行内代码 `…`', re: /`[^`]+`/ },
    { name: '链接 [x](y)', re: /\[[^\]]+\]\([^)]+\)/ },
  ]

  for (const locale of SUPPORTED_LOCALES) {
    for (const ns of NAMESPACES) {
      it(`${locale}:${ns}`, () => {
        const flat = flatten(resources[locale][ns] as Json)
        const bad: string[] = []
        for (const [key, text] of flat) {
          if (typeof text !== 'string') continue
          for (const { name, re } of MARKDOWN) {
            if (re.test(text)) bad.push(`${ns}:${key} 含${name}`)
          }
        }
        expect(bad, bad.join('\n')).toEqual([])
      })
    }
  }
})

describe('缺 key 时的回退', () => {
  /**
   * 这一层有一批**开放集合**：matplotlib 的属性名、色图名（viridis）、刻度
   * 格式串（%.1f）、脚本自定义的枚举值——它们永远不会全都进翻译表，查不到
   * 时必须原样显示。调用方为此传 `defaultValue`。
   *
   * `parseMissingKeyHandler` 会**盖掉** defaultValue（i18next 把它作为第二个
   * 参数传进去，处理函数的返回值就是最终结果），写成 `(key) => key` 的话
   * 用户在色图下拉里看到的是一串 `enum.cmap.viridis`：控件全在、功能全对、
   * 只是没人看得懂，而且不报错——所以钉一条。
   */
  it('给了 defaultValue 就用它，没给才回退到 key 本身', () => {
    expect(i18n.t('enum.cmap.viridis', { ns: 'inspector', defaultValue: 'viridis' })).toBe(
      'viridis',
    )
    expect(i18n.t('enum.format.%.1f', { ns: 'inspector', defaultValue: '%.1f' })).toBe('%.1f')
    // 空串同样是「调用方说了算」：propLabel 靠 `t(...) || 兜底` 判断有没有译文
    expect(i18n.t('prop.zzz_not_a_real_prop', { ns: 'inspector', defaultValue: '' })).toBe('')
    // 没给 defaultValue：漏翻至少看得见是哪一条
    expect(i18n.t('prop.zzz_not_a_real_prop', { ns: 'inspector' })).toBe(
      'prop.zzz_not_a_real_prop',
    )
  })
})
