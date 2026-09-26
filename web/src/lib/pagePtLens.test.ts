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
 * 判的是 AST，不是子串（注释里提到这几个名字不算）。主语是「**值能不能从换算模块流出去**」：
 *
 * 1. **扫描面 = `src/` 下全部生产源码**，只做显式排除（`EXCLUDED`：测试文件、`.d.ts`），不按目录列白名单；
 *    换算模块本身与豁免文件**也在扫描面里**，只是换了判法（见 2、3）。
 * 2. **碰到换算模块的地方就地判**：定义者（`src/` 里顶层声明或导出了这几个名字的模块，现场认出来、再与
 *    期望的两个对拍）之外的任何生产文件，从定义者取值——具名（按原名，`as` 改名不豁免）、`{ default as x }`、
 *    default、namespace、`import x = require()`、`require()`、`import()`、`new URL(…, import.meta.url)`——一律红；
 *    `export … from 定义者` 转出换算名也红。豁免只认 `ALLOW` 里点名的具名 import。纯类型 import、纯副作用
 *    import、取定义者的其它导出（`pagePtLens`）不算。
 * 3. **合法拿到的绑定不许再流出去**（定义者自己的声明、定义者之间的 import、豁免文件的 import）：绑定只许
 *    出现在被调用的位置（`panelScale(p)`、`ns.panelScale(p)`），其余一切出现按逃逸判红。定义者用
 *    `export { toPageValue }` 原名导出自己的声明不算逃逸。
 * 4. **fail closed：语法面无法穷举，认不出的一律红，不再为它扩解析器**（#557 评审五轮 P1：glob 的字符类
 *    `[tj]` 被当字面量——手写解析器每补一个语法角落，就还有下一个）：
 *    - `import.meta.glob` **不解析模式**：出现即红，只有 `QUOTA` 里按处数点名的文件放行（现在 0 处）。
 *    - 路径不是字面量的 `import()` / `require()` / `new URL(…, import.meta.url)`：出现即红，同样按处数点名。
 *    - `import.meta` 只认 `.env` 与 `new URL(字面量, import.meta.url)` 两种用法，其余（`.resolve`、`.hot`、
 *      把 `.glob` / `.url` 取出来）一律红。
 *    - 字面量说明符只认四种：别名（从 vite / vitest 配置、tsconfig `paths`、package.json `imports` 读）、
 *      相对路径、根绝对路径（`/src/...`）、package.json 里声明过的包（与 `node:` 内建）。字符集之外的
 *      （`%` 编码、空白、通配符）、带协议的（`https:`、`virtual:`）、没声明的包名、没配上别名的 `#…`——一律红。
 *      认得的这几种再做 posix 归一（`.`、`..`、重复斜杠）、去查询与片段（偏严：`?raw` 也按拿到了算）、
 *      不分大小写、去扩展名与 `/index`。
 *    - 配置读不出（别名目标不是认得的两种写法、`alias` 出现在认不出的位置、`resolve.extensions`、tsconfig 的
 *      `extends` / `rootDirs` / `moduleSuffixes`、`imports` 里认不出的值与通配）：直接报错。
 *    - 源码有语法错误（AST 不完整）：判红。
 *
 * 为什么这样就闭合：值要到达任何文件，第一跳必然是「某个文件从定义者取值」。第一跳在非豁免文件里 → 2 当场红；
 * 在定义者 / 豁免文件里 → 取到的绑定只能被调用，流不出去（3）；第一跳的写法认不出 → 4 当场红。于是转出链
 * 不需要沿链追：链的第一个转出口就是红的。
 *
 * 盲点（静态看不到的，写在明处，样本里标 blind）：定义者与豁免文件里**调用**换算函数的包装再导出
 * （`export const s = (p) => panelScale(p)`——定义者本来就是换算的唯一出处，豁免文件按名审过）；不 import、
 * 自己重写一遍乘除；`eval` / `globalThis` 这类运行时取值；vite 插件在运行时加的别名（配置文件里看不见）；
 * 测试文件与 `.d.ts`（不是生产代码）。判据按名字、不做作用域分析——同名的影子变量只会让它偏严（误红），不会漏。
 */
const FORBIDDEN = new Set(['panelScale', 'toPageValue', 'toScriptValue', 'pageField'])

type Sources = Record<string, string>

/**
 * 代码扩展名 → 解析语法：**唯一一张表**（#557 评审六轮 P1：只收 `.ts/.tsx`，`.mts` 模块整个不在视野里）。
 * 扫描面（`isProduction`）、解析器（`parse` 的语法）、说明符去扩展名（`keyOfPath`）都从它派生，
 * 有一条用例按行为核对三者认的是同一组扩展名。
 */
const CODE_EXTENSIONS: Readonly<Record<string, ts.ScriptKind>> = {
  '.ts': ts.ScriptKind.TS,
  '.tsx': ts.ScriptKind.TSX,
  '.mts': ts.ScriptKind.TS,
  '.cts': ts.ScriptKind.TS,
  '.js': ts.ScriptKind.JS,
  '.jsx': ts.ScriptKind.JSX,
  '.mjs': ts.ScriptKind.JS,
  '.cjs': ts.ScriptKind.JS,
}
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
/** 最长的先配（`.mts` 不能被当成 `.ts`） */
const CODE_EXT_LIST = Object.keys(CODE_EXTENSIONS).sort((a, b) => b.length - a.length)
const CODE_EXT_RE = CODE_EXT_LIST.map(escapeRe).join('|')
const codeExtOf = (path: string) => CODE_EXT_LIST.find((e) => path.toLowerCase().endsWith(e))

/**
 * src 下不是代码、也带不出函数值的文件：按扩展名显式点名，理由写在旁边。
 * 代码表与这张表**之外**的扩展名一律红（fail closed：防下一个扩展名悄悄进来）。
 */
const DATA_EXTENSIONS: Readonly<Record<string, string>> = {
  '.json': 'JSON 只有数据，import 进来拿不到函数',
  '.css': '样式表',
  '.webp': '图片',
  '.py': 'playground 的示例脚本：在 Pyodide 里跑的 Python，不是 JS 模块',
}
/** 按文件名点名的非代码文件（不进 git 的本机杂物） */
const DATA_BASENAMES: Readonly<Record<string, string>> = {
  '.DS_Store': 'macOS Finder 的目录元数据，不进 git',
}

