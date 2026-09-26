/**
 * 页面 pt 透镜（`stylePresets.pagePtLens`）：属性页、样式面板、浮动工具条读写图内 pt 量的唯一换算。
 *
 * 钉住的是用户拍板的那句话「统一按页面上的实际大小显示」背后的数字纪律：
 *
 * 1. 往返：输入一个页面值 → 写进 override 的脚本值 → 读回来显示，仍是同一个数。缩放比 ≤ 1 时
 *    对**每一个**两位小数的页面值成立；> 1 时页面上能表示的值间隔是 0.01 × 缩放比（manifest
 *    按两位小数回报脚本值），对每个可表示的值成立；其余真实渲染出来的值离输入 ≤ 0.005 × 缩放比，
 *    显示出来差一格（0.01）。
 * 2. 三处同一个数：属性页 / 样式面板读的是 `toPage(脚本值)`，问题面板读的是预检的
 *    `r2(manifest 值 × panelScale)`——写进去的脚本值本来就是两位小数，manifest 报回来的就是它，
 *    所以两条路算出同一个数（这里直接拿预检的 `panelScale` 对拍）。
 * 3. 上下界：约束的是脚本值（引擎字段的 min / max），换到页面上显示与钳位——只乘不取整。
 * 4. 缩放比为 1 时原样进出（同一个对象、不引入一次多余的取整）。
 */
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import type { EditableField } from './api'
import { panelScale } from './preflight'
import { PAGE_PT_PROPS, pageField, pagePtLens, PT_DECIMALS, styleLens } from './stylePresets'
import type { PanelObject } from '@/types/document'

/** 原生 80 mm 宽、页面上 `80 × k` mm 宽的面板 */
const panelAt = (k: number, nativeW = 80): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW,
    nativeH: 60,
    x: 0,
    y: 0,
    w: 80 * k,
    h: 60 * k,
    overrides: [],
  }) as PanelObject

/** 问题面板那条路：预检量的是 manifest 值 × panelScale，消息里给两位小数 */
const preflightShows = (script: number, panel: PanelObject) =>
  Number((script * panelScale(panel)).toFixed(2))

/** 两位小数的页面值 1.00 … 40.00 */
const PAGE_VALUES = Array.from({ length: 3901 }, (_, i) => (i + 100) / 100)

describe('往返：输入的页面值写进去、读回来，仍是同一个数', () => {
  for (const k of [0.6, 0.75]) {
    it(`缩放比 ${k}：每一个两位小数的页面值都原样回来，问题面板也是这个数`, () => {
      const lens = pagePtLens(panelAt(k))
      const bad: number[] = []
      for (const p of PAGE_VALUES) {
        const script = lens.toScript('fontsize', p) as number
        if (lens.toPage('fontsize', script) !== p) bad.push(p)
        if (preflightShows(script, panelAt(k)) !== p) bad.push(p)
      }
      expect(bad).toEqual([])
    })
  }

  it('缩放比 1.33：可表示的值原样回来；其余渲染偏差 ≤ 0.005 × 缩放比、显示差一格，三处仍是同一个数', () => {
    const k = 1.33
    const panel = panelAt(k)
    const lens = pagePtLens(panel)
    // 与显示同一种取整（`toFixed`，问题面板的 `eff.toFixed(2)` 也是它）
    const r2 = (v: number) => Number(v.toFixed(2))
    let reachable = 0
    for (const p of PAGE_VALUES) {
      const script = lens.toScript('fontsize', p) as number
      const back = lens.toPage('fontsize', script) as number
      // 属性页 / 样式面板与问题面板读同一个脚本值，永远同一个数
      expect(preflightShows(script, panel), `p=${p}`).toBe(back)
      // 有没有一个两位小数的脚本值能表示 p：有就必须正好回到 p（就近取整不许错过它）
      const lo = Math.floor((p / k) * 100) / 100
      const canHit = [lo, r2(lo + 0.01)].some((s) => r2(s * k) === p)
      if (canHit) {
        reachable++
        expect(back, `p=${p}`).toBe(p)
      } else {
        // 真实渲染出来的页面字号离输入 ≤ 0.005 × 缩放比；显示按两位小数，差一格（0.01）
        expect(Math.abs(script * k - p), `p=${p}`).toBeLessThanOrEqual(0.005 * k + 1e-9)
        expect(Math.abs(back - p), `p=${p}`).toBeLessThanOrEqual(0.01 + 1e-9)
      }
    }
    expect(reachable, '夹具该覆盖到大多数值').toBeGreaterThan(2500)
  })

  it('缩放比 1.33 的两个关键值：8.5 原样回来；8.0 不可表示，回显 8.01（真实渲染 8.0066 pt）', () => {
    const lens = pagePtLens(panelAt(1.33))
    expect(lens.toScript('fontsize', 8.5)).toBe(6.39)
    expect(lens.toPage('fontsize', 6.39)).toBe(8.5)
    expect(lens.toScript('fontsize', 8)).toBe(6.02)
    expect(lens.toPage('fontsize', 6.02)).toBe(8.01)
  })
})

