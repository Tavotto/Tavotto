/**
 * Import Origin 的翻译层（PR6）：读后端事实、译闭集码、给五种展示态起名字——**没有判据**。
 * 判据的主语：每个闭集码都有一句（中英两语）人话；每句话只有一句、没有术语；形状读不出就当没有；
 * 同样的字段换一堆名字结果不变（前端不看 import 名）。
 */
import { describe, expect, it } from 'vitest'
import {
  DIST_FAILURE_REASONS,
  ENV_COVERAGE_DETAIL_CODES,
  JOINT_ORIGIN_REASONS,
  type DependencyPreparationOffer,
  type JointOrigin,
  type PreparationReport,
} from '@/lib/api'
import { i18n, resources, t } from '@/i18n'
import {
  ORIGIN_VIEWS,
  coverageCodeText,
  coverageDetails,
  distFailureText,
  draftFromOffer,
  originFactsOf,
  originReasonText,
  originViewOfCard,
  originViewOfOffer,
  partitionOrigins,
  userSpecifiedBody,
} from '@/lib/importOrigin'
import { sentenceCount } from '@/test/visibleBlocks'

const LOCALES = Object.keys(resources) as (keyof typeof resources)[]

const origin = (name: string, over: Partial<JointOrigin> = {}): JointOrigin => ({
  import_name: name,
  bucket: 'unknown',
  distribution: '',
  reason: 'distribution_unresolved',
  resolution_status: 'unresolved',
  distribution_status: '',
  origin_kind: 'unresolved',
  provenance: '',
  candidates: [],
  coverage: '',
  ...over,
})
const offerOf = (origins: JointOrigin[], over: Record<string, unknown> = {}, requirements: string[] = []): DependencyPreparationOffer =>
  ({
    script: 'a.py',
    plan: { requirements, origins, unknown: origins.map((o) => o.import_name) },
    ...over,
  }) as never
const reportWith = (detail: unknown): PreparationReport =>
  ({ checks: [{ id: 'dependencies', status: 'ok', ...(detail === undefined ? {} : { detail }) }] }) as never

const flatten = (obj: unknown, prefix = '', out: [string, string][] = []): [string, string][] => {
  if (typeof obj === 'string') out.push([prefix, obj])
  else if (obj && typeof obj === 'object') for (const [k, v] of Object.entries(obj)) flatten(v, prefix ? `${prefix}.${k}` : k, out)
  return out
}

describe('每个闭集码一句人话（两种语言）', () => {
  for (const lng of LOCALES) {
    it(`${lng} · 来源未定的 7 个原因、覆盖度的 6 个 detail 码、核验的 4 个原因：各有一句、互不相同、不甩代码`, async () => {
      await i18n.changeLanguage(lng)
      const groups: [readonly string[], (c: string) => string][] = [
        [JOINT_ORIGIN_REASONS, originReasonText],
        [ENV_COVERAGE_DETAIL_CODES, coverageCodeText],
        [DIST_FAILURE_REASONS, distFailureText],
      ]
      for (const [codes, text] of groups) {
        const fallback = text('__not_in_the_closed_set__')
        const seen = new Set<string>()
        for (const code of codes) {
          const sentence = text(code)
          expect(sentence, code).not.toBe(fallback)
          expect(sentence, code).not.toContain(code)
          expect(sentence.trim(), code).not.toBe('')
          expect(seen.has(sentence), `${code} 与别的码同一句`).toBe(false)
          seen.add(sentence)
        }
        // 认不出的新码（新后端、旧前端）落到通用的一句，不原样甩代码
        expect(fallback).not.toContain('__not_in_the_closed_set__')
      }
      await i18n.changeLanguage('zh-CN')
    })
  }

  it('闭集与后端镜像的长度钉死：多一个少一个都要同时补文案（api.ts 的顺序由 tests/test_import_origin_ui_facts.py 钉）', () => {
    expect(JOINT_ORIGIN_REASONS).toHaveLength(7)
    expect(ENV_COVERAGE_DETAIL_CODES).toHaveLength(6)
    expect(DIST_FAILURE_REASONS).toHaveLength(4)
  })
})

describe('prep.origin.* 的每一条文案', () => {
  for (const lng of LOCALES) {
    const leaves = flatten((resources[lng].workspace as { prep: { origin: unknown } }).prep.origin)
    it(`${lng} · 每条至多一句话；没有「隔离环境 / site-packages / editable / dist-info」这类术语；没有 Python 包管理行话`, () => {
      expect(leaves.length).toBeGreaterThan(30)
      for (const [key, value] of leaves) {
        expect(sentenceCount(value, lng), `${key}: ${value}`).toBeLessThanOrEqual(1)
        expect(value, key).not.toMatch(/隔离环境|site-?packages|editable|dist-?info|importlib|virtualenv|\bwheel\b|sys\.path|namespace package|\.pth\b/i)
      }
    })
  }
})

