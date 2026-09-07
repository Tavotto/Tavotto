/**
 * 版本列表那句「变了什么」（审计 T04）。
 *
 * 这里最容易问错的是**主语**：检查点拍的是激活画布，却按 documentId（整个
 * 项目）归档，所以时间上相邻的两条完全可能来自两张不同的画布。拿 A 的对象数
 * 减 B 的对象数会得到一个「新增 12 个对象」——数字是真的，含义是假的。
 */
import { describe, expect, it } from 'vitest'
import { applyLocale, formatMessage } from '@/i18n'
import type { LayoutVersionMeta } from '@/lib/api'
import {
  comparableEarlier,
  versionDisplayName,
  versionSummary,
  versionSummaryText,
} from './versionSummary'

const v = (over: Partial<LayoutVersionMeta> = {}): LayoutVersionMeta => ({
  id: 'v1',
  name: '09-06 21:30',
  ts: 1_757_000_000_000,
  auto: true,
  description: '',
  objects: 5,
  page: { w: 150, h: 100 },
  canvasId: 'c1',
  canvasName: 'Fig 1',
  ...over,
})

const text = (s: ReturnType<typeof versionSummary>) =>
  versionSummaryText(s).map(formatMessage).join(' · ')

describe('该跟谁比', () => {
  it('跳过中间来自别的画布的检查点，比同一张画布上最近的那一版', () => {
    // 列表按时间倒序：[0] 最新
    const list = [
      v({ id: 'a3', canvasId: 'c1' }),
      v({ id: 'b1', canvasId: 'c2' }),
      v({ id: 'a1', canvasId: 'c1' }),
    ]
    expect(comparableEarlier(list, 0)?.id).toBe('a1')
    expect(comparableEarlier(list, 1)).toBeNull() // c2 只有这一条
  })

  it('自己没有画布身份（旧检查点）：不比', () => {
    const list = [v({ id: 'old', canvasId: undefined }), v({ id: 'a1', canvasId: 'c1' })]
    expect(comparableEarlier(list, 0)).toBeNull()
  })

  it('上一条没有画布身份：不拿它当同一张画布', () => {
    const list = [v({ id: 'a2', canvasId: 'c1' }), v({ id: 'old', canvasId: undefined })]
    expect(comparableEarlier(list, 0)).toBeNull()
  })

  it('最老的那一条没有更早的可比', () => {
    expect(comparableEarlier([v()], 0)).toBeNull()
  })
})

describe('摘要', () => {
  it('没有可比的上一版：说自己有多大', () => {
    const s = versionSummary(v({ objects: 7 }), null)
    expect(s).toEqual({ kind: 'baseline', objects: 7, page: { w: 150, h: 100 } })
    expect(text(s)).toContain('7')
  })

  it('对象多了 / 少了各说各的', () => {
    expect(versionSummary(v({ objects: 9 }), v({ objects: 5 }))).toEqual({
      kind: 'changed',
      objectDelta: 4,
      page: null,
    })
    expect(text(versionSummary(v({ objects: 9 }), v({ objects: 5 })))).toContain('新增')
    expect(text(versionSummary(v({ objects: 2 }), v({ objects: 5 })))).toContain('删除')
    // 措辞里出现的是差值，不是那一版的总数
    expect(text(versionSummary(v({ objects: 9 }), v({ objects: 5 })))).toContain('4')
    expect(text(versionSummary(v({ objects: 9 }), v({ objects: 5 })))).not.toContain('9')
  })

  it('图幅变了就把两个尺寸都说出来', () => {
    const s = versionSummary(
      v({ page: { w: 210, h: 140 } }),
      v({ page: { w: 150, h: 100 } }),
    )
    expect(s).toEqual({
      kind: 'changed',
      objectDelta: 0,
      page: { from: { w: 150, h: 100 }, to: { w: 210, h: 140 } },
    })
    const out = text(s)
    for (const n of ['150', '100', '210', '140']) expect(out).toContain(n)
  })

  it('两个维度都没变时说的是「这两个维度没变」，不是「内容一致」', () => {
    const s = versionSummary(v(), v())
    expect(s).toEqual({ kind: 'sameMetrics' })
    // 挪动 / 改字 / 改样式这里量不到，措辞不许把它们也一起担保掉
    expect(text(s)).toBe('对象数与图幅未变')
  })

  it('任一侧没记图幅（旧检查点）：不说图幅变了', () => {
    expect(versionSummary(v({ page: undefined }), v()).kind).toBe('sameMetrics')
    expect(versionSummary(v(), v({ page: undefined })).kind).toBe('sameMetrics')
  })

  it('英文下也说得出这几句（复数形态各有各的 key）', async () => {
    await applyLocale('en-US')
    try {
      expect(text(versionSummary(v({ objects: 6 }), v({ objects: 5 })))).toContain('1 object added')
      expect(text(versionSummary(v({ objects: 8 }), v({ objects: 5 })))).toContain('3 objects added')
      expect(text(versionSummary(v(), v()))).not.toContain('sameMetrics')
    } finally {
      await applyLocale('zh-CN')
    }
  })
})

describe('名字', () => {
  it('后端按时间生成的那个名字不再显示一遍（每行本来就有时间）', () => {
    expect(versionDisplayName(v({ name: '09-06 21:30' }))).toBeNull()
    expect(versionDisplayName(v({ name: '' }))).toBeNull()
    expect(versionDisplayName(v({ name: '   ' }))).toBeNull()
  })

  it('用户起的名字照常显示', () => {
    expect(versionDisplayName(v({ name: '投稿前' }))).toBe('投稿前')
    expect(versionDisplayName(v({ name: '恢复前（21:30）' }))).toBe('恢复前（21:30）')
    // 形状不同就不是生成的：多一位、少一位、换了分隔符都算用户内容
    expect(versionDisplayName(v({ name: '2026-09-06 21:30' }))).toBe('2026-09-06 21:30')
    expect(versionDisplayName(v({ name: '09-06' }))).toBe('09-06')
  })
})