describe('上下界与取整', () => {
  const size: EditableField = { prop: 'fontsize', type: 'number', value: 10, min: 3, max: 36, step: 0.5, unit: 'pt' }

  it('字段的当前值换到页面上；上下界只乘不取整（约束的是脚本值）', () => {
    const f = pagePtLens(panelAt(0.6)).field(size)
    expect(f.value).toBe(6)
    expect(f.min).toBeCloseTo(1.8, 12)
    expect(f.max).toBeCloseTo(21.6, 12)
    expect(f.step, '步长是手感，不换算').toBe(0.5)
    // 钳到页面下界再换回去，正好落在脚本下界上
    expect(pagePtLens(panelAt(0.6)).toScript('fontsize', f.min)).toBe(3)
  })

  it('缩放比为 1：原样进出，字段是同一个对象（memo 不白白失效）', () => {
    const lens = pagePtLens(panelAt(1))
    expect(lens.field(size)).toBe(size)
    expect(lens.toScript('fontsize', 8.333)).toBe(8.333)
    expect(lens.toPage('fontsize', 8.333)).toBe(8.333)
    expect(lens.bound('linewidth', 12)).toBe(12)
  })

  it('表外的量不换算：颜色、透明度、散点面积（pt²，按平方缩放）', () => {
    const lens = pagePtLens(panelAt(0.6))
    expect(lens.toPage('alpha', 0.5)).toBe(0.5)
    expect(lens.toScript('color', '#000000')).toBe('#000000')
    const area: EditableField = { prop: 'size', type: 'number', value: 36, unit: 'pt²' }
    expect(lens.field(area)).toBe(area)
    expect(PAGE_PT_PROPS.has('size')).toBe(false)
  })

  it('应用样式用同一个透镜：pt_basis=page 的样式按缩放比换算，旧样式（没有标记）原样写', () => {
    const panel = panelAt(0.6)
    expect(styleLens({ pt_basis: 'page' }, panel).toScript('fontsize', 9)).toBe(15)
    expect(styleLens({}, panel).toScript('fontsize', 9)).toBe(9)
    expect(styleLens({}, panel).toPage('fontsize', 15)).toBe(15)
  })

  it('三维轴箭头的箭头大小（arrow_head，引擎里就是 mutation_scale）与相邻的箭头线宽同一种单位', () => {
    const lens = pagePtLens(panelAt(0.5))
    expect(lens.toPage('arrow_head', 6)).toBe(3)
    expect(lens.toPage('mutation_scale', 6)).toBe(3)
    expect(lens.toPage('arrow_width', 0.8)).toBe(0.4)
  })

  it('边框卡的「全部」与逐边是同一种单位（一张卡里不许一个页面值一个脚本值）', () => {
    const lens = pagePtLens(panelAt(0.5))
    for (const side of ['top', 'right', 'bottom', 'left']) {
      expect(lens.toPage(`spine_${side}_linewidth`, 1)).toBe(lens.toPage('spine_linewidth', 1))
    }
  })

  it('缩放比算不出来（nativeW 缺失）：按 1 显示脚本值——与预检的兜底同一个数', () => {
    const broken = { ...panelAt(0.6), nativeW: 0 }
    const lens = pagePtLens(broken)
    expect(lens.scale).toBe(1)
    expect(panelScale(broken)).toBe(1)
    expect(lens.toPage('fontsize', 10)).toBe(10)
  })

  it('取整位数只有一个数：换算与数字框的显示都是两位', () => {
    expect(PT_DECIMALS).toBe(2)
    expect(pageField(size, 0.875).value).toBe(8.75)
  })
})

