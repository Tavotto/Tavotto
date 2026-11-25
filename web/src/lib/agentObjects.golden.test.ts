import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/agent_object_vectors.json'
import { AGENT_OBJECT_COVERAGE, AGENT_OBJECT_TABLE } from './agentObjects'
import { toExportObjects } from './exportPayload'
import { isKnownObject, type CanvasObject } from '@/types/document'

/**
 * ADR 0208 §五：后端 `engine/agentobjects.py` 校验通过、转成载荷的对象，交给前端**真实的**
 * `toExportObjects` 必须得到逐字段相同的载荷（严格同源对；向量由 `scripts/gen_agent_object_vectors.py` 生成）。
 * 这是独立的一侧：前端从来没读过后端的转换代码，只读对象本身。
 */
type Case = { name: string; ok: boolean; objects?: unknown[]; export?: unknown[] }
const cases = (vectors as { cases: Case[] }).cases
const json = (v: unknown) => JSON.parse(JSON.stringify(v))

describe('agent objects ↔ exportPayload（golden）', () => {
  for (const c of cases.filter((x) => x.ok)) {
    it(c.name, () => {
      const objs = c.objects as CanvasObject[]
      expect(objs.every(isKnownObject)).toBe(true)
      expect(json(toExportObjects(objs))).toEqual(c.export)
    })
  }
})

describe('逐键覆盖表 ↔ 字段表', () => {
  const t = AGENT_OBJECT_TABLE as unknown as {
    common: Record<string, unknown>
    types: Record<string, Record<string, unknown>>
    reserved: string[]
  }

  it('每种类型里标 accept 的键 = 字段表里这一类的键（common + types）', () => {
    for (const [type, cov] of Object.entries(AGENT_OBJECT_COVERAGE)) {
      const accepted = Object.entries(cov)
        .filter(([, fate]) => fate === 'accept')
        .map(([k]) => k)
        .sort()
      const inTable = [...Object.keys(t.common), ...Object.keys(t.types[type] ?? {})].sort()
      expect(accepted, type).toEqual(inTable)
    }
  })

  it('字段表的类型 = 覆盖表的类型', () => {
    expect(Object.keys(t.types).sort()).toEqual(Object.keys(AGENT_OBJECT_COVERAGE).sort())
  })

  it('保留字段还不在任何对象接口里；进了接口就要在覆盖表里标 reserved', () => {
    for (const k of t.reserved) {
      for (const cov of Object.values(AGENT_OBJECT_COVERAGE)) {
        if (k in cov) expect(cov[k], k).toBe('reserved')
      }
    }
  })
})
