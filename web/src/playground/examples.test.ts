/**
 * 案例数据的看护（ADR 0007）：
 *
 *   * 案例必须是**普通的** matplotlib 脚本——不带 pyodide / js / Tavotto
 *     专有 import。产品主张是「接你本来就在写的图」，案例要是特供的，
 *     演示的就是假东西；
 *   * 不读任何外部文件——浏览器 playground 是单文件沙盒，案例要是自己都
 *     跑不起来，它示范的就是失败；
 *   * 封面与源码由哈希绑定：`examples/*.py` 改了而封面没重新生成，
 *     这里与 `generate_playground_examples.py --check` 都是红灯。
 */
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import coversManifest from './generated/examples-manifest.json'
import { EXAMPLES, FEATURED_EXAMPLE, exampleById } from './examples'
import { MAX_SOURCE_BYTES } from './runtime'

const sha256 = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex')

describe('playground 案例数据', () => {
  it('恰好三个案例，filename 唯一且与 id 对应', () => {
    expect(EXAMPLES).toHaveLength(3)
    const names = EXAMPLES.map((e) => e.filename)
    expect(new Set(names).size).toBe(names.length)
    for (const ex of EXAMPLES) {
      expect(ex.filename).toBe(`${ex.id}.py`)
      expect(ex.filename).toMatch(/^[\w.-]+\.py$/)
    }
  })

  it('主推案例有且只有一个，且它就是唯一的 starter——主路径指不到两个地方', () => {
    expect(EXAMPLES.filter((e) => e.featured)).toHaveLength(1)
    expect(EXAMPLES.filter((e) => e.difficulty === 'starter')).toHaveLength(1)
    expect(FEATURED_EXAMPLE.featured).toBe(true)
    expect(FEATURED_EXAMPLE.difficulty).toBe('starter')
  })

  it('源码非空、UTF-8 可编码、在大小限制之内，且真的画图', () => {
    for (const ex of EXAMPLES) {
      expect(ex.source.length).toBeGreaterThan(0)
      const bytes = new TextEncoder().encode(ex.source)
      expect(bytes.length).toBeLessThan(MAX_SOURCE_BYTES)
      expect(ex.source).toMatch(/import matplotlib/)
      expect(ex.source).toMatch(/savefig|plt\.show/)
    }
  })

  it('是普通 Python：不 import pyodide / js / tavotto，任何浏览器特供 API 都不出现', () => {
    for (const ex of EXAMPLES) {
      expect(ex.source).not.toMatch(/import\s+(pyodide|js|micropip|tavotto)/)
      expect(ex.source).not.toMatch(/from\s+(pyodide|js|tavotto)/)
      expect(ex.source).not.toMatch(/postMessage|window\.|document\./)
    }
  })

  it('不读任何外部文件——单文件沙盒里的案例必须自给自足', () => {
    for (const ex of EXAMPLES) {
      expect(ex.source).not.toMatch(/read_csv|read_excel|np\.load|loadtxt|genfromtxt/)
      expect(ex.source).not.toMatch(/(?<!\w)open\s*\(/)
      expect(ex.source).not.toMatch(/urllib|requests|http/)
    }
  })

  it('封面来自同一份源码：sha256 与生成 manifest 逐字相同，尺寸非零', () => {
    const manifest = coversManifest as Record<
      string,
      { sourceSha256: string; width: number; height: number }
    >
    for (const ex of EXAMPLES) {
      const entry = manifest[ex.id]
      expect(entry, `${ex.id} 缺封面 manifest——跑 generate_playground_examples.py`).toBeTruthy()
      expect(sha256(ex.source), `${ex.id}.py 改了但封面没重新生成`).toBe(entry.sourceSha256)
      expect(ex.thumbWidth).toBe(entry.width)
      expect(ex.thumbHeight).toBe(entry.height)
      expect(entry.width).toBeGreaterThan(0)
      expect(entry.height).toBeGreaterThan(0)
      expect(ex.thumbnail.length).toBeGreaterThan(0)
    }
    // manifest 里没有多余条目（删了案例要重新生成）
    expect(Object.keys(manifest).sort()).toEqual(EXAMPLES.map((e) => e.id).sort())
  })

  it('主推案例暴露标题 / 轴标签 / 图例 / 两条曲线，标题字号钉死在 9pt（引导任务的起点）', () => {
    const src = FEATURED_EXAMPLE.source
    expect(src).toMatch(/set_title\("Reaction kinetics", fontsize=9\)/)
    expect(src).toMatch(/set_xlabel/)
    expect(src).toMatch(/set_ylabel/)
    expect(src).toMatch(/legend/)
    expect(src.match(/ax\.plot\(/g) ?? []).toHaveLength(2)
  })

  it('引导任务的判据合法：gid / prop / 目标值都是真实可验证的形状', () => {
    const task = FEATURED_EXAMPLE.guidedTask
    expect(task).toBeTruthy()
    expect(task!.targetGid).toMatch(/^axes_\d+\./)
    expect(task!.prop).toBe('fontsize')
    expect(task!.targetValue).toBe(12)
    // 只有内置案例出现引导；其余案例本轮没有任务，不许带半个
    for (const ex of EXAMPLES.filter((e) => e !== FEATURED_EXAMPLE)) {
      expect(ex.guidedTask).toBeUndefined()
    }
  })

  it('exampleById 按 id 找得到每一个', () => {
    for (const ex of EXAMPLES) expect(exampleById(ex.id)).toBe(ex)
    expect(exampleById('nope')).toBeUndefined()
  })
})
