#!/usr/bin/env node
/**
 * 翻译资源的完整性检查。`pnpm i18n:check` 的主力。
 *
 * ## 为什么不是 `i18next-cli extract --ci` 一条命令了事
 *
 * 本项目里绝大多数调用点走的是**每个模块自己的短助手**：
 *
 *     const wb = (key, v) => translate(`writeBack.${key}`, { ns: 'inspector', ...v })
 *     wb('confirm')                     // → inspector:writeBack.confirm
 *
 * 命名空间与前缀固定在一处、调用点只写最后一段，可读性好得多；代价是 key 在
 * **运行时**才拼出来，官方提取器只看得到 `'confirm'` 那一截，于是既抽出半截
 * key（还会与真实的 `confirm.*` 撞成 nesting conflict），又把助手写的真 key
 * 当成「没人引用」。
 *
 * 所以这里自己解析：先把助手声明还原成「命名空间 + 前缀」，再按这张表解析
 * 调用点。覆盖范围**比 CLI 默认那套更大**，不是更小：
 *
 *   1. 两种语言的 key 集合是否一致；
 *   2. 代码里用到、资源里没有的 key（缺失）；
 *   3. 资源里有、代码里从没用过的 key（多余）；
 *   4. 空翻译；
 *   5. 两种语言的插值变量是否一致；
 *   6. 两种语言的复数形态是否配套。
 *
 * 硬编码字符串由 `i18next-cli lint` 查，生成的类型是否过期由
 * `i18next-cli types --ci` 查——三条一起构成 `i18n:check`。
 *
 * 退出码：有任何一类问题就是 1。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const LOCALES_DIR = path.join(WEB, 'src/i18n/locales')
const SRC = path.join(WEB, 'src')

const LOCALES = ['zh-CN', 'en-US']
const PRIMARY = 'zh-CN'
const NAMESPACES = [
  'common',
  'workspace',
  'project',
  'inspector',
  'dialogs',
  'errors',
  'ai',
  'shortcuts',
]
const DEFAULT_NS = 'common'

/**
 * 只在 `src/i18n/` 内部被引用、扫描器看不到的 key。
 * 每条都要写清为什么——这张表是「例外」，不是垃圾桶。
 */
const ALWAYS_USED = new Set([
  // literal() 造的描述符：用户自己的内容（文件名 / 画布名）原样透出时用它
  'common:literal',
])

/** i18next 的复数后缀；资源里的 `foo_one` 对应代码里的 `foo` */
const PLURAL_SUFFIXES = ['_zero', '_one', '_two', '_few', '_many', '_other']

const stripPlural = (key) => {
  for (const suffix of PLURAL_SUFFIXES) {
    if (key.endsWith(suffix)) return key.slice(0, -suffix.length)
  }
  return key
}
const pluralSuffixOf = (key) => PLURAL_SUFFIXES.find((s) => key.endsWith(s)) ?? null

/* --------------------------------- 资源 ------------------------------------ */

function flatten(obj, prefix = '', out = new Map()) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, out)
    else out.set(key, v)
  }
  return out
}

/** locale → ns → Map(flatKey → value) */
function readResources() {
  const res = {}
  for (const locale of LOCALES) {
    res[locale] = {}
    for (const ns of NAMESPACES) {
      const file = path.join(LOCALES_DIR, locale, `${ns}.json`)
      res[locale][ns] = fs.existsSync(file)
        ? flatten(JSON.parse(fs.readFileSync(file, 'utf8')))
        : new Map()
    }
  }
  return res
}

/* --------------------------------- 源码 ------------------------------------ */

function sourceFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      // src/i18n 自己不参与扫描（它定义机制，不消费 key）
      if (entry.name === 'i18n' && path.dirname(p) === SRC) continue
      if (entry.name === '__fixtures__') continue
      sourceFiles(p, out)
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(p)
    }
  }
  return out
}

/**
 * 从 `(` 后面开始，按括号 / 引号配平切出实参文本。
 * 正则做不到这件事：实参里有三元、对象字面量、模板串、嵌套调用。
 */
function splitArgs(src, openParen) {
  const args = []
  let depth = 0
  let start = openParen + 1
  let quote = null
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (quote) {
      if (c === '\\') i++
      else if (c === quote) quote = null
      continue
    }
    if (c === "'" || c === '"' || c === '`') {
      quote = c
      continue
    }
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') {
      if (c === ')' && depth === 0) {
        args.push(src.slice(start, i))
        return { args, end: i }
      }
      depth--
    } else if (c === ',' && depth === 0) {
      args.push(src.slice(start, i))
      start = i + 1
    }
  }
  return { args, end: -1 }
}

