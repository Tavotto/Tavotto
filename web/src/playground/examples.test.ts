/**
 * 示例必须是**普通的** matplotlib 脚本（ADR 0007）：不带 pyodide / js /
 * Tavotto 专有 import——产品主张是「接你本来就在写的图」，示例要是特供的，
 * 演示的就是假东西。
 */
import { describe, expect, it } from 'vitest'
import { EXAMPLES, PRIMARY_EXAMPLE, SECONDARY_EXAMPLES } from './examples'
import { MAX_SOURCE_BYTES } from './runtime'

describe('playground 示例', () => {
  it('有 2–3 个，且文件名都是 .py', () => {
    expect(EXAMPLES.length).toBeGreaterThanOrEqual(2)
    expect(EXAMPLES.length).toBeLessThanOrEqual(3)
    for (const ex of EXAMPLES) expect(ex.filename).toMatch(/^[\w.-]+\.py$/)
  })

  it('是普通 Python：不 import pyodide / js / tavotto，任何浏览器特供 API 都不出现', () => {
    for (const ex of EXAMPLES) {
      expect(ex.source).not.toMatch(/import\s+(pyodide|js|micropip|tavotto)/)
      expect(ex.source).not.toMatch(/from\s+(pyodide|js|tavotto)/)
      expect(ex.source).not.toMatch(/postMessage|window\.|document\./)
    }
  })

  it('都在源文件大小限制之内，且真的画图（savefig 或 pyplot）', () => {
    for (const ex of EXAMPLES) {
      expect(new TextEncoder().encode(ex.source).length).toBeLessThan(MAX_SOURCE_BYTES)
      expect(ex.source).toMatch(/import matplotlib/)
      expect(ex.source).toMatch(/savefig|plt\.show/)
    }
  })

  it('至少一个示例同时暴露标题 / 轴标签 / 图例 / 多条曲线（语义选择一眼可见）', () => {
    const rich = EXAMPLES.some(
      (ex) =>
        /set_title/.test(ex.source) &&
        /set_xlabel/.test(ex.source) &&
        /legend/.test(ex.source) &&
        (ex.source.match(/ax\.plot\(/g)?.length ?? 0) >= 2,
    )
    expect(rich).toBe(true)
  })

  it('主 CTA 的示例有且只有一个——主路径指不到两个地方', () => {
    expect(EXAMPLES.filter((ex) => ex.primary)).toHaveLength(1)
    expect(PRIMARY_EXAMPLE.primary).toBe(true)
  })

  it('主示例本身就是那个「一眼看得见语义」的：标题 / 轴标签 / 图例 / 两条曲线', () => {
    const src = PRIMARY_EXAMPLE.source
    expect(src).toMatch(/set_title/)
    expect(src).toMatch(/set_xlabel/)
    expect(src).toMatch(/set_ylabel/)
    expect(src).toMatch(/legend/)
    expect(src.match(/ax\.plot\(/g) ?? []).toHaveLength(2)
  })

  it('次级示例不与主 CTA 重复，且加起来就是全部', () => {
    expect(SECONDARY_EXAMPLES).not.toContain(PRIMARY_EXAMPLE)
    expect([PRIMARY_EXAMPLE, ...SECONDARY_EXAMPLES].sort()).toEqual([...EXAMPLES].sort())
  })
})
