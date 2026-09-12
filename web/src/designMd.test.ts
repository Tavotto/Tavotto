/**
 * 根目录 `DESIGN.md` 是给 impeccable / Stitch 读的**机器层索引**，不是第二份宪法：
 * 规矩在 `docs/ux/DESIGN_CONSTITUTION.md`，值在 `index.css` 的 `@theme`。两份文档
 * 长期漂移是 CLAUDE.md 明令反对的事，所以这里把 frontmatter 与 index.css **逐条对拍**：
 *
 *   1. 颜色：frontmatter 的每个 hex 等于 `--color-<key>`；反过来 `@theme` 里每个 hex
 *      颜色都在 frontmatter 里（闭集，两个方向都比；`color-mix()` 那两档不是 hex，不在内）；
 *   2. 圆角：`--radius-<key>` 闭集；
 *   3. 字体角色：`@utility type-<role>` 里 font-size / line-height / font-weight /
 *      letter-spacing 解析出来的值等于 frontmatter 的 typography.<role>；字体族等于
 *      `--font-sans` / `--font-mono`；
 *   4. 投影：正文里写的 `--shadow-pop` 值与 index.css 逐字相同；
 *   5. 宪法第一节颜色表里写了 hex 的行也对一遍——#330 里 selected 收浅到 #ebebe6 时
 *      那张表没跟上（`#e6e6e0`），说明「说明书写值」这件事本身就需要门禁。
 *
 * 改值先改 index.css，DESIGN.md 跟着改，同一次提交——这条用例就是那句话的门禁。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DESIGN_MD = readFileSync(path.resolve(HERE, '../../DESIGN.md'), 'utf8')
const INDEX_CSS = readFileSync(path.resolve(HERE, 'index.css'), 'utf8')

/* ------------------------------ frontmatter ------------------------------ */

const frontmatter = (() => {
  const m = DESIGN_MD.match(/^---\n([\s\S]*?)\n---\n/)
  if (!m) throw new Error('DESIGN.md 没有 frontmatter')
  return m[1]
})()

/** 顶层块（`colors:` 下面缩进两格的行），直到下一个顶层键 */
function block(name: string): string[] {
  const lines = frontmatter.split('\n')
  const start = lines.findIndex((l) => l === `${name}:`)
  if (start < 0) throw new Error(`frontmatter 里没有 ${name}:`)
  const out: string[] = []
  for (const l of lines.slice(start + 1)) {
    if (/^\S/.test(l)) break
    out.push(l)
  }
  return out
}

const unquote = (v: string) => v.trim().replace(/^"(.*)"$/, '$1')

/** `  key: "value"` 一层 */
function flatMap(name: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const l of block(name)) {
    const m = l.match(/^ {2}([a-z0-9-]+): (.+)$/)
    if (m) out[m[1]] = unquote(m[2])
  }
  return out
}

/** `  role:` + `    prop: value` 两层 */
function nestedMap(name: string): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {}
  let cur: string | null = null
  for (const l of block(name)) {
    const head = l.match(/^ {2}([a-z0-9-]+):$/)
    if (head) {
      cur = head[1]
      out[cur] = {}
      continue
    }
    const prop = l.match(/^ {4}([A-Za-z]+): (.+)$/)
    if (prop && cur) out[cur][prop[1]] = unquote(prop[2])
  }
  return out
}

/* -------------------------------- index.css -------------------------------- */

const theme = (() => {
  const m = INDEX_CSS.match(/@theme \{([\s\S]*?)\n\}/)
  if (!m) throw new Error('index.css 没有 @theme 块')
  return m[1]
})()

/** `--name: value;`（值可跨行，取到分号为止；注释先剥掉） */
function cssVars(src: string): Record<string, string> {
  const out: Record<string, string> = {}
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, '')
  for (const m of clean.matchAll(/--([a-z0-9*-]+):\s*([^;]+);/g)) {
    out[m[1]] = m[2].replace(/\s+/g, ' ').trim()
  }
  return out
}

const vars = cssVars(theme)

/** `@utility type-<role> { ... }` 里的声明 */
function utility(name: string): Record<string, string> {
  const m = INDEX_CSS.match(new RegExp(`@utility ${name} \\{([\\s\\S]*?)\\}`))
  if (!m) throw new Error(`index.css 没有 @utility ${name}`)
  const out: Record<string, string> = {}
  for (const d of m[1].matchAll(/([a-z-]+):\s*([^;]+);/g)) out[d[1]] = d[2].trim()
  return out
}

/** `var(--text-lg)` → `14px`；不是 var 的原样回 */
const resolve = (v: string): string => {
  const m = v.match(/^var\(--([a-z0-9-]+)\)$/)
  return m ? vars[m[1]] : v
}