/**
 * 换算只有一处：界面与 store（样式绑定 `styleBinding` 与设置页都是 `withPageBasis` 的调用方，只打口径标记，
 * 不做乘除）**不许自己拿缩放比做乘除**——拿到 `panelScale` / `toPageValue` / `toScriptValue` / `pageField`
 * 的文件就是第二份换算的起点（属性页以前正是没有这一步才与样式面板差出 0.6 倍）。界面一律过写入器，
 * 写入器过 `pagePtLens`。
 *
 * 判的是 AST，不是子串（注释里提到这几个名字不算）。主语是「**值能不能从换算模块流出去**」，
 * 判据按下面三条收口（#557 评审三轮 P1：namespace import、只扫三个目录、转出口断链，都是同一个
 * 「只量了一部分流法」）：
 *
 * 1. **扫描面 = `src/` 下全部生产源码**，只做显式排除（`EXCLUDED`：测试文件、`.d.ts`），不按目录列白名单；
 *    换算模块本身与豁免文件**也在扫描面里**，只是换了判法（见 2、3）。
 * 2. **碰到换算模块的地方就地判**：定义者（`src/` 里顶层声明了这几个名字的模块，现场认出来、再与期望
 *    的两个对拍）之外的任何生产文件，从定义者取值——具名（按原名，`as` 改名不豁免）、`{ default as x }`、
 *    default、namespace、`import x = require()`、`require()`、`import()`（字面量或拼出来的路径）、命中定义者的
 *    `import.meta.glob`（或模式不是字面量）——一律红；`export … from 定义者` 转出换算名也红。豁免只认
 *    `ALLOW` 里点名的具名 import。纯类型 import、纯副作用 import、取定义者的其它导出（`pagePtLens`）不算。
 * 3. **合法拿到的绑定不许再流出去**（定义者自己的声明、定义者之间的 import、豁免文件的 import）：绑定只许
 *    出现在被调用的位置（`panelScale(p)`、`ns.panelScale(p)`），其余一切出现——`export { b }` / `export default b`、
 *    `const y = b`、`{ b }`、`f(b)`、`ns.panelScale` 不调用、`ns['panelScale']`、`new b()`——按逃逸判红。
 *    定义者用 `export { toPageValue }` 原名导出自己的声明不算逃逸。
 *
 * 为什么这样就闭合：值要到达任何文件，第一跳必然是「某个文件从定义者取值」。第一跳在非豁免文件里 → 2 当场红；
 * 在定义者 / 豁免文件里 → 取到的绑定只能被调用，流不出去（3）。于是转出链（`export default scale` 再转、
 * namespace 再转）不需要沿链追：链的第一个转出口就是红的。
 *
 * 盲点（静态看不到的，写在明处，样本里标 blind）：定义者与豁免文件里**调用**换算函数的包装再导出
 * （`export const s = (p) => panelScale(p)`——定义者本来就是换算的唯一出处，豁免文件按名审过）；不 import、
 * 自己重写一遍乘除；`eval` / `globalThis` 这类运行时取值；测试文件与 `.d.ts`（不是生产代码）。判据按名字、
 * 不做作用域分析——同名的影子变量只会让它偏严（误红），不会漏。
 */
const FORBIDDEN = new Set(['panelScale', 'toPageValue', 'toScriptValue', 'pageField'])

type Sources = Record<string, string>

/** 扫描面的显式排除：不是生产代码的 */
const EXCLUDED: ReadonlyArray<readonly [string, (path: string) => boolean]> = [
  ['测试文件', (p) => /\.test\.tsx?$/.test(p)],
  ['类型声明（没有值）', (p) => p.endsWith('.d.ts')],
]
const isProduction = (path: string) => /\.tsx?$/.test(path) && !EXCLUDED.some(([, hit]) => hit(path))

/**
 * 豁免（只认具名 import 的原名；namespace / default / 转出 / 逃逸不给豁免）
 */
const ALLOW: Record<string, string[]> = {
  // 样式对话框把缩放比交给 `extractFromManifest`（提取在 `stylePresets` 里换算，对话框自己不乘）
  '/src/components/StyleDialog.tsx': ['panelScale'],
  // 规范修复事务（ADR 0080）：缩放比原样随请求交给后端去判，自己不换算任何页面 pt 值
  '/src/store/issueFixActions.ts': ['panelScale'],
  // 写入器本身：按缩放比 memo 住换好的字段表（与 `lens.field` 同一个函数，不是第二份换算）
  '/src/components/inspector/textStyleAdapter.ts': ['pageField'],
  // 样式面板的纯计算模型：uniform 读数换成页面值，调的就是透镜底下那个 `toPageValue`（同一个函数）
  '/src/lib/stylePanelModel.ts': ['toPageValue'],
}

