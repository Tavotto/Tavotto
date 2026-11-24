import { describe, expect, it } from 'vitest'
import type { ChangePlanGroup } from '@/lib/api'
import { scriptHandoffPrompt } from './assistantScriptContext'

const g = (key: string, gid: string, prop: string, after: unknown): ChangePlanGroup => ({
  key, label: key, atomic: false, boxes: { after: [] },
  changes: [{ target_gid: gid, prop, before: 1, after, patch: { gid, prop, value: after } }],
})
const plan = { groups: [g('g1', 'ax.line', 'linewidth', 3), g('g2', 'ax.title', 'text', 'Kinetics')] }
const labelOf = (gid: string) => (gid === 'ax.line' ? '曲线 1' : undefined)

describe('scriptHandoffPrompt', () => {
  it('已应用：逐条列出元素显示名 + 属性 + 新值，没有显示名退到 gid，并写明只需做原话里剩下的', () => {
    const out = scriptHandoffPrompt('加粗并改对数坐标', plan, { applied: true, unchecked: {}, labelOf })
    expect(out.startsWith('加粗并改对数坐标\n')).toBe(true)
    expect(out).toContain('已经在图内改好（不必再改）')
    expect(out).toContain('- 曲线 1 · linewidth → 3')
    expect(out).toContain('- ax.title · text → Kinetics')
    expect(out).toContain('只需做：加粗并改对数坐标')
  })

  it('未应用：写明用户还没应用、不要在脚本里重复', () => {
    const out = scriptHandoffPrompt('x', plan, { applied: false, unchecked: {}, labelOf })
    expect(out).toContain('还没应用，不要在脚本里重复它们')
    expect(out).not.toContain('已经在图内改好')
  })

  it('取消勾选的组不列；一处图内修改都没有 = 原话一个字不加', () => {
    expect(scriptHandoffPrompt('x', plan, { applied: true, unchecked: { g1: true }, labelOf })).not.toContain('曲线 1')
    expect(scriptHandoffPrompt('x', plan, { applied: true, unchecked: { g1: true, g2: true }, labelOf })).toBe('x')
    expect(scriptHandoffPrompt('x', { groups: [] }, { applied: false, unchecked: {}, labelOf })).toBe('x')
    expect(scriptHandoffPrompt('x', undefined, { applied: false, unchecked: {}, labelOf })).toBe('x')
  })

  it('同样的输入同样的输出；太多条只列前 20 条并说另有几处', () => {
    const many = { groups: Array.from({ length: 25 }, (_, i) => g(`g${i}`, `e${i}`, 'color', '#000000')) }
    const a = scriptHandoffPrompt('x', many, { applied: true, unchecked: {}, labelOf })
    expect(a).toBe(scriptHandoffPrompt('x', many, { applied: true, unchecked: {}, labelOf }))
    expect(a).toContain('另有 5 处')
    expect(a.split('\n').filter((l) => l.startsWith('- e')).length).toBe(20)
  })
})
