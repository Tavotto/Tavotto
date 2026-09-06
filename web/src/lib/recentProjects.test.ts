/**
 * 最近项目列表的纯判据（审计 T02）：同名怎么区分、失效怎么分组、
 * 输入框里那段字是路径还是筛选词。
 */
import { describe, expect, it } from 'vitest'
import {
  disambiguateRecent,
  looksLikePath,
  matchesRecent,
  pathSegments,
  splitRecent,
  submitTargetFor,
} from './recentProjects'

const entry = (path: string, name = path.split(/[\\/]/).pop()!, exists = true) => ({
  path,
  name,
  exists,
})

describe('disambiguateRecent：同名项目取最短能区分的尾段', () => {
  it('六个 figs 的上一级同名时，一直往上取到能区分为止', () => {
    const list = [
      entry('/private/var/folders/T/pytest-of-jiaqi/pytest-1/figs'),
      entry('/private/var/folders/T/pytest-of-jiaqi/pytest-2/figs'),
      entry('/private/var/folders/T/pytest-of-jiaqi/pytest-3/figs'),
    ]
    const hints = disambiguateRecent(list)
    expect(hints.get(list[0].path)).toBe('…/pytest-1/figs')
    expect(hints.get(list[2].path)).toBe('…/pytest-3/figs')
  })

  it('尾段相同、更上一级才不同时，多取一段', () => {
    const list = [entry('/r/a/x/figs'), entry('/r/b/x/figs')]
    const hints = disambiguateRecent(list)
    expect(hints.get('/r/a/x/figs')).toBe('…/a/x/figs')
    expect(hints.get('/r/b/x/figs')).toBe('…/b/x/figs')
  })

  it('路径本来就不够长时显示整条，不带省略号', () => {
    const list = [entry('/figs'), entry('/tmp/figs')]
    const hints = disambiguateRecent(list)
    expect(hints.get('/figs')).toBe('/figs')
    // 两段正好取完：整条路径，没有省略号
    expect(hints.get('/tmp/figs')).toBe('/tmp/figs')
  })

  it('不同名的项目没有提示；教程副本不参与', () => {
    const list = [
      entry('/Users/x/Desktop/polish-mech'),
      entry('/Users/x/Desktop/sub'),
      { ...entry('/data/tutorial/figs'), tutorial: true },
      entry('/Users/x/figs'),
    ]
    const hints = disambiguateRecent(list)
    expect(hints.size).toBe(0)
  })

  it('Windows 路径沿用反斜杠', () => {
    const list = [entry('D:\\research\\a\\figs'), entry('D:\\research\\b\\figs')]
    const hints = disambiguateRecent(list)
    expect(hints.get('D:\\research\\a\\figs')).toBe('…\\a\\figs')
  })

  it('pathSegments 去掉空段', () => {
    expect(pathSegments('/a//b/')).toEqual(['a', 'b'])
    expect(pathSegments('C:\\x\\y')).toEqual(['C:', 'x', 'y'])
  })
})

describe('looksLikePath：只认绝对路径与 ~', () => {
  it.each(['/Users/x/figs', '~/figs', '~', 'D:\\research', 'd:/research', '\\\\server\\share'])(
    '%s 是路径',
    (s) => expect(looksLikePath(s)).toBe(true),
  )
  it.each(['figs', 'polish', 'research/figs', '', '  '])('%j 不是路径', (s) =>
    expect(looksLikePath(s)).toBe(false),
  )
})

describe('splitRecent / matchesRecent：筛选 + 失效分组', () => {
  const list = [
    entry('/Users/x/Desktop/polish-mech'),
    entry('/tmp/gone/figs', 'figs', false),
    entry('/Users/x/figs'),
    entry('/tmp/gone2/figs', 'figs', false),
  ]

  it('有效的在前、失效的单独一组，各自保持原顺序', () => {
    const { available, missing } = splitRecent(list)
    expect(available.map((e) => e.path)).toEqual(['/Users/x/Desktop/polish-mech', '/Users/x/figs'])
    expect(missing.map((e) => e.path)).toEqual(['/tmp/gone/figs', '/tmp/gone2/figs'])
  })

  it('筛选词同时匹配名字与路径，不分大小写', () => {
    expect(splitRecent(list, 'POLISH').available).toHaveLength(1)
    expect(splitRecent(list, 'desktop').available).toHaveLength(1)
    expect(splitRecent(list, 'gone2').missing).toHaveLength(1)
    expect(matchesRecent(list[0], '  ')).toBe(true)
  })

  it('教程副本只按名字匹配，不按它那条不显示的路径', () => {
    const tut = { ...entry('/data/tutorial/figs', 'Tutorial'), tutorial: true }
    expect(matchesRecent(tut, 'figs')).toBe(false)
    expect(matchesRecent(tut, 'tutor')).toBe(true)
  })
})

describe('submitTargetFor：回车打开什么', () => {
  const avail = [entry('/Users/x/Desktop/polish-mech'), entry('/Users/x/figs')]
  it('像路径就打开路径（哪怕列表里有匹配项）', () => {
    expect(submitTargetFor('/Users/x/figs', avail)).toEqual({ kind: 'path', path: '/Users/x/figs' })
  })
  it('筛选后恰好一个可打开项就打开它', () => {
    const t = submitTargetFor('polish', avail.filter((e) => e.name.includes('polish')))
    expect(t?.kind).toBe('recent')
  })
  it('空词 / 多个匹配没有目标', () => {
    expect(submitTargetFor('', avail)).toBeNull()
    expect(submitTargetFor('s', avail)).toBeNull()
  })
})