/**
 * 路径看不见的 `import()` 的豁免：文件 → 允许几处（多一处就红）。只收指向 `src/` 之外的——
 * 构建产物里的源码模块没有可寻址的 URL，这类加载拿不到换算模块。
 */
const OPAQUE_IMPORT_ALLOW: Record<string, number> = {
  // 从运行时给的 CDN 基址加载 Pyodide 自己的 `pyodide.mjs`（`${pyodideBaseUrl}pyodide.mjs`）
  '/src/playground/pyodide.worker.ts': 1,
}

const parse = (path: string, src: string) =>
  ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

/** import 路径 → `src/` 里的模块键（`/src/lib/preflight`，去扩展名、去 `/index`）；包名回 null */
function resolveModule(from: string, spec: string): string | null {
  let parts: string[]
  if (spec.startsWith('@/')) parts = ['', 'src', ...spec.slice(2).split('/')]
  else if (spec.startsWith('/')) parts = spec.split('/')
  else if (spec.startsWith('./') || spec.startsWith('../')) {
    parts = from.split('/').slice(0, -1)
    for (const seg of spec.split('/')) {
      if (seg === '.' || seg === '') continue
      if (seg === '..') parts.pop()
      else parts.push(seg)
    }
  } else return null
  return parts
    .join('/')
    .replace(/\.(tsx?|jsx?|mjs)$/, '')
    .replace(/\/index$/, '')
}

const moduleKey = (path: string) => path.replace(/\.tsx?$/, '').replace(/\/index$/, '')

const specText = (node: ts.Node | undefined) =>
  node && ts.isStringLiteralLike(node) ? node.text : undefined

/** vite 的 glob 模式 → 正则（`**`、`*`、`{a,b}`）；只用来判「会不会命中定义者」 */
function globRegex(from: string, pattern: string): RegExp {
  const abs = pattern.startsWith('@/')
    ? `/src/${pattern.slice(2)}`
    : pattern.startsWith('.')
      ? `${from.split('/').slice(0, -1).join('/')}/${pattern}`.replace(/\/\.\//g, '/')
      : pattern
  let re = ''
  for (let i = 0; i < abs.length; i++) {
    const c = abs[i]
    if (c === '*' && abs[i + 1] === '*') {
      re += abs[i + 2] === '/' ? '(?:.*/)?' : '.*'
      i += abs[i + 2] === '/' ? 2 : 1
    } else if (c === '*') re += '[^/]*'
    else if (c === '?') re += '[^/]'
    else if (c === '{') re += '(?:'
    else if (c === '}') re += ')'
    else if (c === ',') re += '|'
    else re += c.replace(/[.+^$()|[\]\\]/g, '\\$&')
  }
  return new RegExp(`^${re}$`)
}

/** 定义者：顶层声明了换算名的生产模块（模块键 → 文件路径） */
function definersOf(sources: Sources): Map<string, string> {
  const out = new Map<string, string>()
  for (const [path, src] of Object.entries(sources)) {
    if (!isProduction(path)) continue
    if (ownDefinitions(parse(path, src)).size > 0) out.set(moduleKey(path), path)
  }
  return out
}

function ownDefinitions(file: ts.SourceFile): Set<string> {
  const own = new Set<string>()
  for (const st of file.statements) {
    if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st)) && st.name && FORBIDDEN.has(st.name.text)) {
      own.add(st.name.text)
    }
    if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) {
        if (ts.isIdentifier(d.name) && FORBIDDEN.has(d.name.text)) own.add(d.name.text)
      }
    }
  }
  return own
}

const isConversionName = (name: string) => FORBIDDEN.has(name) || name === 'default'

/** 这个名字出现的位置是不是「声明 / 属性名 / 类型」——不是对绑定的一次取值 */
function notAValueUse(node: ts.Identifier): boolean {
  const p = node.parent
  if (
    ts.isImportSpecifier(p) ||
    ts.isImportClause(p) ||
    ts.isNamespaceImport(p) ||
    (ts.isImportEqualsDeclaration(p) && p.name === node)
  ) {
    return true
  }
  if ((ts.isFunctionDeclaration(p) || ts.isClassDeclaration(p)) && p.name === node) return true
  if ((ts.isVariableDeclaration(p) || ts.isParameter(p) || ts.isBindingElement(p)) && p.name === node) return true
  if (ts.isPropertyAccessExpression(p) && p.name === node) return true
  if ((ts.isPropertyAssignment(p) || ts.isPropertyDeclaration(p) || ts.isMethodDeclaration(p)) && p.name === node) {
    return true
  }
  if (ts.isPropertySignature(p) || ts.isQualifiedName(p)) return true
  for (let a: ts.Node | undefined = p; a; a = a.parent) if (ts.isTypeNode(a)) return true
  return false
}