/* ---------------------------------- 对拍 ---------------------------------- */

describe('DESIGN.md 的 frontmatter 是 index.css @theme 的镜像', () => {
  it('颜色：frontmatter 的每个 hex 等于 --color-<key>', () => {
    const colors = flatMap('colors')
    expect(Object.keys(colors).length).toBeGreaterThan(10)
    for (const [key, hex] of Object.entries(colors)) {
      expect(vars[`color-${key}`], `--color-${key} 在 index.css 里不存在`).toBeDefined()
      expect(vars[`color-${key}`], `--color-${key}`).toBe(hex)
    }
  })

  it('颜色：@theme 里每个 hex 颜色都登记在 frontmatter 里（闭集）', () => {
    const colors = flatMap('colors')
    const hexTokens = Object.entries(vars)
      .filter(([k, v]) => k.startsWith('color-') && /^#[0-9a-f]{6}$/i.test(v))
      .map(([k]) => k.slice('color-'.length))
    expect(hexTokens.length).toBeGreaterThan(10)
    const missing = hexTokens.filter((k) => !(k in colors))
    expect(missing, 'index.css 有、DESIGN.md 没有的颜色').toEqual([])
  })

  it('圆角：--radius-<key> 闭集', () => {
    const rounded = flatMap('rounded')
    const cssRadii = Object.entries(vars)
      .filter(([k, v]) => k.startsWith('radius-') && k !== 'radius-*' && v !== 'initial')
      .map(([k, v]) => [k.slice('radius-'.length), v])
    expect(Object.fromEntries(cssRadii)).toEqual(rounded)
  })

  it('字体角色：type-<role> 解析出的字号 / 行高 / 字重 / 字距等于 frontmatter', () => {
    const typo = nestedMap('typography')
    for (const role of ['title', 'section', 'body', 'control', 'caption', 'meta']) {
      const u = utility(`type-${role}`)
      const fm = typo[role]
      expect(fm, `frontmatter 缺 typography.${role}`).toBeDefined()
      expect(resolve(u['font-size']), `${role} font-size`).toBe(fm.fontSize)
      expect(resolve(u['line-height']), `${role} line-height`).toBe(String(fm.lineHeight))
      expect(u['font-weight'] ?? '400', `${role} font-weight`).toBe(String(fm.fontWeight))
      if (u['letter-spacing'] || fm.letterSpacing) {
        expect(u['letter-spacing'], `${role} letter-spacing`).toBe(fm.letterSpacing)
      }
      expect(fm.fontFamily, `${role} fontFamily`).toBe(vars['font-sans'])
    }
    expect(typo.mono.fontFamily).toBe(vars['font-mono'])
  })

  it('宪法第一节颜色表里写了 hex 的行，值与 index.css 相同（#330 里 selected 收浅到 #ebebe6 时表没跟上）', () => {
    const doc = readFileSync(path.resolve(HERE, '../../docs/ux/DESIGN_CONSTITUTION.md'), 'utf8')
    const section = doc.slice(doc.indexOf('## 一、颜色'), doc.indexOf('## 二、圆角'))
    const rows = [...section.matchAll(/^\|\s*[a-z0-9-]+\s*\|\s*`([a-z0-9-]+)`[^|]*\|\s*`(#[0-9a-fA-F]{6})`/gm)]
    expect(rows.length, '颜色表里一行带 hex 的都没解析到：判据恒真').toBeGreaterThan(8)
    for (const [, cls, hex] of rows) {
      expect(vars[`color-${cls}`], `宪法表 ${cls}`).toBe(hex.toLowerCase())
    }
  })

  it('投影：正文里写的 --shadow-pop 与 index.css 逐字相同', () => {
    const m = DESIGN_MD.match(/`--shadow-pop: ([^`]+)`/)
    expect(m, '正文里没写 --shadow-pop').toBeTruthy()
    expect(m![1]).toBe(vars['shadow-pop'])
  })

  it('索引不复制规矩：正文每一节都指向宪法', () => {
    const body = DESIGN_MD.slice(DESIGN_MD.indexOf('\n---\n', 4) + 5)
    for (const h of ['## Colors', '## Typography', '## Layout', '## Shapes', '## Components']) {
      const i = body.indexOf(h)
      expect(i, `缺 ${h}`).toBeGreaterThanOrEqual(0)
      const next = body.indexOf('\n## ', i + 1)
      const section = body.slice(i, next < 0 ? undefined : next)
      expect(section, `${h} 没有指向宪法`).toMatch(/宪法/)
    }
  })
})