/** 扫描面的显式排除：不是生产代码的 */
const EXCLUDED: ReadonlyArray<readonly [string, (path: string) => boolean]> = [
  ['测试文件', (p) => new RegExp(`\\.test(${CODE_EXT_RE})$`, 'i').test(p)],
  ['类型声明（没有值）', (p) => /\.d\.[cm]?ts$/i.test(p)],
]
const isProduction = (path: string) => codeExtOf(path) !== undefined && !EXCLUDED.some(([, hit]) => hit(path))

/** 扩展名既不在代码表、也不在数据表里的文件（测试文件除外）：一律红 */
const unknownKind = (path: string) =>
  codeExtOf(path) === undefined &&
  !Object.keys(DATA_EXTENSIONS).some((e) => path.toLowerCase().endsWith(e)) &&
  !(path.split('/').pop()! in DATA_BASENAMES)

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

/** 「出现即红」的写法按处数豁免：文件 → 每种写法允许几处（多一处就红；少了也红，豁免不许悬着） */
interface Quota {
  /** 路径不是字面量的 `import()` / `require()` / `new URL(…, import.meta.url)` */
  opaque?: number
  /** `import.meta.glob`（不解析模式） */
  glob?: number
}
const QUOTA: Record<string, Quota> = {
  // 从运行时给的 CDN 基址加载 Pyodide 自己的 `pyodide.mjs`（`${pyodideBaseUrl}pyodide.mjs`）：
  // 构建产物里的源码模块没有可寻址的 URL，这一处拿不到换算模块
  '/src/playground/pyodide.worker.ts': { opaque: 1 },
  // `import.meta.glob`：main 上生产源码里 0 处（2026-09-26 核对），所以这里没有条目
}

/** `.ts` 按 TS 解析、`.tsx` 按 TSX 解析（`.ts` 里的 `<T>(x) => …` 在 TSX 下会解析错） */
const parse = (path: string, src: string): ts.SourceFile => {
  // 同一份源码只解析一次：全量扫描在收集阶段做一次，之后的用例（加几个样本文件再判）只解析新文件
  // ——CI 慢机上每条用例重扫整个 src 会超 5 s（a0e2d342 的 frontend job）
  const key = `${path}\0${src}`
  let file = PARSED.get(key)
  if (!file) {
    file = ts.createSourceFile(
      path,
      src,
      ts.ScriptTarget.Latest,
      true,
      CODE_EXTENSIONS[codeExtOf(path) ?? '.ts'],
    )
    PARSED.set(key, file)
  }
  return file
}
const PARSED = new Map<string, ts.SourceFile>()

/** 语法错误：AST 不完整，判不了就按红算 */
const hasSyntaxErrors = (file: ts.SourceFile) =>
  ((file as unknown as { parseDiagnostics?: readonly unknown[] }).parseDiagnostics?.length ?? 0) > 0

// ---- 说明符 → 规范路径：import / export-from / require / import() / new URL 全走这一处 ----
//
// 键空间：web 根 = `/`，源码在 `/src/...`；web 根之外的路径留着开头的 `/..`，永远不会与 `src` 里的模块相撞。

interface Alias {
  /** `@`、`@profiles`、`#conv/`（`prefix` 为真时按前缀匹配，剩下的部分接在 `target` 后面） */
  find: string
  prefix: boolean
  /** web 根下的绝对路径（`/src`、`/../src/tavotto/...`）；前缀别名时是通配之前的那一段，原样拼接 */
  target: string
  /** 前缀别名里通配之后的那一段（`./src/lib/*.ts` 的 `.ts`） */
  suffix?: string
}

/** posix 归一：输入以 `/` 开头；越过根的 `..` 保留（落在 web 根之外） */
function posixNormalize(abs: string): string {
  const out: string[] = []
  for (const seg of abs.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..' && out.length > 0 && out[out.length - 1] !== '..') out.pop()
    else out.push(seg)
  }
  return `/${out.join('/')}`
}

const dirOf = (path: string) => path.split('/').slice(0, -1).join('/') || '/'

/** 别名（最长的先配）；没配上回 null */
function applyAlias(spec: string, aliases: readonly Alias[]): string | null {
  for (const a of [...aliases].sort((x, y) => y.find.length - x.find.length)) {
    if (a.prefix && spec.startsWith(a.find)) return `${a.target}${spec.slice(a.find.length)}${a.suffix ?? ''}`
    if (!a.prefix && (spec === a.find || spec.startsWith(`${a.find}/`))) {
      return `${a.target}/${spec.slice(a.find.length)}`
    }
  }
  return null
}

/** 规范路径 → 模块键：去扩展名、去 `/index`、小写 */
const keyOfPath = (abs: string) =>
  abs
    .replace(new RegExp(`(${CODE_EXT_RE})$`, 'i'), '')
    .replace(/\/index$/i, '')
    .toLowerCase()

const moduleKey = (path: string) => keyOfPath(posixNormalize(path))

type Resolved = { kind: 'module'; key: string } | { kind: 'package' } | { kind: 'unknown'; why: string }