/**
 * 一段实参文本里**当作 key 用**的单引号字面量。
 *
 * 两类要排掉，否则会凭空多出一堆不存在的 key：
 *   * 模板串实参（`` `pre.${x}` ``）里的字面量都在 `${}` 表达式内部——那时
 *     只有静态前缀有意义，见 templatePrefixes；
 *   * 比较运算的另一侧（`cost === 'heavy' ? 'a' : 'b'` 里的 `'heavy'`）是
 *     **条件**不是 key。判据就是紧邻的 `===` / `!==` / `==` / `!=`。
 */
function literalsIn(text) {
  if (text.includes('`')) return []
  const out = []
  for (const m of text.matchAll(/'([^'\\]*)'/g)) {
    const before = text.slice(0, m.index).replace(/\s+$/, '')
    const after = text.slice(m.index + m[0].length).replace(/^\s+/, '')
    if (/[=!]==?$/.test(before)) continue
    if (/^[=!]==?/.test(after)) continue
    out.push(m[1])
  }
  return out
}

/**
 * 一段实参文本里模板串的**静态前缀**：`` `enum.${prop}.${value}` `` → `enum.`。
 * 前缀里再含 `${` 的一律不要（那不是前缀，是拼接中段）。
 */
function templatePrefixes(text) {
  const out = []
  for (const m of text.matchAll(/`([^`]*?)\$\{/g)) {
    if (!m[1].includes('${')) out.push(m[1])
  }
  return out
}

/** 实参是不是一个光秃秃的标识符（`el(key)` 里的 key） */
const isBareIdentifier = (text) => /^\s*[A-Za-z_$][\w$]*\s*$/.test(text)

/** 选项对象字面量里的 `ns: 'x'` */
const nsFromOptions = (text) => (text ? (text.match(/\bns:\s*'([\w-]+)'/) ?? [])[1] : undefined)

/**
 * 一个文件里声明的短助手：名字 → { ns, prefix }。
 *
 * 认三种写法（本仓库实际用到的全部形态）：
 *
 *     const x = (key: string, values?: …) => translate(`pre.${key}`, { ns: 'inspector', … })
 *     const x = (key: string): UiMessage => msg(`pre.${key}`, values, 'workspace')
 *     const x = (key: string) => t(key, { ns: 'shortcuts' })        // 前缀为空
 */
function helperTable(src) {
  const helpers = new Map()
  const head = String.raw`const\s+(\w+)\s*=\s*\([^)]*\)\s*(?::\s*[\w<>|\s]+)?\s*=>\s*`

  for (const m of src.matchAll(
    new RegExp(head + String.raw`(?:translate|t)\(\s*` + '`' + String.raw`([^` + '`' + String.raw`]*)\$\{key\}` + '`' + String.raw`\s*,\s*\{\s*ns:\s*'([\w-]+)'`, 'g'),
  )) {
    helpers.set(m[1], { prefix: m[2], ns: m[3] })
  }
  for (const m of src.matchAll(
    new RegExp(head + String.raw`(?:translate|t)\(\s*key\s*,\s*\{\s*ns:\s*'([\w-]+)'`, 'g'),
  )) {
    helpers.set(m[1], { prefix: '', ns: m[2] })
  }
  for (const m of src.matchAll(
    new RegExp(head + String.raw`msg\(\s*` + '`' + String.raw`([^` + '`' + String.raw`]*)\$\{key\}` + '`' + String.raw`\s*,\s*[^,]+,\s*'([\w-]+)'`, 'g'),
  )) {
    helpers.set(m[1], { prefix: m[2], ns: m[3] })
  }
  return helpers
}

/**
 * 一个文件里 `t` 的默认命名空间。
 *
 * 两个 `t` 必须分开看：
 *   * `const { t } = useTranslation('inspector')` —— hook 给的那个，默认 ns
 *     就是括号里写的；
 *   * `import { t } from '@/i18n'`（本仓库里常改名成 `translate`）—— 实例上的
 *     裸 t，不带 ns 选项时落在 defaultNS 上。
 */