describe('originFactsOf：只读后端给的计数与原因码，读不出就当没有', () => {
  it('正常形状', () => {
    expect(originFactsOf(reportWith({ origins: { count: 3, by_reason: { unverified: 1, module_origin_ambiguous: 2 }, confirmable: 1 } }))).toEqual({
      count: 3,
      by_reason: { unverified: 1, module_origin_ambiguous: 2 },
      confirmable: 1,
    })
  })
  it('没有 confirmable 就没有这个键；confirmable 不会超过 count', () => {
    expect(originFactsOf(reportWith({ origins: { count: 2, by_reason: {} } }))).toEqual({ count: 2, by_reason: {} })
    expect(originFactsOf(reportWith({ origins: { count: 2, by_reason: {}, confirmable: 9 } }))?.confirmable).toBe(2)
  })
  it.each([
    ['没有 detail', undefined],
    ['没有 origins', { deferred: 'project_environment' }],
    ['计数为 0', { origins: { count: 0, by_reason: {} } }],
    ['计数不是数', { origins: { count: '3', by_reason: {} } }],
    ['origins 不是对象', { origins: 'x' }],
    ['origins 是数组', { origins: [1] }],
  ])('%s → null', (_name, detail) => {
    expect(originFactsOf(reportWith(detail))).toBeNull()
  })
  it('没有报告 / 没有依赖检查项 → null', () => {
    expect(originFactsOf(null)).toBeNull()
    expect(originFactsOf({ checks: [{ id: 'target', status: 'ok', detail: { origins: { count: 1, by_reason: {} } } }] } as never)).toBeNull()
  })
  it('原因里的非正数 / 非数被丢掉，不让一行「0 个」出现', () => {
    expect(originFactsOf(reportWith({ origins: { count: 2, by_reason: { unverified: 0, conda_package_not_pypi: 2, bad: 'x' } } }))?.by_reason).toEqual({
      conda_package_not_pypi: 2,
    })
  })
})

describe('partitionOrigins / draftFromOffer / userSpecifiedBody', () => {
  const o = [origin('a', { suggested_distribution: 'a-dist' }), origin('b'), origin('c', { suggested_distribution: 'c-dist' }), origin('d', { suggested_distribution: '' })]
  it('只按「后端有没有给预填」分两堆，顺序保持', () => {
    const { specifiable, explainOnly } = partitionOrigins({ origins: o })
    expect(specifiable.map((x) => x.import_name)).toEqual(['a', 'c'])
    expect(explainOnly.map((x) => x.import_name)).toEqual(['b', 'd'])
    expect(partitionOrigins(undefined)).toEqual({ specifiable: [], explainOnly: [] })
  })
  it('草稿的初始值就是后端的预填（原样）', () => {
    expect(draftFromOffer(offerOf(o))).toEqual({ script: 'a.py', names: ['a', 'c'], values: { a: 'a-dist', c: 'c-dist' } })
  })
  it('提交体：只收有输入框的、没清空的；值只去首尾空白，不改写不预判', () => {
    const draft = { script: 'a.py', names: ['a', 'c'], values: { a: '  a-dist>=1.2 ', c: '   ', zzz: 'ignored' } }
    expect(userSpecifiedBody(draft)).toEqual({ a: 'a-dist>=1.2' })
    expect(userSpecifiedBody({ ...draft, values: { a: 'Weird Name!!', c: '' } })).toEqual({ a: 'Weird Name!!' })
  })
  it('前端不看 import 名：换一堆名字，分堆结果的形状完全一样', () => {
    const rename = (x: JointOrigin, i: number) => ({ ...x, import_name: `n${i}.__${'x'.repeat(i)}` })
    const a = partitionOrigins({ origins: o })
    const b = partitionOrigins({ origins: o.map(rename) })
    expect(b.specifiable).toHaveLength(a.specifiable.length)
    expect(b.explainOnly).toHaveLength(a.explainOnly.length)
  })
})

describe('五种展示态的名字', () => {
  it('卡片状态 → 展示态：五种都有出处，不属于这五档的回 null', () => {
    const named = new Set(
      ['ready', 'env_choice', 'deps', 'deps_runtime', 'env_blocked', 'origin_confirm', 'origin_unknown', 'deps_unknown']
        .map(originViewOfCard)
        .filter(Boolean),
    )
    expect(named).toEqual(new Set(ORIGIN_VIEWS))
    for (const other of ['running', 'completed', 'workdir', 'args', 'failed', 'checking', 'gui_dialog']) {
      expect(originViewOfCard(other), other).toBeNull()
    }
  })
  it('授权框载荷 → 展示态：装齐的用户环境 > 有要装的 > 带预填的 > 其余来源未定 > 已就绪', () => {
    const a = origin('a', { suggested_distribution: 'a' })
    const b = origin('b')
    expect(originViewOfOffer(offerOf([a], { user_environments: [{ id: 'e', satisfies: true }] }, ['six']))).toBe('better_env')
    expect(originViewOfOffer(offerOf([a], { user_environments: [{ id: 'e', satisfies: false }] }, ['six']))).toBe('auto_fix')
    expect(originViewOfOffer(offerOf([a, b]))).toBe('needs_confirm')
    expect(originViewOfOffer(offerOf([b]))).toBe('undetermined')
    expect(originViewOfOffer(offerOf([]))).toBe('ready')
  })
})

describe('coverageDetails', () => {
  it('只留有码的条目（形状不对的丢掉）', () => {
    expect(coverageDetails([{ code: 'environment_unusable', reason: 'x' }, null, { reason: 'y' }] as never)).toEqual([
      { code: 'environment_unusable', reason: 'x' },
    ])
    expect(coverageDetails(undefined)).toEqual([])
  })
})

describe('语言切换', () => {
  it('同一个码在两种语言里是不同的句子', async () => {
    await i18n.changeLanguage('zh-CN')
    const zh = originReasonText('unverified')
    await i18n.changeLanguage('en-US')
    const en = originReasonText('unverified')
    await i18n.changeLanguage('zh-CN')
    expect(zh).not.toBe(en)
    expect(t('prep.origin.reason.unverified', { ns: 'workspace' })).toBe(zh)
  })
})