/** 说明符只许用这些字符（去掉查询与片段之后）；之外的——`%` 编码、空白、通配符——不认 */
const SPEC_CHARS = /^[A-Za-z0-9@#~._\-/:+]+$/

/** 包名：`@scope/name` 或第一段 */
const packageName = (spec: string) => spec.split('/').slice(0, spec.startsWith('@') ? 2 : 1).join('/')

/** 字面量说明符 → 四种认得的去处之一；认不出就是 `unknown`（调用方判红） */
function resolveSpec(
  from: string,
  spec: string,
  aliases: readonly Alias[],
  deps: ReadonlySet<string> = REAL_DEPS,
): Resolved {
  let s = spec.replace(/\?.*$/, '')
  if (!s.startsWith('#')) s = s.replace(/#.*$/, '')
  if (!SPEC_CHARS.test(s)) return { kind: 'unknown', why: '字符集之外' }
  const aliased = applyAlias(s, aliases)
  if (aliased !== null) return { kind: 'module', key: keyOfPath(posixNormalize(aliased)) }
  if (s.startsWith('./') || s.startsWith('../') || s === '.' || s === '..') {
    return { kind: 'module', key: keyOfPath(posixNormalize(`${dirOf(from)}/${s}`)) }
  }
  if (s.startsWith('/')) return { kind: 'module', key: keyOfPath(posixNormalize(s)) }
  if (/^node:[a-z_/]+$/.test(s)) return { kind: 'package' }
  if (s.includes(':')) return { kind: 'unknown', why: '带协议' }
  if (s.startsWith('#')) return { kind: 'unknown', why: '没配上别名的 #' }
  if (deps.has(packageName(s))) return { kind: 'package' }
  return { kind: 'unknown', why: '没声明的包' }
}

function resolveModule(from: string, spec: string, aliases: readonly Alias[]): string | null {
  const r = resolveSpec(from, spec, aliases)
  return r.kind === 'module' ? r.key : null
}

const specText = (node: ts.Node | undefined) =>
  node && ts.isStringLiteralLike(node) ? node.text : undefined

// ---- 别名与依赖从配置里读；读不出一律报错 ----

const isImportMetaUrl = (node: ts.Node) =>
  ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'url'

/** vite / vitest 配置里的 `resolve.alias`：对象形式与 `{ find, replacement }` 数组形式；目标只认两种写法 */
function viteAliases(configPath: string, src: string): Alias[] {
  const file = parse(configPath, src)
  if (hasSyntaxErrors(file)) throw new Error(`${configPath}: 语法错误`)
  const out: Alias[] = []
  const handled = new Set<ts.Node>()
  const target = (node: ts.Expression): string => {
    // fileURLToPath(new URL('<相对路径>', import.meta.url))
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === 'fileURLToPath' &&
      node.arguments.length === 1
    ) {
      const inner = node.arguments[0]
      if (
        ts.isNewExpression(inner) &&
        ts.isIdentifier(inner.expression) &&
        inner.expression.text === 'URL' &&
        inner.arguments?.length === 2 &&
        isImportMetaUrl(inner.arguments[1])
      ) {
        const rel = specText(inner.arguments[0])
        if (rel !== undefined) return posixNormalize(`${dirOf(configPath)}/${rel}`)
      }
    }
    // 字面量的相对 / 根绝对路径
    const lit = specText(node)
    if (lit !== undefined && (lit.startsWith('./') || lit.startsWith('../') || lit.startsWith('/'))) {
      return posixNormalize(lit.startsWith('/') ? lit : `${dirOf(configPath)}/${lit}`)
    }
    throw new Error(`${configPath}: 读不出别名的目标 ${node.getText(file)}`)
  }
  /** 属性名 / 标识符 / 字符串里的这个词（`alias:`、`'alias':`、`resolve['alias']` 都算） */
  const word = (node: ts.Node) =>
    ts.isIdentifier(node) || ts.isStringLiteralLike(node) ? node.text : undefined
  const visit = (node: ts.Node) => {
    if (ts.isPropertyAssignment(node) && word(node.name) === 'alias') {
      handled.add(node.name)
      const init = node.initializer
      if (ts.isObjectLiteralExpression(init)) {
        for (const p of init.properties) {
          if (!ts.isPropertyAssignment(p)) throw new Error(`${configPath}: 读不出别名 ${p.getText(file)}`)
          const find = specText(p.name) ?? (ts.isIdentifier(p.name) ? p.name.text : undefined)
          if (find === undefined) throw new Error(`${configPath}: 别名的键不是字面量`)
          out.push({ find, prefix: false, target: target(p.initializer) })
        }
      } else if (ts.isArrayLiteralExpression(init)) {
        for (const el of init.elements) {
          const props: readonly ts.ObjectLiteralElementLike[] = ts.isObjectLiteralExpression(el) ? el.properties : []
          const get = (k: string): ts.PropertyAssignment | undefined =>
            props.filter(ts.isPropertyAssignment).find((p) => p.name.getText(file) === k)
          const find = specText(get('find')?.initializer)
          const repl = get('replacement')?.initializer
          if (find === undefined || !repl || props.length !== 2) {
            throw new Error(`${configPath}: 读不出别名 ${el.getText(file)}`)
          }
          out.push({ find, prefix: false, target: target(repl) })
        }
      } else throw new Error(`${configPath}: alias 不是字面量 ${init.getText(file)}`)
    } else if (word(node) === 'alias' && !handled.has(node)) {
      // `resolve: { alias }` 简写、`config.resolve.alias = …`、`resolve['alias']`——都读不出
      throw new Error(`${configPath}: alias 出现在认不出的位置`)
    } else if (word(node) === 'extensions') {
      throw new Error(`${configPath}: resolve.extensions 会改变解析，判据不认`)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return out
}

/** 键不带 `*` → 精确；键以 `*` 结尾、目标恰有一个 `*` → 前缀（`*` 两侧原样拼接）；其余写法读不出就报错 */
function wildcardAlias(where: string, key: string, value: string, base: string): Alias {
  const keyStars = key.split('*').length - 1
  const valueStars = value.split('*').length - 1
  if (keyStars === 0 && valueStars === 0) {
    return { find: key, prefix: false, target: posixNormalize(`${base}/${value}`) }
  }
  if (keyStars !== 1 || !key.endsWith('*') || valueStars !== 1) {
    throw new Error(`${where}: 读不出的通配 ${key} → ${value}`)
  }
  const [pre, post] = value.split('*')
  return { find: key.slice(0, -1), prefix: true, target: `${base}/${pre}`, suffix: post }
}

/** tsconfig 的 `compilerOptions.paths`（相对 `baseUrl`，没有就相对 tsconfig 所在目录） */
function tsconfigAliases(configPath: string, src: string): Alias[] {
  const { config, error } = ts.parseConfigFileTextToJson(configPath, src)
  if (error) throw new Error(`${configPath}: 解析失败`)
  if (config?.extends !== undefined) throw new Error(`${configPath}: extends 进来的 paths 读不到`)
  const opts = (config?.compilerOptions ?? {}) as Record<string, unknown>
  for (const k of ['rootDirs', 'moduleSuffixes']) {
    if (opts[k] !== undefined) throw new Error(`${configPath}: ${k} 会改变解析，判据不认`)
  }
  const base = posixNormalize(`${dirOf(configPath)}/${(opts.baseUrl as string | undefined) ?? '.'}`)
  return Object.entries((opts.paths ?? {}) as Record<string, string[]>).flatMap(([key, targets]) =>
    targets.map((t) => wildcardAlias(configPath, key, t, base)),
  )
}

/** package.json 的 `imports`（`#` 开头的子路径导入）；条件对象里的每一个字符串目标都算，认不出的值报错 */
function packageImportAliases(path: string, src: string): Alias[] {
  const imports = (JSON.parse(src) as { imports?: Record<string, unknown> }).imports ?? {}
  const flat = (v: unknown): string[] => {
    if (typeof v === 'string') return [v]
    if (v === null) return [] // 显式排除
    if (typeof v === 'object') return Object.values(v).flatMap(flat)
    throw new Error(`${path}: imports 里认不出的值 ${JSON.stringify(v)}`)
  }
  return Object.entries(imports).flatMap(([key, v]) =>
    flat(v).map((t) => wildcardAlias(path, key, t, dirOf(path))),
  )
}

/** package.json 声明过的包（dependencies / devDependencies / peer / optional） */
function declaredPackages(src: string): Set<string> {
  const pkg = JSON.parse(src) as Record<string, Record<string, string> | undefined>
  return new Set(
    ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'].flatMap((k) =>
      Object.keys(pkg[k] ?? {}),
    ),
  )
}

// vite 要求 glob 的选项是就地的对象字面量
const CONFIG_SOURCES = {
  vite: import.meta.glob('/{vite,vitest}{,.*}.config.ts', { eager: true, query: '?raw', import: 'default' }) as Sources,
  tsconfig: import.meta.glob('/tsconfig*.json', { eager: true, query: '?raw', import: 'default' }) as Sources,
  pkg: import.meta.glob('/package.json', { eager: true, query: '?raw', import: 'default' }) as Sources,
}

function aliasesFrom(sources: typeof CONFIG_SOURCES): Alias[] {
  return [
    ...Object.entries(sources.vite).flatMap(([p, s]) => viteAliases(p, s)),
    ...Object.entries(sources.tsconfig).flatMap(([p, s]) => tsconfigAliases(p, s)),
    ...Object.entries(sources.pkg).flatMap(([p, s]) => packageImportAliases(p, s)),
  ]
}

const REAL_ALIASES = aliasesFrom(CONFIG_SOURCES)
const REAL_DEPS = declaredPackages(CONFIG_SOURCES.pkg['/package.json'])

// ---- 定义者：顶层声明或导出了换算名的生产模块 ----

/** 绑定模式里的全部名字（`const { a, b: [c] } = …`） */
function bindingNames(name: ts.BindingName): string[] {
  if (ts.isIdentifier(name)) return [name.text]
  return name.elements.flatMap((el) => (ts.isOmittedExpression(el) ? [] : bindingNames(el.name)))
}

/** 顶层声明或导出的换算名：函数 / 类 / 枚举 / 命名空间 / 变量（含解构）/ `import x = …` / `export { y as x }` */
function ownDefinitions(file: ts.SourceFile): Set<string> {
  const own = new Set<string>()
  const add = (n: string) => FORBIDDEN.has(n) && own.add(n)
  for (const st of file.statements) {
    if (
      (ts.isFunctionDeclaration(st) ||
        ts.isClassDeclaration(st) ||
        ts.isEnumDeclaration(st) ||
        ts.isModuleDeclaration(st) ||
        ts.isImportEqualsDeclaration(st)) &&
      st.name &&
      ts.isIdentifier(st.name)
    ) {
      add(st.name.text)
    }
    if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) bindingNames(d.name).forEach(add)
    }
    if (ts.isExportDeclaration(st) && !st.moduleSpecifier && st.exportClause && ts.isNamedExports(st.exportClause)) {
      for (const el of st.exportClause.elements) add(el.name.text)
    }
  }
  return own
}

