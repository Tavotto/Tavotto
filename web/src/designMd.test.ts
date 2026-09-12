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
 *      那张表没跟上（`#e6e6e0`），说明「说明书写值」这件事本身就需要门禁；
 *   6. `spacing` 与 `components`（评审 P2：frontmatter 里每一块机器可读的数据都得有
 *      对拍对象，否则就是没人守的第二份真值）：`spacing.control` 等于 `Button` 的
 *      唯一高度档、`setting-row` 等于 `SettingRow` 的行高；每个组件的
 *      `{colors.x}` / `{rounded.x}` / `{spacing.x}` 引用都解析得到，并且换算成
 *      Tailwind 类之后真的出现在那个组件的类串里（`rounded.sm` → `rounded-sm`、
 *      `28px` → `h-7`、`{colors.ink}` 做底色 → `bg-ink`）。没有对拍对象的字段
 *      （gap-sm / gap-md、mono 的字号）已从 frontmatter 删掉，不留没人守的数据。
 *
 * 改值先改 index.css / 组件，DESIGN.md 跟着改，同一次提交——这条用例就是那句话的门禁。
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

  it('ink-3 的对比度说明与 index.css 的实测一致：画布灰上不达标，正文不许说「所有底色」', () => {
    // index.css 的注释是权威：ink-3 白 5.37 / surface-2 5.00 / bg 4.78 过线，canvas 4.45 不过
    expect(INDEX_CSS).toMatch(/--color-canvas 上只有\s*4\.45:1/)
    const line = DESIGN_MD.split('\n').find((l) => l.includes('Ink-3') && l.includes('Ink-faint'))!
    expect(line, '正文里没有 Ink-3 那一行').toBeTruthy()
    expect(line).toMatch(/4\.45:1/)
    expect(line).not.toMatch(/前两档在所有底色上/)
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

/* ------------------------- spacing / components 对拍 ------------------------- */

const ui = (rel: string) => readFileSync(path.resolve(HERE, 'components', rel), 'utf8')

/** Tailwind 的 4px 栅格：28px → h-7 */
const px = (v: string): number => {
  const m = v.match(/^(\d+)px$/)
  if (!m) throw new Error(`不是像素值：${v}`)
  return Number(m[1])
}
const stepOf = (v: string) => `${px(v) / 4}`

/** `{colors.ink}` → colors 块里 ink 的值；不是引用的原样回 */
function deref(value: string): { value: string; ref?: [string, string] } {
  const m = value.match(/^\{([a-z]+)\.([a-z0-9-]+)\}$/)
  if (!m) return { value }
  const table = flatMap(m[1])
  if (!(m[2] in table)) throw new Error(`引用解析不到：${value}`)
  return { value: table[m[2]], ref: [m[1], m[2]] }
}

/** 一条 frontmatter 声明换算成它在组件类串里必须出现的 Tailwind 类 */
function expectedClasses(prop: string, raw: string): string[] {
  const { value, ref } = deref(raw)
  switch (prop) {
    case 'rounded':
      if (value === '9999px') return ['rounded-full']
      if (!ref || ref[0] !== 'rounded') throw new Error(`rounded 只认 {rounded.x} 或 9999px：${raw}`)
      return [`rounded-${ref[1]}`]
    case 'height':
      return [`h-${stepOf(value)}`]
    case 'size':
      return [`h-${stepOf(value)}`, `w-${stepOf(value)}`]
    case 'backgroundColor':
      if (!ref || ref[0] !== 'colors') throw new Error(`backgroundColor 只认 {colors.x}：${raw}`)
      return [`bg-${ref[1]}`]
    case 'textColor':
      if (value === '#ffffff') return ['text-white']
      if (!ref || ref[0] !== 'colors') throw new Error(`textColor 只认 {colors.x} 或 #ffffff：${raw}`)
      return [`text-${ref[1]}`]
    default:
      throw new Error(`components 里出现了对拍表不认识的属性：${prop}`)
  }
}

/** 组件 → 它的类串权威（源码里那段字面量；找不到就抛，别让判据恒真） */
const COMPONENT_CLASSES: Record<string, () => string> = {
  'button-primary': () => buttonVariant('primary') + ' ' + buttonSize('sm'),
  'button-secondary': () => buttonVariant('secondary') + ' ' + buttonSize('sm'),
  'button-ghost': () => buttonVariant('ghost') + ' ' + buttonSize('sm'),
  'button-danger': () => buttonVariant('danger') + ' ' + buttonSize('sm'),
  'icon-button': () => buttonSize('icon'),
  input: () => block1(ui('ui/Input.tsx'), /const BOX_CLASS = cn\(([\s\S]*?)\)\n/) + ' ' + literal(ui('ui/Input.tsx'), /'(h-7 w-full[^']*)'/),
  badge: () => literal(ui('ui/Badge.tsx'), /'(inline-flex h-\d[^']*)'/),
  menu: () => block1(ui('ui/Menu.tsx'), /const CONTENT_CLASS = cn\(([\s\S]*?)\)\n/),
  dialog: () => literal(ui('ui/Dialog.tsx'), /'([^']*rounded-\w+ border border-border bg-surface shadow-pop[^']*)'/),
}

