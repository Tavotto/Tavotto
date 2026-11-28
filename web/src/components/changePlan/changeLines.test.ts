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
