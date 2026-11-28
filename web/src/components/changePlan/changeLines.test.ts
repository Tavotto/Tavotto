/**
 * 图内方案改动清单的逐行（ADR 0206 修订 2026-11-28）：元素名 · 属性 前 → 后，与矢量方案同一种行；
 * 取不到名字就只写属性（不露 gid）；自动留边距那条不列；值的显示（数字 / 颜色 / 开关 / 区间）。
 */
import { describe, expect, it } from 'vitest'
import type { ChangePlanChange } from '@/lib/api'
import { changeLines, changeValue } from './changePlanModel'

const ch = (over: Partial<ChangePlanChange>): ChangePlanChange => ({ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 }, ...over })

describe('changeLines', () => {
  it('元素名 · 属性，前 → 后；取不到名字只写属性，不露 gid', () => {
    const lines = changeLines([ch({}), ch({ target_gid: 'ax.line0', prop: 'linewidth', before: 1, after: 2.5 })], (gid) => (gid === 'ax.title' ? '标题' : undefined))
    expect(lines[0].label).toMatch(/^标题 · /)
    expect(lines[0].before?.text).toBe('9')
    expect(lines[0].after?.text).toBe('14')
    expect(lines[1].label).not.toContain('ax.line0')
    expect(lines[1].after?.text).toBe('2.5')
  })

  it('自动留边距那条不列', () => {
    expect(changeLines([ch({}), ch({ target_gid: 'ax', prop: 'position', auto: 'room' })], () => undefined)).toHaveLength(1)
  })
})

describe('changeValue', () => {
  it('颜色带色块（#hex 与 0–1 的 RGB）、开关说开 / 关、区间用 –、认不出的结构不列值', () => {
    expect(changeValue('color', '#1f77b4')).toEqual({ text: '#1F77B4', color: '#1f77b4' })
    expect(changeValue('facecolor', [1, 0, 0])).toEqual({ text: '#FF0000', color: '#FF0000' })
    expect(changeValue('visible', false)?.text).toBe('关')
    expect(changeValue('xlim', [0, 10])?.text).toBe('0 – 10')
    expect(changeValue('fontsize', 10.456)?.text).toBe('10.46')
    expect(changeValue('text', 'Time (s)')?.text).toBe('「Time (s)」')
    expect(changeValue('whatever', { a: 1 })).toBeUndefined()
  })
})

describe('changeLines 合并', () => {
  const names: Record<string, string> = {
    'ax.line0': '曲线 “Blank (k = 0.042 …”',
    'ax.legend.item0': '图例项 “Blank (k = 0.042 …”',
    'ax.line1': '曲线 “Catalyst”',
    'ax.title': '标题',
    'ax.xlabel': 'X 轴',
    'ax.ylabel': 'Y 轴',
  }
  const labelOf = (gid: string) => names[gid]
  const c = (gid: string, prop: string, before: unknown, after: unknown) => ch({ target_gid: gid, prop, before, after })

  it('同一系列、前后值相同的颜色 / 填充 / 描边 / 图例示意线合成一行，标记形状说人话', () => {
    const lines = changeLines(
      [
        c('ax.line0', 'color', '#8A4F9E', '#009E73'),
        c('ax.line0', 'markerfacecolor', '#8A4F9E', '#009E73'),
        c('ax.line0', 'markeredgecolor', '#8A4F9E', '#009E73'),
        c('ax.legend.item0', 'handle_color', '#8A4F9E', '#009E73'),
        c('ax.legend.item0', 'handle_marker', 's', '^'),
      ],
      labelOf,
    )
    expect(lines).toHaveLength(2)
    expect(lines[0].label).toBe('Blank (k = 0.042 … · 颜色')
    expect(lines[0].before?.color).toBe('#8A4F9E')
    expect(lines[0].after?.color).toBe('#009E73')
    expect(lines[0].compact).toBe(true)
    expect(lines[0].title).toContain('#8A4F9E → #009E73')
    expect(lines[1].label).toBe('Blank (k = 0.042 … · 标记')
    expect(lines[1].before?.text).toBe('方块')
    expect(lines[1].after?.text).toBe('上三角')
  })

  it('前后值不同的不合并', () => {
    const lines = changeLines([c('ax.line0', 'color', '#111111', '#222222'), c('ax.line0', 'markeredgecolor', '#111111', '#333333')], labelOf)
    expect(lines).toHaveLength(2)
    expect(lines[1].label).toContain('标记描边')
  })

  it('同一属性、同一前后值落在多个元素上：一行带计数，悬停列出各处', () => {
    const lines = changeLines(['ax.title', 'ax.xlabel', 'ax.ylabel'].map((g) => c(g, 'fontfamily', 'serif', 'Times New Roman')), labelOf)
    expect(lines).toHaveLength(1)
    expect(lines[0].after?.text).toBe('Times New Roman')
    expect(lines[0].extra).toBe('3 处')
    expect(lines[0].title).toBe('标题\nX 轴\nY 轴')
  })

  it('两个系列各自的颜色变化不同，各算各的；相同前后值则带计数', () => {
    expect(changeLines([c('ax.line0', 'color', '#111111', '#222222'), c('ax.line1', 'color', '#333333', '#444444')], labelOf)).toHaveLength(2)
    const same = changeLines([c('ax.line0', 'color', '#111111', '#222222'), c('ax.line1', 'color', '#111111', '#222222')], labelOf)
    expect(same).toHaveLength(1)
    expect(same[0].extra).toBe('2 处')
  })
})