function fileNsDefaults(src) {
  const hasHookT = /const\s*\{\s*t\s*[,}]/.test(src)
  let hookNs = DEFAULT_NS
  if (hasHookT) {
    const single = src.match(/useTranslation\(\s*'([\w-]+)'/)
    const multi = src.match(/useTranslation\(\s*\[\s*'([\w-]+)'/)
    hookNs = single?.[1] ?? multi?.[1] ?? DEFAULT_NS
  }
  return { t: hasHookT ? hookNs : DEFAULT_NS, translate: DEFAULT_NS }
}

/** `ns:key` → [ns, key]；没有前缀就用给定的默认 ns */
function splitNs(raw, fallbackNs) {
  const i = raw.indexOf(':')
  if (i < 0) return [fallbackNs, raw]
  const ns = raw.slice(0, i)
  return NAMESPACES.includes(ns) ? [ns, raw.slice(i + 1)] : [fallbackNs, raw]
}

/**
 * 扫一个文件里所有 key 的使用。
 *
 * 返回 { used: Set('ns:key'), prefixes: Set('ns:prefix') }。动态 key
 * （模板串带 `${}`、或实参就是个变量）解析不出完整 key，只记下静态前缀——
 * 「多余 key」检查据此放行整片前缀，而不是把 `inspector:prop.*` 全判成没人用。
 */
function scanFile(file) {
  const src = fs.readFileSync(file, 'utf8')
  const used = new Set()
  const prefixes = new Set()
  const helpers = helperTable(src)
  const defaults = fileNsDefaults(src)

  const names = ['t', 'translate', 'msg', 'nsMsg', ...helpers.keys()]
  const callRe = new RegExp(String.raw`\b(${names.join('|')})\s*\(`, 'g')

  for (const m of src.matchAll(callRe)) {
    const name = m[1]
    const open = m.index + m[0].length - 1
    const { args } = splitArgs(src, open)
    if (!args.length) continue

    if (name === 'nsMsg') {
      const ns = literalsIn(args[0])[0]
      if (!ns) continue
      for (const key of literalsIn(args[1] ?? '')) used.add(`${ns}:${key}`)
      for (const pre of templatePrefixes(args[1] ?? '')) prefixes.add(`${ns}:${pre}`)
      continue
    }

    if (name === 'msg') {
      // msg(key, values, ns)
      const ns = literalsIn(args[2] ?? '')[0] ?? DEFAULT_NS
      for (const raw of literalsIn(args[0])) {
        const [realNs, key] = splitNs(raw, ns)
        used.add(`${realNs}:${key}`)
      }
      for (const pre of templatePrefixes(args[0])) {
        const [realNs, p] = splitNs(pre, ns)
        prefixes.add(`${realNs}:${p}`)
      }
      if (isBareIdentifier(args[0])) prefixes.add(`${ns}:`)
      continue
    }

    if (name === 't' || name === 'translate') {
      const fallback = nsFromOptions(args[1]) ?? defaults[name]
      for (const raw of literalsIn(args[0])) {
        const [ns, key] = splitNs(raw, fallback)
        used.add(`${ns}:${key}`)
      }
      for (const pre of templatePrefixes(args[0])) {
        const [ns, p] = splitNs(pre, fallback)
        prefixes.add(`${ns}:${p}`)
      }
      if (isBareIdentifier(args[0])) prefixes.add(`${fallback}:`)
      continue
    }

    // 模块自己的短助手
    const { ns, prefix } = helpers.get(name)
    for (const key of literalsIn(args[0])) used.add(`${ns}:${prefix}${key}`)
    for (const pre of templatePrefixes(args[0])) prefixes.add(`${ns}:${prefix}${pre}`)
    if (isBareIdentifier(args[0])) prefixes.add(`${ns}:${prefix}`)
  }

  return { used, prefixes }
}

/* --------------------------------- 检查 ------------------------------------ */

const INTERP = /\{\{\s*([\w.]+)\s*(?:,[^}]*)?\}\}/g
const varsOf = (value) =>
  typeof value === 'string' ? new Set([...value.matchAll(INTERP)].map((m) => m[1])) : new Set()

function main() {
  const res = readResources()
  const problems = []
  const add = (kind, msg) => problems.push({ kind, msg })

  const used = new Set(ALWAYS_USED)
  const prefixes = new Set()
  for (const file of sourceFiles(SRC)) {
    const r = scanFile(file)
    for (const k of r.used) used.add(k)
    for (const p of r.prefixes) prefixes.add(p)
  }
  const coveredByPrefix = (nsKey) => [...prefixes].some((p) => nsKey.startsWith(p))

  /* --- 1. 两种语言的 key 集合一致 --- */
  for (const ns of NAMESPACES) {
    const a = res[PRIMARY][ns]
    for (const locale of LOCALES) {
      if (locale === PRIMARY) continue
      const b = res[locale][ns]
      for (const key of a.keys()) {
        if (!b.has(key)) add('missing-translation', `${locale} 缺 ${ns}:${key}`)
      }
      for (const key of b.keys()) {
        if (!a.has(key)) add('extra-translation', `${locale} 多出 ${ns}:${key}（${PRIMARY} 里没有）`)
      }
    }
  }

  /* --- 2. 空翻译 --- */
  for (const locale of LOCALES) {
    for (const ns of NAMESPACES) {
      for (const [key, value] of res[locale][ns]) {
        if (typeof value !== 'string' || value.trim() === '') {
          add('empty', `${locale} ${ns}:${key} 是空的`)
        }
      }
    }
  }

  /* --- 3. 插值变量一致 --- */
  for (const ns of NAMESPACES) {
    for (const [key, value] of res[PRIMARY][ns]) {
      const want = varsOf(value)
      for (const locale of LOCALES) {
        if (locale === PRIMARY || !res[locale][ns].has(key)) continue
        const got = varsOf(res[locale][ns].get(key))
        const missing = [...want].filter((v) => !got.has(v))
        const extra = [...got].filter((v) => !want.has(v))
        if (missing.length || extra.length) {
          add(
            'interpolation',
            `${ns}:${key} 插值变量不一致（${locale} 缺 [${missing}]、多 [${extra}]）`,
          )
        }
      }
    }
  }

  /* --- 4. 复数形态配套 --- */
  for (const ns of NAMESPACES) {
    const forms = new Map()
    for (const locale of LOCALES) {
      for (const key of res[locale][ns].keys()) {
        const suffix = pluralSuffixOf(key)
        if (!suffix) continue
        const base = stripPlural(key)
        if (!forms.has(base)) forms.set(base, new Map())
        const byLocale = forms.get(base)
        if (!byLocale.has(locale)) byLocale.set(locale, new Set())
        byLocale.get(locale).add(suffix)
      }
    }
    for (const [base, byLocale] of forms) {
      for (const locale of LOCALES) {
        const have = byLocale.get(locale)
        if (!have) {
          add('plural', `${ns}:${base} 在 ${locale} 里没有任何复数形态`)
          continue
        }
        if (!have.has('_other')) add('plural', `${ns}:${base} 在 ${locale} 里缺 _other`)
        if (locale === 'en-US' && !have.has('_one')) {
          add('plural', `${ns}:${base} 在 en-US 里缺 _one（英文必须区分单复数）`)
        }
        if (res[locale][ns].has(base)) {
          add('plural', `${ns}:${base} 在 ${locale} 里同时存在基键与复数形态`)
        }
      }
    }
  }

  /* --- 5. 代码用到、资源里没有 --- */
  for (const nsKey of used) {
    const [ns, key] = nsKey.split(/:(.+)/)
    if (!NAMESPACES.includes(ns) || !key) continue
    const table = res[PRIMARY][ns]
    if (table.has(key) || PLURAL_SUFFIXES.some((s) => table.has(`${key}${s}`))) continue
    add('missing-key', `代码里用了 ${ns}:${key}，${PRIMARY} 资源里没有`)
  }

  /* --- 6. 资源里有、代码里从没用过 --- */
  for (const ns of NAMESPACES) {
    const seen = new Set()
    for (const key of res[PRIMARY][ns].keys()) {
      const base = stripPlural(key)
      const nsKey = `${ns}:${base}`
      if (seen.has(nsKey)) continue
      seen.add(nsKey)
      if (used.has(nsKey) || coveredByPrefix(nsKey)) continue
      add('unused', `${nsKey} 在资源里，但代码里找不到引用`)
    }
  }

  /* --------------------------------- 报告 ---------------------------------- */

  const counts = Object.fromEntries(
    LOCALES.map((l) => [l, NAMESPACES.reduce((n, ns) => n + res[l][ns].size, 0)]),
  )
  console.log('i18n 资源统计：')
  for (const locale of LOCALES) console.log(`  ${locale}: ${counts[locale]} 条`)
  console.log(`  命名空间: ${NAMESPACES.join(', ')}`)
  console.log(`  代码里解析出的 key: ${used.size} 条；动态前缀: ${prefixes.size} 个`)

  if (!problems.length) {
    console.log('\n✔ 翻译资源检查通过')
    return 0
  }

  const byKind = new Map()
  for (const p of problems) {
    if (!byKind.has(p.kind)) byKind.set(p.kind, [])
    byKind.get(p.kind).push(p.msg)
  }
  console.error('\n✘ 翻译资源检查未通过：')
  for (const [kind, msgs] of byKind) {
    console.error(`\n  [${kind}] ${msgs.length} 条`)
    for (const m of msgs.slice(0, 40)) console.error(`    - ${m}`)
    if (msgs.length > 40) console.error(`    …另有 ${msgs.length - 40} 条`)
  }
  return 1
}

process.exit(main())