/** 一个生产文件里值从换算模块流出来的每一处 */
function conversionHits(
  path: string,
  src: string,
  defs: Map<string, string>,
  allow: readonly string[] = [],
  opaqueAllowed = 0,
): string[] {
  const hits: string[] = []
  const opaque: string[] = []
  const file = parse(path, src)
  const isDefiner = defs.has(moduleKey(path))
  const toDefiner = (spec: string | undefined) =>
    spec !== undefined && defs.has(resolveModule(path, spec) ?? '')
  const hit = (what: string) => hits.push(`${path}: ${what}`)
  /** 本文件里合法拿到、指向换算函数（或装着它的命名空间）的绑定 */
  const own = isDefiner ? ownDefinitions(file) : new Set<string>()
  const bindings = new Set(own)

  for (const st of file.statements) {
    if (ts.isImportDeclaration(st) && toDefiner(specText(st.moduleSpecifier))) {
      const clause = st.importClause
      if (!clause || clause.isTypeOnly) continue // 纯副作用 / 纯类型：拿不到值
      if (clause.name) {
        bindings.add(clause.name.text)
        if (!isDefiner) hit(`default import ${clause.name.text}`)
      }
      const nb = clause.namedBindings
      if (nb && ts.isNamespaceImport(nb)) {
        bindings.add(nb.name.text)
        if (!isDefiner) hit(`import * as ${nb.name.text}`)
      }
      if (nb && ts.isNamedImports(nb)) {
        for (const el of nb.elements) {
          const name = (el.propertyName ?? el.name).text
          if (el.isTypeOnly || !isConversionName(name)) continue
          bindings.add(el.name.text)
          if (!isDefiner && !allow.includes(name)) hit(`import { ${name} }`)
        }
      }
    } else if (
      ts.isImportEqualsDeclaration(st) &&
      !st.isTypeOnly &&
      ts.isExternalModuleReference(st.moduleReference) &&
      toDefiner(specText(st.moduleReference.expression))
    ) {
      bindings.add(st.name.text)
      if (!isDefiner) hit(`import ${st.name.text} = require()`)
    } else if (
      ts.isExportDeclaration(st) &&
      st.moduleSpecifier &&
      !st.isTypeOnly &&
      toDefiner(specText(st.moduleSpecifier))
    ) {
      const clause = st.exportClause
      if (!clause || ts.isNamespaceExport(clause)) hit('export * from 换算模块')
      else {
        for (const el of clause.elements) {
          const name = (el.propertyName ?? el.name).text
          if (!el.isTypeOnly && isConversionName(name)) hit(`export { ${name} } from 换算模块`)
        }
      }
    }
  }

  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const callee = node.expression
      const arg = node.arguments[0]
      // import() / require()：路径看不见（不是字面量）就按红算
      if (callee.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(callee) && callee.text === 'require')) {
        const spec = specText(arg)
        if (spec === undefined) opaque.push(`${path}: import(<非字面量>)`)
        else if (toDefiner(spec)) hit(`import('${spec}')`)
      }
      if (
        ts.isPropertyAccessExpression(callee) &&
        ts.isMetaProperty(callee.expression) &&
        callee.name.text.startsWith('glob')
      ) {
        const pats = arg && ts.isArrayLiteralExpression(arg) ? [...arg.elements] : arg ? [arg] : []
        const literal = pats.map((e) => specText(e))
        if (pats.length === 0 || literal.some((s) => s === undefined)) hit('import.meta.glob(<非字面量>)')
        else {
          const res = literal.filter((s): s is string => !s!.startsWith('!')).map((s) => globRegex(path, s))
          if ([...defs.values()].some((f) => res.some((r) => r.test(f)))) hit('import.meta.glob 命中换算模块')
        }
      }
    }
    if (ts.isIdentifier(node) && bindings.has(node.text) && !notAValueUse(node)) {
      const p = node.parent
      const called = ts.isCallExpression(p) && p.expression === node
      const nsCalled =
        ts.isPropertyAccessExpression(p) &&
        p.expression === node &&
        ts.isCallExpression(p.parent) &&
        p.parent.expression === p
      if (ts.isExportSpecifier(p)) {
        const local = (p.propertyName ?? p.name) === node
        const sameName = p.name.text === node.text
        if (local && !(own.has(node.text) && sameName)) hit(`${node.text} 转出（export { … }）`)
      } else if (!called && !nsCalled) {
        hit(`${node.text} 逃逸（${ts.SyntaxKind[p.kind]}）`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  // 路径看不见的加载：超出点名的处数就全部报出来（不猜是哪一处新加的）
  if (opaque.length > opaqueAllowed) hits.push(...opaque)
  return hits
}

/** 全部生产源码一起判 */
function scanAll(sources: Sources): { hits: string[]; scanned: string[]; defs: Map<string, string> } {
  const defs = definersOf(sources)
  const hits: string[] = []
  const scanned: string[] = []
  for (const [path, src] of Object.entries(sources)) {
    if (!isProduction(path)) continue
    scanned.push(path)
    hits.push(...conversionHits(path, src, defs, ALLOW[path], OPAQUE_IMPORT_ALLOW[path]))
  }
  return { hits, scanned, defs }
}

describe('界面代码与 store 不自己做页面 pt 换算', () => {
  const ALL = import.meta.glob('/src/**/*.{ts,tsx}', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Sources
  const REAL = scanAll(ALL)
  const topDir = (p: string) => (p.split('/').length > 3 ? p.split('/')[2] : '(src 根)')

  it('定义者是从源码里认出来的，恰好是期望的两处（多出一处 = 多了一份换算）', () => {
    expect([...REAL.defs.values()].sort()).toEqual(['/src/lib/preflight.ts', '/src/lib/stylePresets.ts'])
  })

  it('扫描面覆盖 src 下每一个含生产源码的顶层目录（新增目录不会静默漏掉）', () => {
    const withProduction = new Set(Object.keys(ALL).filter(isProduction).map(topDir))
    expect(new Set(REAL.scanned.map(topDir))).toEqual(withProduction)
    for (const dir of ['components', 'canvas', 'store', 'hooks', 'lib', 'embedded', 'mcp', 'playground']) {
      expect(withProduction.has(dir), dir).toBe(true)
    }
    expect(REAL.scanned.length).toBe(Object.keys(ALL).filter(isProduction).length)
  })

  it('豁免表里的每个文件都在扫描面里（改名 / 删掉后的豁免不会悬着）', () => {
    for (const path of [...Object.keys(ALLOW), ...Object.keys(OPAQUE_IMPORT_ALLOW)]) {
      expect(REAL.scanned, path).toContain(path)
    }
  })

  it('路径看不见的 import() 豁免按处数点名：多一处就红，豁免文件以外一处就红', () => {
    const W = '/src/playground/pyodide.worker.ts'
    const one = 'await import(`${base}pyodide.mjs`)'
    expect(conversionHits(W, one, REAL.defs, [], OPAQUE_IMPORT_ALLOW[W])).toEqual([])
    expect(conversionHits(W, `${one}\nawait import(other)`, REAL.defs, [], OPAQUE_IMPORT_ALLOW[W])).not.toEqual([])
    expect(conversionHits(UI, one, REAL.defs)).not.toEqual([])
  })

  it('没有文件让换算函数流出去（豁免表之外）', () => {
    expect(REAL.hits).toEqual([])
  })

  // ---- 流法全表：导入方式 × 转出方式 × 绑定种类，每格一条样本（#557 评审三轮 P1 收口）----
  //
  // 位置：UI = 普通生产文件（这里用 hooks/，上一版漏扫的目录）；ALLOWED = 豁免文件（点名 panelScale）；
  // DEF = 定义者（stylePresets，可以从另一个定义者 preflight 取值）。
  // 期望：red = 判红；ok = 合法不红；blind = 盲点，静态看不到（写在上面的注释里，样本钉住现状）。

  const UI = '/src/hooks/usePageSize.ts'
  const ALLOWED = '/src/components/StyleDialog.tsx'
  const DEF = '/src/lib/stylePresets.ts'
  type Row = readonly [cell: string, at: string, src: string, expected: 'red' | 'ok' | 'blind']
  const TABLE: readonly Row[] = [
    // -- 导入方式（UI）--
    ['具名', UI, "import { panelScale } from '@/lib/preflight'", 'red'],
    ['具名改名', UI, "import { panelScale as ps } from '@/lib/preflight'", 'red'],
    ['具名 default', UI, "import { default as pf } from '@/lib/preflight'", 'red'],
    ['具名 + 带扩展名 / 相对路径', UI, "import { toPageValue } from '../lib/stylePresets.ts'", 'red'],
    ['default', UI, "import pf from '@/lib/preflight'", 'red'],
    ['namespace', UI, "import * as pf from '@/lib/preflight'\npf.panelScale(p)", 'red'],
    ['import = require', UI, "import pf = require('@/lib/preflight')", 'red'],
    ['require()', UI, "const pf = require('@/lib/preflight')", 'red'],
    ['动态 import 字面量', UI, "const { panelScale } = await import('@/lib/preflight')", 'red'],
    ['动态 import 模板字面量', UI, 'const m = await import(`@/lib/preflight`)', 'red'],
    ['动态 import 拼路径', UI, "const m = await import('@/lib/' + name)", 'red'],
    ['import.meta.glob 命中', UI, "const m = import.meta.glob('/src/lib/*.ts', { eager: true })", 'red'],
    ['import.meta.glob 数组 + 花括号', UI, "import.meta.glob(['@/lib/{preflight,api}.ts'])", 'red'],
    ['import.meta.glob 非字面量', UI, 'import.meta.glob(pattern)', 'red'],
    ['import.meta.glob 不命中', UI, "import.meta.glob('/src/i18n/locales/**/*.json')", 'ok'],
    ['纯类型 import', UI, "import type * as pf from '@/lib/preflight'", 'ok'],
    ['纯类型具名', UI, "import { type toPageValue, pagePtLens } from '@/lib/stylePresets'", 'ok'],
    ['纯副作用 import', UI, "import '@/lib/preflight'", 'ok'],
    ['定义者的其它导出', UI, "import { pagePtLens, PT_DECIMALS } from '@/lib/stylePresets'", 'ok'],
    ['别的模块', UI, "import * as api from '@/lib/api'\nconst { useRenderStore } = await import('@/store/renderStore')", 'ok'],
    ['注释里提到', UI, "// import * as pf from '@/lib/preflight'", 'ok'],
    // -- 转出方式（任何文件：转出换算名本身就红，不必沿链追）--
    ['export { x } from', UI, "export { panelScale } from '@/lib/preflight'", 'red'],
    ['export { x as y } from', UI, "export { toPageValue as page } from '@/lib/stylePresets'", 'red'],
    ['export { x as default } from', UI, "export { panelScale as default } from '@/lib/preflight'", 'red'],
    ['export * from', UI, "export * from '@/lib/stylePresets'", 'red'],
    ['export * as ns from', UI, "export * as sp from '@/lib/stylePresets'", 'red'],
    ['export { 其它导出 } from', UI, "export { pagePtLens } from '@/lib/stylePresets'", 'ok'],
    ['豁免文件 export * from', ALLOWED, "export * from '@/lib/preflight'", 'red'],
    ['定义者 export { x } from 另一定义者', DEF, "export { panelScale } from './preflight'", 'red'],
    // -- 豁免文件：只认具名原名；拿到的绑定只许被调用 --
    ['豁免：点名的具名 + 调用', ALLOWED, "import { panelScale } from '@/lib/preflight'\npanelScale(p)", 'ok'],
    ['豁免：改名后调用', ALLOWED, "import { panelScale as ps } from '@/lib/preflight'\nps(p)", 'ok'],
    ['豁免：没点名的名字', ALLOWED, "import { toPageValue } from '@/lib/stylePresets'", 'red'],
    ['豁免：namespace', ALLOWED, "import * as pf from '@/lib/preflight'", 'red'],
    ['豁免：default', ALLOWED, "import pf from '@/lib/preflight'", 'red'],
    ['具名绑定 × export { b }', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport { panelScale }", 'red'],
    ['改名绑定 × export { b as y }', ALLOWED, "import { panelScale as ps } from '@/lib/preflight'\nexport { ps as scale }", 'red'],
    ['具名绑定 × export default', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport default panelScale", 'red'],
    ['具名绑定 × const 别名', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const s = panelScale", 'red'],
    ['具名绑定 × 对象简写', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const api = { panelScale }", 'red'],
    ['具名绑定 × 对象值', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const api = { s: panelScale }", 'red'],
    ['具名绑定 × 数组', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const fs = [panelScale]", 'red'],
    ['具名绑定 × 当实参传出', ALLOWED, "import { panelScale } from '@/lib/preflight'\nregister(panelScale)", 'red'],
    ['具名绑定 × 条件 / 或', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const s = x ?? panelScale", 'red'],
    ['具名绑定 × 括号调用', ALLOWED, "import { panelScale } from '@/lib/preflight'\n;(panelScale)(p)", 'red'],
    ['具名绑定 × new', ALLOWED, "import { panelScale } from '@/lib/preflight'\nnew panelScale(p)", 'red'],
    ['具名绑定 × 返回', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport function f() { return panelScale }", 'red'],
    ['具名绑定 × 类型位置', ALLOWED, "import { panelScale } from '@/lib/preflight'\ntype S = typeof panelScale", 'ok'],
    ['具名绑定 × 同名属性', ALLOWED, "import { panelScale } from '@/lib/preflight'\nconst o = { panelScale: 1 }; o.panelScale", 'ok'],
    // -- 定义者：自己的声明、从另一个定义者取到的 default / namespace / require 绑定 --
    ['定义者：原名导出自己的声明', DEF, 'function toPageValue() {}\nexport { toPageValue }', 'ok'],
    ['定义者：改名导出自己的声明', DEF, 'function toPageValue() {}\nexport { toPageValue as page }', 'red'],
    ['定义者：别名常量', DEF, 'export function toPageValue() {}\nexport const alias = toPageValue', 'red'],
    ['定义者：default 导出自己的声明', DEF, 'function toPageValue() {}\nexport default toPageValue', 'red'],
    ['定义者 namespace 绑定 × 调用', DEF, "import * as pf from './preflight'\npf.panelScale(p)", 'ok'],
    ['定义者 namespace 绑定 × export { ns }', DEF, "import * as pf from './preflight'\nexport { pf }", 'red'],
    ['定义者 namespace 绑定 × export default', DEF, "import * as pf from './preflight'\nexport default pf", 'red'],
    ['定义者 namespace 绑定 × 成员取值', DEF, "import * as pf from './preflight'\nexport const x = pf.panelScale", 'red'],
    ['定义者 namespace 绑定 × 下标', DEF, "import * as pf from './preflight'\npf['panelScale'](p)", 'red'],
    ['定义者 default 绑定 × 转出', DEF, "import pf from './preflight'\nexport default pf", 'red'],
    ['定义者 require 绑定 × 转出', DEF, "import pf = require('./preflight')\nexport { pf }", 'red'],
    // -- 盲点：静态看不到（写在上面的注释里）--
    ['盲点：豁免文件里调用后包一层导出', ALLOWED, "import { panelScale } from '@/lib/preflight'\nexport const s = (p) => panelScale(p)", 'blind'],
    ['盲点：不 import、自己重写乘除', UI, 'export const scaleOf = (p) => p.w / p.nativeW', 'blind'],
    ['盲点：运行时取值', UI, "export const s = (globalThis as any)['panel' + 'Scale']", 'blind'],
  ]

  it.each(TABLE)('%s（%s）%s → %s', (_cell, at, src, expected) => {
    const hits = conversionHits(at, src, REAL.defs, ALLOW[at])
    if (expected === 'red') expect(hits).not.toEqual([])
    else expect(hits).toEqual([])
  })

  it('#557 评审的两条转出链：链的第一个转出口当场红（default 链、namespace 链）', () => {
    const chains: Sources[] = [
      {
        '/src/lib/relay1.ts': "export { panelScale as default } from './preflight'",
        '/src/lib/relay2.ts': "import scale from './relay1'\nexport default scale",
        '/src/hooks/useScale.ts': "import s from '@/lib/relay2'\nexport const useScale = s",
      },
      {
        '/src/lib/relay1.ts': "import * as pf from './preflight'\nexport { pf }",
        '/src/lib/relay2.ts': "import { pf } from './relay1'\nexport default pf",
        '/src/hooks/useScale.ts': "import pf from '@/lib/relay2'\nexport const useScale = pf.panelScale",
      },
    ]
    for (const extra of chains) {
      const { hits } = scanAll({ ...ALL, ...extra })
      expect(hits.some((h) => h.startsWith('/src/lib/relay1.ts:')), JSON.stringify(hits)).toBe(true)
    }
  })

  it('定义者是现场认的：别处新声明一个换算名，它就成了定义者，从它取值照样红', () => {
    // 函数声明与常量声明两种写法都认
    for (const decl of ['export function panelScale() { return 1 }', 'export const panelScale = () => 1']) {
      const extra = { '/src/lib/scale2.ts': decl }
      const { defs, hits } = scanAll({ ...ALL, ...extra, [UI]: "import { panelScale } from '@/lib/scale2'" })
      expect([...defs.keys()], decl).toContain('/src/lib/scale2')
      expect(hits.some((h) => h.startsWith(`${UI}:`)), decl).toBe(true)
    }
  })
})
