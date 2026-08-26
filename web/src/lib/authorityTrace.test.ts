/**
 * 几何权威追踪环的**隐私边界与有界性**（issue #131）。
 *
 * 这个环是要进诊断包的，所以「记了什么」比「记了多少」重要得多：
 * 用户图内文字、脚本、文件绝对路径、项目名、override 的值一个字都不许出现。
 * 变体键里天然带着文件名与 overrides 的 JSON，只能过不可逆短 hash。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  assertGeometryAuthority,
  clearTrace,
  readTrace,
  shortHash,
  traceGeometry,
} from './authorityTrace'

beforeEach(() => clearTrace())

describe('有界：环不会随会话增长', () => {
  it('写 500 条只留最近 100 条，且按时间序读出', () => {
    for (let i = 0; i < 500; i++) traceGeometry('align.request', { i })
    const all = readTrace()
    expect(all).toHaveLength(100)
    expect(all[0].data.i).toBe(400)
    expect(all[99].data.i).toBe(499)
  })

  it('没写满时按插入序读出', () => {
    traceGeometry('gesture.begin', { i: 1 })
    traceGeometry('gesture.finish', { i: 2 })
    expect(readTrace().map((r) => r.ev)).toEqual(['gesture.begin', 'gesture.finish'])
  })
})

describe('脱敏：内容一个字都不进来', () => {
  it('变体键过短 hash，反推不回文件名与 overrides', () => {
    const key = 'D:\\\\论文\\\\figures\\\\接触角验证.pdf [{"gid":"t1","prop":"text","value":"未发表结论"}]'
    traceGeometry('align.commit', { currentKey: key })
    const rec = readTrace()[0]
    expect(rec.data.currentKey).toBe(shortHash(key))
    const dumped = JSON.stringify(rec)
    expect(dumped).not.toContain('未发表结论')
    expect(dumped).not.toContain('论文')
    expect(dumped).not.toContain('.pdf')
  })

  it('长字符串一律 hash，短的技术枚举原样留（mode / reason 要能读）', () => {
    traceGeometry('align.blocked', { mode: 'left', reason: 'syncing' })
    expect(readTrace()[0].data).toMatchObject({ mode: 'left', reason: 'syncing' })

    clearTrace()
    const long = 'x'.repeat(200)
    traceGeometry('align.blocked', { note: long })
    expect(readTrace()[0].data.note).toBe(shortHash(long))
  })

  it('补丁只记 gid:prop 与条数，值不进来', () => {
    traceGeometry('align.commit', {
      patches: ['axes_0.title:pos_frac', 'axes_0.xlabel:pos_frac'],
    })
    const rec = readTrace()[0]
    expect(rec.data.patches_n).toBe(2)
    expect(rec.data.patches).toBe('axes_0.title:pos_frac,axes_0.xlabel:pos_frac')
  })

  it('对象 / 函数这类不认识的形状直接丢掉，不做深序列化', () => {
    traceGeometry('align.commit', {
      // 真实调用点不会这么传，但环是公共入口，兜底必须在
      blob: { secret: '用户正文' },
      fn: () => '用户正文',
    })
    expect(JSON.stringify(readTrace()[0])).not.toContain('用户正文')
  })

  it('时间是相对会话开始的毫秒，不落墙钟', () => {
    traceGeometry('gesture.begin')
    const t = readTrace()[0].t
    expect(t).toBeGreaterThanOrEqual(0)
    expect(t).toBeLessThan(Date.now() - 1_000_000)
  })
})

describe('不变式：权威键必须等于当前变体键', () => {
  it('相等放行，不记违规', () => {
    expect(assertGeometryAuthority('f1 []', 'f1 []', 'align')).toBe(true)
    expect(readTrace()).toHaveLength(0)
  })

  it('不等则拒绝并留痕（两个键都是 hash）', () => {
    expect(assertGeometryAuthority('f1 [1]', 'f1 [2]', 'align')).toBe(false)
    const rec = readTrace().at(-1)!
    expect(rec.ev).toBe('invariant.violated')
    expect(rec.data.where).toBe('align')
    expect(rec.data.currentKey).toBe(shortHash('f1 [1]'))
  })

  it('权威为 null（还没画出来）同样拒绝', () => {
    expect(assertGeometryAuthority('f1 []', null, 'align')).toBe(false)
  })
})

describe('shortHash', () => {
  it('同串同值、异串异值、null 有稳定占位', () => {
    expect(shortHash('a')).toBe(shortHash('a'))
    expect(shortHash('a')).not.toBe(shortHash('b'))
    expect(shortHash(null)).toBe('-')
    expect(shortHash('a')).toMatch(/^[0-9a-f]{8}$/)
  })
})
