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
 * 换算只有一处：界面与 store（`components/` / `canvas/` / `store/`——样式绑定 `styleBinding` 与设置页
 * 都是 `withPageBasis` 的调用方，它们只打口径标记，不做乘除）**不许自己拿缩放比做乘除**——拿到
 * `panelScale` / `toPageValue` / `toScriptValue` / `pageField` 的界面文件就是第二份换算的起点（属性页以前
 * 正是没有这一步才与样式面板差出 0.6 倍）。界面一律过写入器，写入器过 `pagePtLens`。
 *
 * 判的是模块依赖（AST），不是子串：注释里提到这几个名字不算。主语是「界面文件能不能拿到换算函数」，
 * 所以拿到它的**每一种**合法 TS 写法都要判（#557 评审 P1：从前只看具名 import，
 * `import * as preflight` 后 `preflight.panelScale(...)` 整条绕过）：
 *
 * - 换算模块：`src/` 里导出这几个名字的模块，**现场从源码认**（定义它的、`export { … } from` /
 *   `export * from` / `export * as` / `export default` 转出去的，按不动点一路传下去，别名也跟着走）——
 *   不是写死两个路径，新开一个转出口的模块自动进视野。
 * - 从换算模块：具名 import 取到换算名（含 `as` 改名）、namespace import、default import、
 *   `import x = require()`、`export … from` 转出（含 `export *`）、字面量路径的动态 `import()`
 *   一律红；namespace / default 不去解析后面的成员访问（`ns['panel' + 'Scale']`、把 ns 传出去都追不清），
 *   直接判红，偏严不偏松。纯类型的 import（`import type`、`{ type X }`）拿不到值，不算。
 * - 动态 `import()` 的路径不是字面量：看不见它指向哪，按红算。
 *
 * 盲点（静态看不到，写在明处）：`lib/` 里若有别的函数**包一层**再导出（`export const s = (p) =>
 * panelScale(p)`），界面 import `s` 这把尺子看不见——包装函数不是转出，名字与值都换了；界面文件
 * 自己写一遍乘除（不 import 任何东西）也看不见。`require()` 调用（非 `import x = require`）不在视野里，
 * 前端源码是 ESM，不用它。
 *
 * 豁免三条（按具名 import 点名，namespace / default 不给豁免）：样式对话框把 `panelScale` 交给
 * `extractFromManifest`（提取在 `stylePresets` 里换算，对话框自己不乘）；规范修复事务把缩放比交给后端；
 * 写入器 `useTextStyleAdapter` 按缩放比 memo 换好的字段表。
 */
const FORBIDDEN = new Set(['panelScale', 'toPageValue', 'toScriptValue', 'pageField'])

type Sources = Record<string, string>

const parse = (path: string, src: string) =>
  ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