function literal(src: string, re: RegExp): string {
  const m = src.match(re)
  if (!m) throw new Error(`源码里找不到类串：${re}`)
  return m[1]
}
const block1 = (src: string, re: RegExp) => literal(src, re).replace(/['\n,]/g, ' ')
function buttonVariant(name: string): string {
  const table = literal(ui('ui/Button.tsx'), /const VARIANTS: Record<Variant, string> = \{([\s\S]*?)\n\}/)
  return literal(table, new RegExp(`\\b${name}:\\s*('[^']*'(?:\\s*\\+\\s*'[^']*')*|\\n\\s*'[^']*')`)).replace(/['\n+]/g, ' ')
}
function buttonSize(name: string): string {
  const table = literal(ui('ui/Button.tsx'), /const SIZES: Record<Size, string> = \{([\s\S]*?)\n\}/)
  return literal(table, new RegExp(`(?:^|\\n)\\s*'?${name}'?:\\s*'([^']*)'`))
}
const hasClass = (classes: string, cls: string) => classes.split(/\s+/).includes(cls)

describe('DESIGN.md 的 spacing / components 是组件源码的镜像', () => {
  it('spacing.control 等于 Button 唯一那档高度；setting-row 等于 SettingRow 的行高', () => {
    const spacing = flatMap('spacing')
    expect(Object.keys(spacing).sort()).toEqual(['control', 'setting-row'])
    const sizes = literal(ui('ui/Button.tsx'), /const SIZES: Record<Size, string> = \{([\s\S]*?)\n\}/)
    const heights = [...sizes.matchAll(/\bh-(\d+)\b/g)].map((m) => m[1])
    expect(heights.length, 'SIZES 里一条 h- 都没解析到：判据恒真').toBeGreaterThanOrEqual(4)
    expect(new Set(heights), 'Button 不止一档高度').toEqual(new Set([stepOf(spacing.control)]))
    expect(ui('settings/SettingRow.tsx')).toContain(`min-h-${stepOf(spacing['setting-row'])} `)
  })

  it('components 里每条引用都解析得到，换算成的 Tailwind 类真的在那个组件的类串里', () => {
    const components = nestedMap('components')
    expect(Object.keys(components).length).toBeGreaterThan(5)
    for (const [name, props] of Object.entries(components)) {
      const classes = COMPONENT_CLASSES[name]
      expect(classes, `${name} 没有对拍对象：要么补，要么从 frontmatter 删掉`).toBeDefined()
      const src = classes()
      expect(Object.keys(props).length, `${name} 一条声明都没有`).toBeGreaterThan(0)
      for (const [prop, raw] of Object.entries(props)) {
        for (const cls of expectedClasses(prop, raw)) {
          expect(hasClass(src, cls), `${name}.${prop} = ${raw} → 组件类串里没有 ${cls}（${src.trim()}）`).toBe(true)
        }
      }
    }
  })

  it('自检：引用解析与换算抓得住反例', () => {
    expect(() => deref('{colors.no-such}')).toThrow(/解析不到/)
    expect(expectedClasses('height', '{spacing.control}')).toEqual(['h-7'])
    expect(expectedClasses('rounded', '9999px')).toEqual(['rounded-full'])
    expect(() => expectedClasses('padding', '4px')).toThrow(/不认识/)
    expect(hasClass('rounded-sm h-7', 'rounded-s')).toBe(false)
  })
})
