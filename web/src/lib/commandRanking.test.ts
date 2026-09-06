/**
 * 命令面板的排序判据（审计 T50）。
 *
 * 这里守的是「**常用编辑动作不用猜内部关键词**」这句验收：空查询时第一屏
 * 必须是此刻能做的事，而不是刷新项目 / 接入状态 / 三条教程命令。判据是纯
 * 函数，所以它可以被逐条反证——组件里那份只负责把它渲染出来。
 */
import { describe, expect, it } from 'vitest'
import {
  COMMON_ORDER,
  RECENT_COMMANDS_MAX,
  pushRecent,
  rankCommands,
  type RankableCommand,
} from './commandRanking'

const cmd = (id: string, needsSelection = false): RankableCommand => ({ id, needsSelection })

/** 真实池子的形状：低频项在注册顺序里排在前面，正是审计观察到的那一屏 */
const POOL: RankableCommand[] = [
  cmd('refresh-project'),
  cmd('readiness'),
  cmd('tutorial-start'),
  cmd('tutorial-resume'),
  cmd('tutorial-reset'),
  cmd('add-text'),
  cmd('select-all'),
  cmd('export'),
  cmd('fit'),
  cmd('group', true),
  cmd('edit-elements', true),
]

const flat = (sections: { items: RankableCommand[] }[]) =>
  sections.flatMap((s) => s.items.map((c) => c.id))

describe('命令面板的空查询顺序', () => {
  it('选区相关的排在最前，低频项沉到底', () => {
    const ids = flat(rankCommands(POOL, { hasSelection: true, recent: [] }))
    expect(ids.slice(0, 2)).toEqual(['group', 'edit-elements'])
    // 审计原话：默认先列刷新、项目接入和多项教程——它们现在必须在常用之后
    expect(ids.indexOf('add-text')).toBeLessThan(ids.indexOf('refresh-project'))
    expect(ids.indexOf('export')).toBeLessThan(ids.indexOf('tutorial-start'))
  })

  it('没有选区就没有那一段，不摆一个空标题', () => {
    const sections = rankCommands(POOL, { hasSelection: false, recent: [] })
    expect(sections.map((s) => s.section)).not.toContain('selection')
    // 「需要选区的命令此刻出不出现」由调用方的 `needsSelection` 过滤决定
    // （`CommandPalette.test.tsx` 量那一条），排序只管顺序：它不该把
    // 「没有选区」读成「这条命令不存在」而把它整个吞掉
    expect(flat(sections)).toHaveLength(POOL.length)
  })

  it('最近使用排在常用之前，按最近一次在前', () => {
    const ids = flat(rankCommands(POOL, { hasSelection: false, recent: ['fit', 'export'] }))
    expect(ids.slice(0, 2)).toEqual(['fit', 'export'])
    expect(ids.indexOf('fit')).toBeLessThan(ids.indexOf('add-text'))
  })

  it('每条命令只出现一次：进了最近就不再进常用', () => {
    const ids = flat(rankCommands(POOL, { hasSelection: true, recent: ['export', 'group'] }))
    expect(ids).toHaveLength(new Set(ids).size)
    expect(ids).toHaveLength(POOL.length)
    // 「group」已经进了选区段，最近段不再重复它
    expect(ids.filter((x) => x === 'group')).toHaveLength(1)
  })

  it('常用段按 COMMON_ORDER 的固定顺序，不跟着注册顺序走', () => {
    const shuffled = [...POOL].reverse()
    const common = rankCommands(shuffled, { hasSelection: false, recent: [] }).find(
      (s) => s.section === 'common',
    )
    const expected = COMMON_ORDER.filter((id) => POOL.some((c) => c.id === id))
    expect(common?.items.map((c) => c.id)).toEqual([...expected])
  })

  it('过滤后的池子照用同一份顺序：搜到的第一条仍按段落排', () => {
    const hit = POOL.filter((c) => c.id.includes('e'))
    const ids = flat(rankCommands(hit, { hasSelection: false, recent: ['export'] }))
    expect(ids[0]).toBe('export')
    expect(ids).toHaveLength(hit.length)
  })

  it('不认识的最近 id 不会凭空造出一行', () => {
    const ids = flat(rankCommands(POOL, { hasSelection: false, recent: ['no-such-command'] }))
    expect(ids).not.toContain('no-such-command')
    expect(ids).toHaveLength(POOL.length)
  })
})

describe('最近使用的记账', () => {
  it('去重、最近在前', () => {
    expect(pushRecent(['a', 'b', 'c'], 'b')).toEqual(['b', 'a', 'c'])
  })

  it('封顶：再多就不是「最近」了', () => {
    let recent: string[] = []
    for (let i = 0; i < RECENT_COMMANDS_MAX + 3; i++) recent = pushRecent(recent, `c${i}`)
    expect(recent).toHaveLength(RECENT_COMMANDS_MAX)
    expect(recent[0]).toBe(`c${RECENT_COMMANDS_MAX + 2}`)
    expect(recent).not.toContain('c0')
  })

  it('不改传进来的那个数组', () => {
    const before = ['a', 'b']
    pushRecent(before, 'c')
    expect(before).toEqual(['a', 'b'])
  })
})