/** import 路径 → `src/` 里的模块键（`/src/lib/preflight`，去扩展名、去 `/index`）；包名回 null */
function resolveModule(from: string, spec: string): string | null {
  let parts: string[]
  if (spec.startsWith('@/')) parts = ['', 'src', ...spec.slice(2).split('/')]
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

const moduleKey = (path: string) => path.replace(/\.(tsx?)$/, '').replace(/\/index$/, '')

const hasExport = (node: ts.Node) =>
  ts.canHaveModifiers(node) &&
  (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword)

const specText = (node: ts.Expression | undefined) =>
  node && ts.isStringLiteralLike(node) ? node.text : undefined

/**
 * 每个模块导出的「换算名」（值层面指向换算函数的导出名）：定义者导出 `FORBIDDEN` 里的名字；
 * 转出者按它从谁转、怎么转（具名 / 改名 / `export *` / `export * as` / `export default`）继承，
 * 按不动点传到底。`export * as ns` 与 default 转出记成导出名 `ns` / `default`。
 */
function conversionExports(sources: Sources): Map<string, Set<string>> {
  const files = Object.entries(sources)
    .filter(([p]) => !/\.test\.tsx?$/.test(p))
    .map(([p, s]) => [p, parse(p, s)] as const)
  const out = new Map<string, Set<string>>()
  const add = (key: string, name: string) => {
    const set = out.get(key) ?? new Set<string>()
    const before = set.size
    set.add(name)
    out.set(key, set)
    return set.size > before
  }
  for (let changed = true; changed; ) {
    changed = false
    for (const [path, file] of files) {
      const key = moduleKey(path)
      // 本模块里值层面叫换算名的局部绑定：自己定义的 + 从换算模块 import 进来的
      const local = new Set<string>()
      for (const st of file.statements) {
        if (ts.isFunctionDeclaration(st) && st.name && FORBIDDEN.has(st.name.text)) {
          local.add(st.name.text)
          if (hasExport(st)) changed = add(key, st.name.text) || changed
        }
        if (ts.isVariableStatement(st)) {
          for (const d of st.declarationList.declarations) {
            if (ts.isIdentifier(d.name) && FORBIDDEN.has(d.name.text)) {
              local.add(d.name.text)
              if (hasExport(st)) changed = add(key, d.name.text) || changed
            }
          }
        }
        if (ts.isImportDeclaration(st) && !st.importClause?.isTypeOnly) {
          const src = resolveModule(path, specText(st.moduleSpecifier) ?? '')
          const theirs = src ? out.get(src) : undefined
          const named = st.importClause?.namedBindings
          if (theirs && named && ts.isNamedImports(named)) {
            for (const el of named.elements) {
              if (!el.isTypeOnly && theirs.has((el.propertyName ?? el.name).text)) local.add(el.name.text)
            }
          }
        }
      }
      for (const st of file.statements) {
        if (ts.isExportAssignment(st) && ts.isIdentifier(st.expression) && local.has(st.expression.text)) {
          changed = add(key, 'default') || changed
        }
        if (!ts.isExportDeclaration(st) || st.isTypeOnly) continue
        const from = specText(st.moduleSpecifier)
        const theirs = from ? out.get(resolveModule(path, from) ?? '') : local
        if (!theirs || theirs.size === 0) continue
        const clause = st.exportClause
        if (!clause) {
          for (const n of theirs) if (n !== 'default') changed = add(key, n) || changed // export *
        } else if (ts.isNamespaceExport(clause)) {
          changed = add(key, clause.name.text) || changed // export * as ns
        } else {
          for (const el of clause.elements) {
            if (!el.isTypeOnly && theirs.has((el.propertyName ?? el.name).text)) {
              changed = add(key, el.name.text) || changed
            }
          }
        }
      }
    }
  }
  return out
}

/** 一个界面文件拿到换算函数的每一处（豁免只认具名 import 的原名） */
function conversionImports(
  path: string,
  src: string,
  exportsOf: Map<string, Set<string>>,
  allow: readonly string[] = [],
): string[] {
  const hits: string[] = []
  const file = parse(path, src)
  const conv = (spec: string | undefined) => {
    const key = spec === undefined ? null : resolveModule(path, spec)
    const names = key ? exportsOf.get(key) : undefined
    return names && names.size > 0 ? names : null
  }
  for (const st of file.statements) {
    if (ts.isImportDeclaration(st)) {
      const names = conv(specText(st.moduleSpecifier))
      const clause = st.importClause
      if (!names || !clause || clause.isTypeOnly) continue
      if (clause.name) hits.push(`${path}: default import`)
      const nb = clause.namedBindings
      if (nb && ts.isNamespaceImport(nb)) hits.push(`${path}: import * as ${nb.name.text}`)
      if (nb && ts.isNamedImports(nb)) {
        for (const el of nb.elements) {
          const name = (el.propertyName ?? el.name).text
          if (!el.isTypeOnly && names.has(name) && !allow.includes(name)) hits.push(`${path}: ${name}`)
        }
      }
    } else if (ts.isImportEqualsDeclaration(st) && !st.isTypeOnly) {
      const ref = st.moduleReference
      if (ts.isExternalModuleReference(ref) && conv(specText(ref.expression))) {
        hits.push(`${path}: import ${st.name.text} = require()`)
      }
    } else if (ts.isExportDeclaration(st) && st.moduleSpecifier && !st.isTypeOnly) {
      const names = conv(specText(st.moduleSpecifier))
      if (!names) continue
      const clause = st.exportClause
      if (!clause || ts.isNamespaceExport(clause)) hits.push(`${path}: export * from`)
      else {
        for (const el of clause.elements) {
          const name = (el.propertyName ?? el.name).text
          if (!el.isTypeOnly && names.has(name)) hits.push(`${path}: export { ${name} } from`)
        }
      }
    }
  }
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const spec = specText(node.arguments[0])
      if (spec === undefined) hits.push(`${path}: import(<非字面量>)`)
      else if (conv(spec)) hits.push(`${path}: import('${spec}')`)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return hits
}

describe('界面代码与 store 不自己做页面 pt 换算', () => {
  const SOURCES = import.meta.glob('/src/{components,canvas,store}/**/*.{ts,tsx}', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Sources
  /** 换算模块从整个 `src/` 里现场认，不写死路径 */
  const ALL = import.meta.glob('/src/**/*.{ts,tsx}', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Sources
  const ALLOW: Record<string, string[]> = {
    '/src/components/StyleDialog.tsx': ['panelScale'],
    // 规范修复事务（ADR 0080）：缩放比原样随请求交给后端去判，自己不换算任何页面 pt 值
    '/src/store/issueFixActions.ts': ['panelScale'],
    // 写入器本身：按缩放比 memo 住换好的字段表（与 `lens.field` 同一个函数，不是第二份换算）
    '/src/components/inspector/textStyleAdapter.ts': ['pageField'],
  }
  const EXPORTS = conversionExports(ALL)

  it('换算模块是从源码里认出来的：定义这几个函数的两处都在视野里', () => {
    expect([...(EXPORTS.get('/src/lib/preflight') ?? [])]).toContain('panelScale')
    expect([...(EXPORTS.get('/src/lib/stylePresets') ?? [])].sort()).toEqual(
      ['pageField', 'toPageValue', 'toScriptValue'].sort(),
    )
  })

  it('没有界面文件拿到换算函数（豁免表之外）', () => {
    const hits: string[] = []
    let scanned = 0
    for (const [path, src] of Object.entries(SOURCES)) {
      if (/\.test\.tsx?$/.test(path)) continue
      scanned++
      hits.push(...conversionImports(path, src, EXPORTS, ALLOW[path]))
    }
    expect(scanned, '扫描面该覆盖到界面代码').toBeGreaterThan(100)
    expect(hits).toEqual([])
  })

  // ---- 尺子自证（#557 评审 P1）：每一种拿到换算函数的写法都红，拿不到值的写法不红 ----

  const UI = '/src/components/inspector/Fake.tsx'
  const red = (src: string, exportsOf = EXPORTS, path = UI) =>
    conversionImports(path, src, exportsOf, ALLOW[path])

  it.each([
    ["import * as preflight from '@/lib/preflight'\npreflight.panelScale(p)"],
    ["import * as sp from '../../lib/stylePresets'\nsp['toPage' + 'Value'](a, b, c)"],
    ["import { panelScale as ps } from '@/lib/preflight'"],
    ["import { pagePtLens, toScriptValue } from '@/lib/stylePresets.ts'"],
    ["import preflight from '@/lib/preflight'\npreflight.panelScale(p)"],
    ["import preflight, { type EditableField } from '@/lib/preflight'"],
    ["import preflight = require('@/lib/preflight')"],
    ["export * from '@/lib/stylePresets'"],
    ["export * as sp from '@/lib/stylePresets'"],
    ["export { panelScale } from '@/lib/preflight'"],
    ["export { toPageValue as page } from '@/lib/stylePresets'"],
    ["const m = await import('@/lib/preflight')\nm.panelScale(p)"],
    ["const { panelScale } = await import('../../lib/preflight')"],
    ['const m = await import(`@/lib/preflight`)'],
    ["const m = await import(where)"],
  ])('红：%s', (src) => {
    expect(red(src)).not.toEqual([])
  })

  it.each([
    ["import type * as preflight from '@/lib/preflight'"],
    ["import type { panelScale } from '@/lib/preflight'"],
    // 换算名必须真的由这个模块导出：写成别的模块的名字，这条样本就量不到 `type` 的豁免
    ["import { type toPageValue, pagePtLens } from '@/lib/stylePresets'"],
    ["import { pagePtLens, PT_DECIMALS } from '@/lib/stylePresets'"],
    ["import * as api from '@/lib/api'"],
    ["const { useRenderStore } = await import('@/store/renderStore')"],
    ["// import * as preflight from '@/lib/preflight'"],
  ])('不红：%s', (src) => {
    expect(red(src)).toEqual([])
  })

  it('豁免只认具名 import 的原名：豁免文件改用 namespace import 照样红', () => {
    const path = '/src/components/StyleDialog.tsx'
    expect(red("import { panelScale } from '@/lib/preflight'", EXPORTS, path)).toEqual([])
    expect(red("import * as pf from '@/lib/preflight'", EXPORTS, path)).not.toEqual([])
    expect(red("import { toPageValue } from '@/lib/stylePresets'", EXPORTS, path)).not.toEqual([])
  })

  it.each([
    ["export * from './preflight'", "import { panelScale } from '@/lib/relay'"],
    ["export { panelScale as scaleOf } from './preflight'", "import { scaleOf } from '@/lib/relay'"],
    ["export * as pf from './preflight'", "import { pf } from '@/lib/relay'"],
    ["import { panelScale } from './preflight'\nexport { panelScale as s }", "import { s } from '@/lib/relay'"],
    ["import { panelScale } from './preflight'\nexport default panelScale", "import s from '@/lib/relay'"],
    ["export * from './preflight'", "import * as relay from '@/lib/relay/index'"],
  ])('转出口也是换算模块（不动点传到底、别名跟着走）：%s', (relay, ui) => {
    const withRelay = conversionExports({ ...ALL, '/src/lib/relay.ts': relay })
    expect(red(ui, withRelay)).not.toEqual([])
    // 同一句 import 在没有转出口时不红：红来自对转出口的识别，不是别处
    expect(red(ui)).toEqual([])
  })
})
