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
 * `panelScale` / `toPageValue` / `toScriptValue` 的界面文件就是第二份换算的起点（属性页以前
 * 正是没有这一步才与样式面板差出 0.6 倍）。界面一律过写入器，写入器过 `pagePtLens`。
 *
 * 判的是 import 声明（AST），不是子串：注释里提到这几个名字不算。
 * 豁免两条：样式对话框把 `panelScale` 交给 `extractFromManifest`（提取在 `stylePresets` 里
 * 换算，对话框自己不乘）；写入器 `useTextStyleAdapter` 按缩放比 memo 换好的字段表。
 */
describe('界面代码与 store 不自己做页面 pt 换算', () => {
  const SOURCES = import.meta.glob('/src/{components,canvas,store}/**/*.{ts,tsx}', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Record<string, string>
  const FORBIDDEN = new Set(['panelScale', 'toPageValue', 'toScriptValue', 'pageField'])
  const ALLOW: Record<string, string[]> = {
    '/src/components/StyleDialog.tsx': ['panelScale'],
    // 写入器本身：按缩放比 memo 住换好的字段表（与 `lens.field` 同一个函数，不是第二份换算）
    '/src/components/inspector/textStyleAdapter.ts': ['pageField'],
  }

  it('没有界面文件 import 换算函数（豁免表之外）', () => {
    const hits: string[] = []
    let scanned = 0
    for (const [path, src] of Object.entries(SOURCES)) {
      if (/\.test\.tsx?$/.test(path)) continue
      scanned++
      const file = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
      for (const st of file.statements) {
        if (!ts.isImportDeclaration(st)) continue
        const named = st.importClause?.namedBindings
        if (!named || !ts.isNamedImports(named)) continue
        for (const spec of named.elements) {
          const name = (spec.propertyName ?? spec.name).text
          if (FORBIDDEN.has(name) && !(ALLOW[path] ?? []).includes(name)) hits.push(`${path}: ${name}`)
        }
      }
    }
    expect(scanned, '扫描面该覆盖到界面代码').toBeGreaterThan(100)
    expect(hits).toEqual([])
  })
})