function definersOf(sources: Sources): Map<string, string> {
  const out = new Map<string, string>()
  for (const [path, src] of Object.entries(sources)) {
    if (!isProduction(path)) continue
    if (ownDefinitions(parse(path, src)).size > 0) out.set(moduleKey(path), path)
  }
  return out
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
  quota: Quota = {},
  aliases: readonly Alias[] = REAL_ALIASES,
): string[] {
  const hits: string[] = []
  const counted: Record<keyof Quota, string[]> = { opaque: [], glob: [] }
  const file = parse(path, src)
  const hit = (what: string) => hits.push(`${path}: ${what}`)
  if (hasSyntaxErrors(file)) hit('语法错误，AST 不完整')
  const isDefiner = defs.has(moduleKey(path))
  /** 字面量说明符：认不出就红；认得出、落在定义者上回 true */
  const toDefiner = (spec: string | undefined, where: string) => {
    if (spec === undefined) return false
    const r = resolveSpec(path, spec, aliases)
    if (r.kind === 'unknown') hit(`${where} 认不出的说明符 '${spec}'（${r.why}）`)
    return r.kind === 'module' && defs.has(r.key)
  }
  /** 本文件里合法拿到、指向换算函数（或装着它的命名空间）的绑定 */
  const own = isDefiner ? ownDefinitions(file) : new Set<string>()
  const bindings = new Set(own)

  for (const st of file.statements) {
    if (ts.isImportDeclaration(st)) {
      if (!toDefiner(specText(st.moduleSpecifier), 'import')) continue
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
    } else if (ts.isImportEqualsDeclaration(st) && ts.isExternalModuleReference(st.moduleReference)) {
      if (!toDefiner(specText(st.moduleReference.expression), 'import = require') || st.isTypeOnly) continue
      bindings.add(st.name.text)
      if (!isDefiner) hit(`import ${st.name.text} = require()`)
    } else if (ts.isExportDeclaration(st) && st.moduleSpecifier) {
      if (!toDefiner(specText(st.moduleSpecifier), 'export from') || st.isTypeOnly) continue
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
    // import() / require()：字面量按说明符判；不是字面量 → 出现即红（按处数豁免）
    if (ts.isCallExpression(node)) {
      const callee = node.expression
      if (callee.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(callee) && callee.text === 'require')) {
        const spec = specText(node.arguments[0])
        if (spec === undefined) counted.opaque.push(`${path}: import(<非字面量>)`)
        else if (toDefiner(spec, 'import()')) hit(`import('${spec}')`)
      }
    }
    // import.meta：只认 `.env` 与 `new URL(字面量, import.meta.url)`；`.glob` 出现即红（按处数豁免）
    if (ts.isMetaProperty(node) && node.keywordToken === ts.SyntaxKind.ImportKeyword) {
      const pa = node.parent
      const prop = ts.isPropertyAccessExpression(pa) && pa.expression === node ? pa.name.text : undefined
      if (prop === 'env') {
        // 构建期常量，拿不到模块
      } else if (prop?.startsWith('glob') && ts.isCallExpression(pa.parent) && pa.parent.expression === pa) {
        counted.glob.push(`${path}: import.meta.${prop}(…)（不解析模式，出现即红）`)
      } else if (
        prop === 'url' &&
        ts.isNewExpression(pa.parent) &&
        ts.isIdentifier(pa.parent.expression) &&
        pa.parent.expression.text === 'URL' &&
        pa.parent.arguments?.length === 2 &&
        pa.parent.arguments[1] === pa
      ) {
        const spec = specText(pa.parent.arguments[0])
        if (spec === undefined) counted.opaque.push(`${path}: new URL(<非字面量>, import.meta.url)`)
        else if (toDefiner(spec, 'new URL')) hit(`new URL('${spec}', import.meta.url)`)
      } else {
        hit(`认不出的 import.meta 用法：${pa.getText(file)}`)
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
  // 出现即红的写法：超出点名的处数就全部报出来（不猜是哪一处新加的）
  for (const kind of ['opaque', 'glob'] as const) {
    if (counted[kind].length > (quota[kind] ?? 0)) hits.push(...counted[kind])
  }
  return hits
}

/** 一个文件里「出现即红」的写法各有几处（核对豁免处数不悬着） */
function quotaUse(path: string, src: string, defs: Map<string, string>): Required<Quota> {
  const count = (kind: keyof Quota, n: number) =>
    conversionHits(path, src, defs, ALLOW[path], { opaque: 1e9, glob: 1e9, [kind]: n }).length
  const used = (kind: keyof Quota) => {
    let n = 0
    while (count(kind, n) > 0 && n < 100) n++
    return n
  }
  return { opaque: used('opaque'), glob: used('glob') }
}

/** 全部生产源码一起判 */
function scanAll(
  sources: Sources,
  aliases: readonly Alias[] = REAL_ALIASES,
): { hits: string[]; scanned: string[]; defs: Map<string, string> } {
  const defs = definersOf(sources)
  const hits: string[] = []
  const scanned: string[] = []
  for (const [path, src] of Object.entries(sources)) {
    if (!isProduction(path)) continue
    scanned.push(path)
    hits.push(...conversionHits(path, src, defs, ALLOW[path], QUOTA[path], aliases))
  }
  return { hits, scanned, defs }
}

describe('界面代码与 store 不自己做页面 pt 换算', () => {
  // 代码源码：扩展名与 `CODE_EXTENSIONS` 同一组（vite 要求模式是字面量，写不成从常量拼；下面有用例核对两边相等）。
  // 点开头的文件 / 目录 glob 默认不收，单独写模式把它们也收进来
  const ALL = import.meta.glob(
    [
      '/src/**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}',
      '/src/**/.*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}',
      '/src/**/.*/**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}',
    ],
    { eager: true, query: '?raw', import: 'default' },
  ) as Sources
  /** src 下的全部文件（不加载，只要路径）：判「扩展名认不认得」 */
  const SRC_FILES = Object.keys(import.meta.glob(['/src/**/*', '/src/**/.*', '/src/**/.*/**/*']))
  // 全量扫描在收集阶段只做一次（不进任何用例的 5 s 预算）；用例只查询结果
  const REAL = scanAll(ALL)
  /**
   * 在真实 src 上加几个样本文件再判：定义者按「真实 + 样本」重新认（解析走缓存，只多解析样本），
   * 命中只判样本文件本身——真实文件的结论已由 `REAL` 钉住（它们不 import 样本文件）。
   */
  const scanWith = (extra: Sources) => {
    const defs = definersOf({ ...ALL, ...extra })
    const hits = Object.entries(extra)
      .filter(([p]) => isProduction(p))
      .flatMap(([p, s]) => conversionHits(p, s, defs, ALLOW[p], QUOTA[p]))
    return { defs, hits }
  }
  const topDir =(p: string) => (p.split('/').length > 3 ? p.split('/')[2] : '(src 根)')

  it('定义者是从源码里认出来的，恰好是期望的两处（多出一处 = 多了一份换算）', () => {
    expect([...REAL.defs.values()].sort()).toEqual(['/src/lib/preflight.ts', '/src/lib/stylePresets.ts'])
  })

  it('src 下每个文件的扩展名都认得：代码表或数据表之外的一律红（fail closed）', () => {
    expect(SRC_FILES.length, '文件清单该是全量').toBeGreaterThan(500)
    expect(SRC_FILES.filter(unknownKind)).toEqual([])
    // 清单里的代码文件都进了源码表（glob 的扩展名与代码表是同一组）
    const code = SRC_FILES.filter((p) => codeExtOf(p) !== undefined)
    expect(code.filter((p) => !(p in ALL))).toEqual([])
    expect(Object.keys(ALL).filter((p) => !SRC_FILES.includes(p))).toEqual([])
  })

  it('扫描面、解析器、说明符去扩展名认的是同一组扩展名（都从 CODE_EXTENSIONS 派生）', () => {
    const candidates = [
      ...Object.keys(CODE_EXTENSIONS),
      ...['.vue', '.svelte', '.astro', '.json', '.css', '.wasm', '.coffee', '.es6', '.ts.orig', '.tsbuildinfo'],
    ]
    const scanned = candidates.filter((e) => isProduction(`/src/lib/x${e}`))
    const stripped = candidates.filter((e) => keyOfPath(`/src/lib/x${e}`) === '/src/lib/x')
    const parsedAs = candidates.filter((e) => codeExtOf(`/src/lib/x${e}`) !== undefined)
    const expected = Object.keys(CODE_EXTENSIONS).sort()
    expect(scanned.sort()).toEqual(expected)
    expect(stripped.sort()).toEqual(expected)
    expect(parsedAs.sort()).toEqual(expected)
    // 最长的先配：`.mts` / `.cts` 不被当成 `.ts`，`.jsx` 不被当成 `.js`
    expect(codeExtOf('/src/a.mts')).toBe('.mts')
    expect(codeExtOf('/src/a.jsx')).toBe('.jsx')
  })

  it.each([
    ['.mts', "import { panelScale } from '@/lib/preflight'\nexport const s = panelScale(p)"],
    ['.cts', "import { panelScale } from '../lib/preflight'"],
    ['.js', "import { panelScale } from '@/lib/preflight'"],
    ['.jsx', "import { panelScale } from '@/lib/preflight'\nexport const C = () => <b>{panelScale(p)}</b>"],
    ['.mjs', "export { panelScale } from '@/lib/preflight'"],
    ['.cjs', "const { panelScale } = require('@/lib/preflight')"],
  ])('%s 文件也在扫描面里、按它的语法解析，从定义者取值照样红', (ext, src) => {
    const path = `/src/hooks/useScale${ext}`
    expect(isProduction(path)).toBe(true)
    const { hits } = scanWith({ [path]: src })
    expect(hits.some((h) => h.startsWith(`${path}:`) && !/语法错误/.test(h)), JSON.stringify(hits)).toBe(true)
    // 按它自己的语法解析：`.jsx` 里的 JSX、`.js` 里的写法都不该报语法错误
    expect(hits.filter((h) => /语法错误/.test(h))).toEqual([])
  })

  it('测试文件与类型声明按全部代码扩展名排除', () => {
    for (const ext of Object.keys(CODE_EXTENSIONS)) {
      expect(isProduction(`/src/lib/a.test${ext}`), ext).toBe(false)
    }
    for (const p of ['/src/a.d.ts', '/src/a.d.mts', '/src/a.d.cts']) expect(isProduction(p), p).toBe(false)
    expect(isProduction('/src/a.mts')).toBe(true)
  })

  it.each([
    ['/src/lib/preflight.mts', 'export function panelScale() { return 1 }'],
    ['/src/lib/scale3.js', 'export function panelScale() { return 1 }'],
  ])('定义者也按全部代码扩展名认：%s', (path, src) => {
    expect([...scanWith({ [path]: src }).defs.values()]).toContain(path)
  })

  it.each([
    ['/src/lib/x.vue'],
    ['/src/lib/x.svelte'],
    ['/src/lib/x.wasm'],
    ['/src/lib/x'],
    ['/src/lib/.env'],
    ['/src/lib/x.ts.orig'],
  ])('认不得的扩展名一律红：%s', (path) => {
    expect(unknownKind(path)).toBe(true)
  })

  it.each([['/src/i18n/x.json'], ['/src/index.css'], ['/src/p/a.webp'], ['/src/p/a.py'], ['/src/lib/.DS_Store']])(
    '点了名的数据文件不红：%s',
    (path) => {
      expect(unknownKind(path)).toBe(false)
    },
  )

  it('扫描面覆盖 src 下每一个含生产源码的顶层目录（新增目录不会静默漏掉）', () => {
    const withProduction = new Set(Object.keys(ALL).filter(isProduction).map(topDir))
    expect(new Set(REAL.scanned.map(topDir))).toEqual(withProduction)
    for (const dir of ['components', 'canvas', 'store', 'hooks', 'lib', 'embedded', 'mcp', 'playground']) {
      expect(withProduction.has(dir), dir).toBe(true)
    }
    expect(REAL.scanned.length).toBe(Object.keys(ALL).filter(isProduction).length)
  })

  it('豁免表里的每个文件都在扫描面里（改名 / 删掉后的豁免不会悬着）', () => {
    for (const path of [...Object.keys(ALLOW), ...Object.keys(QUOTA)]) {
      expect(REAL.scanned, path).toContain(path)
    }
  })

  it('按处数的豁免与实际处数相等（多一处红、少一处也红：豁免不许悬着）', () => {
    for (const [path, quota] of Object.entries(QUOTA)) {
      expect(quotaUse(path, ALL[path], REAL.defs), path).toEqual({ opaque: 0, glob: 0, ...quota })
    }
  })

  it('没有文件让换算函数流出去（豁免表之外）', () => {
    expect(REAL.hits).toEqual([])
  })

  // ---- 流法全表：导入方式 × 转出方式 × 绑定种类，每格一条样本 ----
  //
  // 位置：UI = 普通生产文件（这里用 hooks/）；ALLOWED = 豁免文件（点名 panelScale）；
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
    ['纯类型 import', UI, "import type * as pf from '@/lib/preflight'", 'ok'],
    ['纯类型具名', UI, "import { type toPageValue, pagePtLens } from '@/lib/stylePresets'", 'ok'],
    ['纯副作用 import', UI, "import '@/lib/preflight'", 'ok'],
    ['定义者的其它导出', UI, "import { pagePtLens, PT_DECIMALS } from '@/lib/stylePresets'", 'ok'],
    ['别的模块', UI, "import * as api from '@/lib/api'\nconst { useRenderStore } = await import('@/store/renderStore')", 'ok'],
    ['注释里提到', UI, "// import * as pf from '@/lib/preflight'", 'ok'],
    // -- import.meta：不解析 glob 模式，出现即红（#557 评审五轮 P1）--
    ['glob 任意模式（不解析）', UI, "import.meta.glob('/src/i18n/locales/**/*.json')", 'red'],
    ['glob 字符类（五轮 P1 的例子）', UI, "import.meta.glob('/src/lib/preflight.[tj]s')", 'red'],
    ['glob extglob', UI, "import.meta.glob('/src/lib/+(preflight).ts')", 'red'],
    ['glob 非字面量', UI, 'import.meta.glob(pattern)', 'red'],
    ['glob 不调用、取出来', UI, 'const g = import.meta.glob', 'red'],
    ['import.meta.env', UI, 'if (import.meta.env?.DEV) f()', 'ok'],
    ['new URL 指向别的模块', UI, "new Worker(new URL('../playground/pyodide.worker.ts', import.meta.url))", 'ok'],
    ['new URL 指向定义者', UI, "new Worker(new URL('../lib/preflight.ts', import.meta.url))", 'red'],
    ['new URL 路径非字面量', UI, 'new URL(p, import.meta.url)', 'red'],
    ['import.meta.url 别的用法', UI, 'fetch(import.meta.url)', 'red'],
    ['import.meta.url 当 new URL 的第一个实参', UI, "new URL(import.meta.url, 'https://x')", 'red'],
    ['new URL 只有 import.meta.url 一个实参', UI, 'new URL(import.meta.url)', 'red'],
    ['import.meta.resolve', UI, "import.meta.resolve('@/lib/api')", 'red'],
    ['import.meta.hot', UI, 'import.meta.hot?.accept()', 'red'],
    ['import.meta 整个取出来', UI, 'const m = import.meta', 'red'],
    // -- 说明符：认得的四种之外一律红（fail closed）--
    ['认不出：带协议 https', UI, "import x from 'https://cdn.example/x.js'", 'red'],
    ['认不出：virtual:', UI, "import x from 'virtual:foo'", 'red'],
    ['认不出：% 编码', UI, "import { panelScale } from '@/lib/%70reflight'", 'red'],
    ['认不出：空白', UI, "import { panelScale } from '@/lib/ preflight'", 'red'],
    ['认不出：通配符', UI, "import x from '@/lib/pre*'", 'red'],
    ['认不出：没声明的包', UI, "import x from 'left-pad'", 'red'],
    ['认不出：没配上别名的 #', UI, "import x from '#conv/preflight'", 'red'],
    ['认不出：export from', UI, "export * from 'virtual:foo'", 'red'],
    ['认不出：require', UI, "require('data:text/javascript,1')", 'red'],
    ['声明过的包（含 scope 与子路径）', UI, "import { create } from 'zustand'\nimport * as D from '@radix-ui/react-dialog'\nimport x from 'react-dom/client'", 'ok'],
    ['node: 内建', UI, "import fs from 'node:fs'", 'ok'],
    ['语法错误', UI, 'import { panelScale from', 'red'],
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
    // -- 认得的说明符做规范化（#557 评审四轮 P1）--
    ['相对 .. 绕一圈', UI, "import { panelScale } from '../lib/../lib/preflight'", 'red'],
    ['重复斜杠', UI, "import { panelScale } from '@/lib//preflight'", 'red'],
    ['./ 段', UI, "import { panelScale } from '@/./lib/preflight'", 'red'],
    ['根绝对路径', UI, "import { panelScale } from '/src/lib/preflight.ts'", 'red'],
    ['根绝对路径指向别的模块', UI, "import { x } from '/src/lib/api.ts'", 'ok'],
    ['.js 扩展名', UI, "import { panelScale } from '@/lib/preflight.js'", 'red'],
    ['?raw 查询', UI, "import src from '@/lib/preflight.ts?raw'", 'red'],
    ['?url 查询', UI, "import { panelScale } from '@/lib/preflight?url'", 'red'],
    ['# 片段', UI, "import { panelScale } from '@/lib/preflight#x'", 'red'],
    ['大小写', UI, "import { panelScale } from '@/lib/PREFLIGHT'", 'red'],
    ['export from 归一', UI, "export * from '../lib/./stylePresets'", 'red'],
    ['require 归一', UI, "require('@/lib//preflight')", 'red'],
    ['动态 import 归一 + 查询', UI, "await import('../hooks/../lib/preflight.ts?x')", 'red'],
    ['归一后是别的模块', UI, "import { x } from '../lib/../store/renderStore'", 'ok'],
    ['tsconfig 精确别名（src 之外）', UI, "import p from '@profiles'", 'ok'],
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

  it('出现即红的写法按处数点名：处数之内不红、多一处就红、豁免文件以外一处就红', () => {
    const W = '/src/playground/pyodide.worker.ts'
    const one = 'await import(`${base}pyodide.mjs`)'
    expect(conversionHits(W, one, REAL.defs, [], QUOTA[W])).toEqual([])
    expect(conversionHits(W, `${one}\nawait import(other)`, REAL.defs, [], QUOTA[W])).not.toEqual([])
    expect(conversionHits(UI, one, REAL.defs)).not.toEqual([])
    const glob = "import.meta.glob('/src/i18n/*.json')"
    expect(conversionHits(UI, glob, REAL.defs, [], { glob: 1 })).toEqual([])
    expect(conversionHits(UI, `${glob}\n${glob}`, REAL.defs, [], { glob: 1 })).not.toEqual([])
    // 把 glob 取出来不调用：不算一处 glob，是认不出的 import.meta 用法，glob 的额度不放行它
    expect(conversionHits(UI, 'const g = import.meta.glob', REAL.defs, [], { glob: 1 })).not.toEqual([])
    // import.meta.url 不在 `new URL(字面量, import.meta.url)` 的第二个实参上：认不出，opaque 的额度也不放行它
    for (const src of ['new URL(import.meta.url)', "new URL(import.meta.url, 'https://x')"]) {
      expect(conversionHits(UI, src, REAL.defs, [], { opaque: 1 }), src).not.toEqual([])
    }
    // 两种写法各算各的：opaque 的额度不给 glob 用
    expect(conversionHits(UI, glob, REAL.defs, [], { opaque: 1 })).not.toEqual([])
    // 核对处数的工具本身：数得出 1 就是 1
    expect(quotaUse(UI, `${one}\n${glob}`, REAL.defs)).toEqual({ opaque: 1, glob: 1 })
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
      const { hits } = scanWith(extra)
      expect(hits.some((h) => h.startsWith('/src/lib/relay1.ts:')), JSON.stringify(hits)).toBe(true)
    }
  })

  // ---- 别名与依赖从配置里读（不写死 `@/`）----

  it('别名是从配置里读出来的：每份 vite / vitest 配置与 tsconfig 的 `@` 都指向 src；依赖表来自 package.json', () => {
    expect(Object.keys(CONFIG_SOURCES.vite).sort()).toEqual(
      ['/vite.config.ts', '/vite.mcp.config.ts', '/vite.playground.config.ts', '/vitest.config.ts'].sort(),
    )
    expect(Object.keys(CONFIG_SOURCES.tsconfig)).toContain('/tsconfig.app.json')
    expect(Object.keys(CONFIG_SOURCES.pkg)).toEqual(['/package.json'])
    // 每一份配置单独拿来解析 `@/lib/preflight`，都落在同一个模块上
    const perConfig: Array<[string, Alias[]]> = [
      ...Object.entries(CONFIG_SOURCES.vite).map(([p, s]): [string, Alias[]] => [p, viteAliases(p, s)]),
      ['/tsconfig.app.json', tsconfigAliases('/tsconfig.app.json', CONFIG_SOURCES.tsconfig['/tsconfig.app.json'])],
    ]
    for (const [path, aliases] of perConfig) {
      expect(resolveModule(UI, '@/lib/preflight', aliases), path).toBe('/src/lib/preflight')
    }
    // 读到了 src 之外的别名，也落在 src 之外
    expect(resolveModule(UI, '@profiles', REAL_ALIASES)).toMatch(/^\/\.\.\//)
    expect(REAL_DEPS.has('react')).toBe(true)
    expect(REAL_DEPS.has('vitest'), 'devDependencies 也算').toBe(true)
  })

  const withAliases = (extra: Alias[], src: string) =>
    conversionHits(UI, src, REAL.defs, [], {}, [...REAL_ALIASES, ...extra])
  const reachedDefiner = (hits: string[]) => hits.some((h) => /import \{ panelScale \}/.test(h))

  it.each([
    [
      'vite 对象形式的新别名 ~',
      () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { '~': fileURLToPath(new URL('./src', import.meta.url)) } } }"),
      "import { panelScale } from '~/lib/preflight'",
    ],
    [
      'vite 数组形式 find / replacement',
      () => viteAliases('/vite.config.ts', "export default { resolve: { alias: [{ find: '~', replacement: './src' }] } }"),
      "import { panelScale } from '~/lib/../lib/preflight'",
    ],
    [
      'tsconfig paths（带注释、baseUrl、通配）',
      () => tsconfigAliases('/tsconfig.app.json', '{ // c\n "compilerOptions": { "baseUrl": "src", "paths": { "conv/*": ["lib/*"] } } }'),
      "import { panelScale } from 'conv/preflight'",
    ],
    [
      'package.json imports 通配',
      () => packageImportAliases('/package.json', '{"imports": {"#conv/*": "./src/lib/*.ts"}}'),
      "import { panelScale } from '#conv/preflight'",
    ],
    [
      'package.json imports 最长前缀先配',
      () => packageImportAliases('/package.json', '{"imports": {"#*": "./other/*", "#conv/*": "./src/lib/*"}}'),
      "import { panelScale } from '#conv/preflight'",
    ],
    [
      'package.json imports 通配后缀（不是扩展名）',
      () => packageImportAliases('/package.json', '{"imports": {"#x/*": "./src/*flight.ts"}}'),
      "import { panelScale } from '#x/lib/pre'",
    ],
    [
      'package.json imports 条件对象',
      () => packageImportAliases('/package.json', '{"imports": {"#pf": {"browser": "./src/lib/preflight.ts", "default": null}}}'),
      "import { panelScale } from '#pf'",
    ],
  ])('配置里新加的别名自动进视野：%s', (_what, read, src) => {
    const extra = read()
    expect(extra.length).toBeGreaterThan(0)
    expect(reachedDefiner(withAliases(extra, src))).toBe(true)
    // 同一句在没有这条别名时认不出（fail closed 照样红），但不是「追到了定义者」：追到来自别名的读取
    const without = conversionHits(UI, src, REAL.defs)
    expect(without).not.toEqual([])
    expect(reachedDefiner(without)).toBe(false)
  })

  it('精确别名只配它自己和 `它/…`：`~preflight` 不是 `~` 别名（认不出，按红算，但没追到定义者）', () => {
    const tilde = viteAliases('/vite.config.ts', "export default { resolve: { alias: { '~': './src/lib' } } }")
    expect(reachedDefiner(withAliases(tilde, "import { panelScale } from '~/preflight'"))).toBe(true)
    const other = withAliases(tilde, "import { panelScale } from '~preflight'")
    expect(reachedDefiner(other)).toBe(false)
    expect(other.join()).toMatch(/认不出的说明符/)
  })

  it('三类配置都进了同一张别名表（任何一类没读，对应的解析就断）', () => {
    const aliases = aliasesFrom({
      vite: { '/vite.config.ts': "export default { resolve: { alias: { '~v': './src/lib' } } }" },
      tsconfig: { '/tsconfig.app.json': '{"compilerOptions": {"paths": {"~t/*": ["./src/lib/*"]}}}' },
      pkg: { '/package.json': '{"imports": {"#p/*": "./src/lib/*"}}' },
    })
    for (const spec of ['~v/preflight', '~t/preflight', '#p/preflight']) {
      expect(resolveModule(UI, spec, aliases), spec).toBe('/src/lib/preflight')
    }
  })

  it.each([
    ['vite 别名的目标是变量', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { '@': somePath } } }")],
    ['vite alias 是函数调用', () => viteAliases('/vite.config.ts', 'export default { resolve: { alias: makeAliases() } }')],
    ['vite alias 简写', () => viteAliases('/vite.config.ts', 'const alias = {}\nexport default { resolve: { alias } }')],
    ['vite alias 事后赋值', () => viteAliases('/vite.config.ts', 'config.resolve.alias = {}')],
    ['vite alias 下标赋值', () => viteAliases('/vite.config.ts', "config.resolve['alias'] = {}")],
    ['vite 别名展开进来', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { ...base, '~': './src' } } }")],
    ['vite 数组形式 find 是正则', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: [{ find: /^~/, replacement: './src' }] } }")],
    ['vite 数组形式带 customResolver', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: [{ find: '~', replacement: './src', customResolver: r }] } }")],
    ['vite 目标形状对、函数名不对', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { '~': toPath(new URL('./src', import.meta.url)) } } }")],
    ['vite 目标是别的函数', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { '~': path.resolve('./src') } } }")],
    ['vite 目标的 URL 基址不是 import.meta.url', () => viteAliases('/vite.config.ts', "export default { resolve: { alias: { '~': fileURLToPath(new URL('./src', base)) } } }")],
    ['vite resolve.extensions', () => viteAliases('/vite.config.ts', "export default { resolve: { extensions: ['.foo'] } }")],
    ['vite 配置语法错误', () => viteAliases('/vite.config.ts', 'export default { resolve: {')],
    ['tsconfig 通配在中间', () => tsconfigAliases('/tsconfig.app.json', '{"compilerOptions": {"paths": {"a*b": ["src/*"]}}}')],
    ['tsconfig extends', () => tsconfigAliases('/tsconfig.app.json', '{"extends": "./base.json"}')],
    ['tsconfig rootDirs', () => tsconfigAliases('/tsconfig.app.json', '{"compilerOptions": {"rootDirs": ["src", "gen"]}}')],
    ['tsconfig moduleSuffixes', () => tsconfigAliases('/tsconfig.app.json', '{"compilerOptions": {"moduleSuffixes": [".ios", ""]}}')],
    ['package.json imports 认不出的值', () => packageImportAliases('/package.json', '{"imports": {"#x": 1}}')],
  ])('读不出的配置写法直接报错（fail closed）：%s', (_what, read) => {
    expect(read).toThrow()
  })

  it('目录导入解析到 index.ts(x)：目录、带尾斜杠、显式 index 三种写法都红', () => {
    const extra = { '/src/lib/scale/index.tsx': 'export function panelScale() { return 1 }' }
    for (const spec of ['@/lib/scale', '@/lib/scale/', '../lib/scale/index.tsx', '../lib/Scale/./']) {
      const { hits } = scanWith({ ...extra, [UI]: `import { panelScale } from '${spec}'` })
      expect(hits.some((h) => h.startsWith(`${UI}:`)), spec).toBe(true)
    }
  })

  it.each([
    ['函数声明', 'export function panelScale() { return 1 }'],
    ['常量声明', 'export const panelScale = () => 1'],
    ['解构声明', 'export const { panelScale } = impl'],
    ['改名导出', 'const s = () => 1\nexport { s as panelScale }'],
    ['枚举', 'export enum panelScale { A }'],
    ['命名空间', 'export namespace panelScale { export const a = 1 }'],
    ['类', 'export class panelScale {}'],
  ])('定义者是现场认的（%s）：别处新声明或导出一个换算名，它就成了定义者，从它取值照样红', (_what, decl) => {
    const extra = { '/src/lib/scale2.ts': decl }
    const { defs, hits } = scanWith({ ...extra, [UI]: "import { panelScale } from '@/lib/scale2'" })
    expect([...defs.keys()], decl).toContain('/src/lib/scale2')
    expect(hits.some((h) => h.startsWith(`${UI}:`)), decl).toBe(true)
  })
})
