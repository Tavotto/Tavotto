/**
 * 图标体系的门禁（用户反馈第 7 条：「图标非常不统一」）。
 *
 * 统一过一次之后如果没有门禁，下一次「顺手画个 svg」「随手 size={13}」几分钟
 * 就能把它散回去——这和 `nativeSelect.test.ts` 守原生 `<select>` 是同一件事。
 * 规则的正文在 `Icon.tsx` 与 `docs/ux/ICONOGRAPHY.md`，这里只钉四条能用源码
 * 结构判出来的：
 *
 *   1. 不许手写内联 `<svg>` 当图标。例外只有「画的是用户数据 / 样式样本 /
 *      品牌标」的那几个文件，而且**按个数**豁免——给某个文件豁免一个样本图，
 *      不等于允许它再添第二个手绘图标（豁免粒度见文末 SVG_ALLOWLIST）。
 *   2. lucide 图标的 `size` 必须写成 `ICON_SIZE.<档>`，`strokeWidth` 必须写成
 *      `ICON_STROKE.<档>`，不许 `absoluteStrokeWidth`。任何大写标签上的数字
 *      字面量 `size={13}` 都算（通过 `icon: Icon` 间接渲染的也逃不掉），只放过
 *      几个「size 不是图标尺寸」的组件（品牌标、Agent 头像框、AI 面板的
 *      生成式加载器）。
 *   3. 从 lucide-react 引入的名字必须是**规范名**（`icons` 表里有的那个）。
 *      `AlertTriangle` 与 `TriangleAlert` 是同一张图，两个名字并存的结果就是
 *      grep 不出「警告图标一共用在哪」。
 *   4. 不许拿 Unicode 字符 / emoji 当图标（JSX 文本里单独一个 ✕ ▸ ▾ …）。
 *      快捷键提示里的 ⌘ ⇧ ⏎ 是按键名不是图标，那些在字符串里，不在这条规则内。
 *   5. 折叠块不用原生 `<summary>`：浏览器自带的实心三角每家长得都不一样，也对不上
 *      树 / 检查器里的折叠箭头。统一走 `ui/Details` 的 `Summary`（lucide ChevronRight）。
 *
 * 判源码结构用 TypeScript 的 AST 而不是正则：注释、docstring、`onClick={() =>`
 * 里的 `>` 都会咬正则（根 AGENTS.md「判据的主语」一节）。
 *
 * 读文件走 `import.meta.glob('?raw')` 而不是 node:fs——src 归 tsconfig.app.json
 * 管，理由同 `nativeSelect.test.ts`。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import ts from 'typescript'
import { ChevronRight, X, icons } from 'lucide-react'
import { describe, expect, it } from 'vitest'

import { ICON_SIZE, ICON_STROKE, IconProvider } from './Icon'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

const isTest = (path: string) => /\.test\.tsx?$/.test(path)

/**
 * 内联 svg 的豁免表：路径 → 允许的个数。每一条都得是「画的不是图标」：
 * 画布上的形状 / 箭头 / 选框本体、检查器里跟着用户当前样式变的样本图、
 * 品牌标。加一条之前先问：这张图换成 lucide 的哪个会失去信息？答不上来就
 * 不该加。
 */
const SVG_ALLOWLIST: Record<string, number> = {
  '/src/canvas/ShapeView.tsx': 1, // 画布形状本体
  '/src/canvas/ArrowView.tsx': 1, // 画布箭头本体
  '/src/canvas/OverlaySvg.tsx': 1, // 选框 / 手柄 / 参考线覆盖层
  '/src/components/ui/BrandMark.tsx': 1, // 品牌标（唯一出处 lib/brand.ts 的图形侧）
  '/src/components/inspector/controls/TickAndSpineDiagram.tsx': 2, // 四边刻度示意图 + 位置点
  '/src/components/inspector/controls/TickTaskCard.tsx': 1, // 刻度朝向示意（in/out/both）
  '/src/components/inspector/controls/HatchPicker.tsx': 1, // 填充纹样样本
  '/src/components/inspector/StrokeSection.tsx': 2, // 画布标注的线型样本 + 箭头端型样本（跟着当前值画）
  '/src/components/inspector/controls/SpineFrameCard.tsx': 1, // 边框四边示意：点亮的是当前在改的那一条边
  '/src/components/inspector/controls/ArrowPickers.tsx': 1, // 箭头头尾样本
  '/src/components/inspector/controls/LineStylePicker.tsx': 1, // 线型样本
  '/src/components/inspector/controls/MarkerPicker.tsx': 1, // 标记形状样本
  '/src/components/inspector/LegendCard.tsx': 1, // 图例句柄样本（跟着 handle_* 走）
  // 画布缩略图：页面比例里按对象落位画这份文档的真实内容（审计 T04 之后
  // 画布列表与版本列表共用这一份，原来那条豁免在 left/CanvasList.tsx 上）
  '/src/components/CanvasThumb.tsx': 1,
  '/src/components/inspector/controls/ErrorBarDiagram.tsx': 1, // 误差棒的线 / 端帽示意
  '/src/components/inspector/controls/ProjectionPicker.tsx': 1, // 三维投影小立方体
  '/src/components/inspector/controls/ViewAngleDiagram.tsx': 1, // 三维三轴方向示意（按当前角度重画）
  '/src/components/settings/AgentIcon.tsx': 2, // Claude / OpenAI 两个品牌标
  '/src/components/settings/CompanionDiagram.tsx': 1, // 「一同移动关联对象」的前后空间关系
  '/src/components/settings/StyleSamplePreview.tsx': 1, // 样式示例图（viewBox 单位就是 pt）
  // 「当前画布」范围的版面示意：页面比例 + 每个对象的落位方块，画的是这份
  // 文档的几何，换成任何一个 lucide 图标都会把它变成一张与内容无关的图
  '/src/components/ExportDialog.tsx': 1,
  // 图例位置：子图容器边界 + 图例此刻落在哪（跟着 loc / bbox_to_anchor 走）+ 六个
  // 外侧预设的缩略示意，全在同一张 svg 里。画的是这张图自己的几何，不是图标
  '/src/components/inspector/controls/LegendPositionPicker.tsx': 1,
}

/** `size` 是别的意思（外框边长 / 加载器尺寸）的组件，数字字面量放行 */
const NON_ICON_SIZED = new Set(['BrandMark', 'AgentIcon', 'InlineLoader', 'TextLoader'])

/** lucide-react 里不是图标、但允许引入的名字 */
const NON_ICON_EXPORTS = new Set(['LucideProvider', 'LucideIcon', 'LucideProps', 'icons'])

const GLYPH_ICONS = /^[✕×✓✔✗▸▾▴▶▼◀▲►◄•●○◦⋯»«‹›]$/u
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/u

export interface Finding {
  kind:
    | 'inline-svg'
    | 'raw-size'
    | 'raw-stroke'
    | 'absolute-stroke'
    | 'alias-import'
    | 'glyph'
    | 'native-summary'
  line: number
  detail: string
}

function parse(path: string, src: string) {
  return ts.createSourceFile(
    path,
    src,
    ts.ScriptTarget.Latest,
    true,
    path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  )
}

/** 一个文件的全部发现（豁免在调用方判，这里只如实报） */
export function audit(path: string, src: string): Finding[] {
  const sf = parse(path, src)
  const out: Finding[] = []
  const lineOf = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1
  const lucideLocals = new Set<string>()

  const visit = (n: ts.Node) => {
    if (ts.isImportDeclaration(n) && ts.isStringLiteral(n.moduleSpecifier)) {
      if (n.moduleSpecifier.text === 'lucide-react') {
        const nb = n.importClause?.namedBindings
        if (nb && ts.isNamedImports(nb)) {
          for (const el of nb.elements) {
            const imported = (el.propertyName ?? el.name).text
            lucideLocals.add(el.name.text)
            if (!(imported in icons) && !NON_ICON_EXPORTS.has(imported)) {
              out.push({ kind: 'alias-import', line: lineOf(el), detail: imported })
            }
          }
        }
      }
    }
    if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
      const tag = n.tagName.getText(sf)
      // Provider 本身就是配置点，它身上的 size / strokeWidth 不算「用点手写」
      if (tag === 'LucideProvider') {
        ts.forEachChild(n, visit)
        return
      }
      if (tag === 'svg') out.push({ kind: 'inline-svg', line: lineOf(n), detail: '<svg>' })
      if (tag === 'summary') out.push({ kind: 'native-summary', line: lineOf(n), detail: '<summary>' })
      const capitalized = /^[A-Z]/.test(tag)
      for (const attr of n.attributes.properties) {
        if (!ts.isJsxAttribute(attr)) continue
        const name = attr.name.getText(sf)
        if (name === 'absoluteStrokeWidth' && lucideLocals.has(tag)) {
          out.push({ kind: 'absolute-stroke', line: lineOf(attr), detail: `<${tag}>` })
        }
        if (!attr.initializer) continue
        const init = attr.initializer
        const expr = ts.isJsxExpression(init) ? init.expression : init
        const text = expr?.getText(sf) ?? ''
        if (name === 'size' && capitalized && !NON_ICON_SIZED.has(tag)) {
          const numeric =
            (expr && ts.isNumericLiteral(expr)) || (ts.isStringLiteral(init) && /^\d+$/.test(init.text))
          const isLucide = lucideLocals.has(tag)
          if (numeric || (isLucide && !/^ICON_SIZE(\.(xs|sm|md|lg)|\[.+\])$/.test(text))) {
            out.push({ kind: 'raw-size', line: lineOf(attr), detail: `<${tag} size=${text}>` })
          }
        }
        // 间接渲染（`icon: Icon`）的描边也不许手写数字：加粗只有 emphasis 一档
        const numericStroke = capitalized && !!expr && ts.isNumericLiteral(expr)
        if (name === 'strokeWidth' && (lucideLocals.has(tag) || numericStroke)) {
          if (!/^ICON_STROKE\.(regular|emphasis)$/.test(text)) {
            out.push({ kind: 'raw-stroke', line: lineOf(attr), detail: `<${tag} strokeWidth=${text}>` })
          }
        }
      }
    }
    if (ts.isJsxText(n)) {
      const t = n.text.trim()
      // 单独撑起一个元素的 ✕ / ▸ 才是「拿字符当图标」；`×{used}`、`{w} × {h}`
      // 里的 × 是乘号，有旁边的兄弟节点作证
      const siblings = ts.isJsxElement(n.parent)
        ? n.parent.children.filter((c) => !(ts.isJsxText(c) && c.containsOnlyTriviaWhiteSpaces))
        : []
      const alone = siblings.length === 1 && siblings[0] === n
      if (alone && GLYPH_ICONS.test(t)) out.push({ kind: 'glyph', line: lineOf(n), detail: t })
      if (EMOJI.test(t)) out.push({ kind: 'glyph', line: lineOf(n), detail: t })
    }
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && EMOJI.test(n.text)) {
      out.push({ kind: 'glyph', line: lineOf(n), detail: n.text })
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return out
}

function offenders(kind: Finding['kind']): string[] {
  const rows: string[] = []
  for (const [path, src] of Object.entries(SOURCES)) {
    if (isTest(path)) continue
    const hits = audit(path, src).filter((f) => f.kind === kind)
    if (kind === 'inline-svg') {
      const allowed = SVG_ALLOWLIST[path] ?? 0
      if (hits.length > allowed) rows.push(`${path}（${hits.length} 处，豁免 ${allowed}）`)
      continue
    }
    if (kind === 'native-summary' && path === '/src/components/ui/Details.tsx') continue
    for (const h of hits) rows.push(`${path}:${h.line} ${h.detail}`)
  }
  return rows
}

describe('图标只有一套：lucide-react + Icon.tsx 的阶梯', () => {
  it('没有手写的内联 svg 图标（样本图 / 画布本体 / 品牌标按个数豁免）', () => {
    expect(
      offenders('inline-svg'),
      '这些文件里有超出豁免个数的内联 <svg>；图标用 lucide-react，样本图要加豁免就写清它画的是什么用户数据',
    ).toEqual([])
  })

  it('豁免表里的每个文件都还存在、个数没有虚高', () => {
    for (const [path, allowed] of Object.entries(SVG_ALLOWLIST)) {
      const src = SOURCES[path]
      expect(src, `${path} 已不存在，把它从豁免表里删掉`).toBeDefined()
      const n = audit(path, src!).filter((f) => f.kind === 'inline-svg').length
      expect(n, `${path} 实际 ${n} 处内联 svg，豁免却给了 ${allowed}——把数字收紧`).toBe(allowed)
    }
  })

  it('lucide 图标的尺寸都来自 ICON_SIZE，没有数字字面量', () => {
    expect(offenders('raw-size'), '尺寸只有 xs/sm/md/lg 四档，写成 size={ICON_SIZE.sm}').toEqual([])
  })

  it('描边粗细由 IconProvider 统一给，不在用点手写', () => {
    expect(offenders('raw-stroke'), '要加粗只有 ICON_STROKE.emphasis 一档（填色方块里的对勾）').toEqual([])
    expect(offenders('absolute-stroke'), '描边按比例缩放，不用 absoluteStrokeWidth').toEqual([])
  })

  it('从 lucide-react 引入的都是规范名（别名会让同一张图有两个名字）', () => {
    expect(offenders('alias-import')).toEqual([])
  })

  it('没有拿 Unicode 字符或 emoji 当图标', () => {
    expect(offenders('glyph')).toEqual([])
  })

  it('折叠块都走 ui/Details，没有裸的原生 <summary>', () => {
    expect(offenders('native-summary'), '用 Details / Summary（折叠箭头是 lucide 的，不是浏览器的三角）').toEqual(
      [],
    )
  })
})

describe('自检：判据认得出每一种违规（不是空门禁）', () => {
  const kinds = (src: string) => audit('/x/a.tsx', src).map((f) => f.kind)

  it('内联 svg', () => {
    expect(kinds('const a = <svg width="11"><path d="M0 0" /></svg>')).toContain('inline-svg')
    expect(kinds('// 注释里提 <svg> 不算\nconst a = <span />')).not.toContain('inline-svg')
  })

  it('数字尺寸：直接用的与间接渲染的都抓', () => {
    const head = "import { Check } from 'lucide-react'\n"
    expect(kinds(head + 'const a = <Check size={13} />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size="13" />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size={someVar} />')).toContain('raw-size')
    expect(kinds('const a = <Icon size={12} />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size={ICON_SIZE.sm} />')).not.toContain('raw-size')
    expect(kinds(head + 'const a = <Check />')).not.toContain('raw-size')
    expect(kinds('const a = <BrandMark size={20} />')).not.toContain('raw-size')
    expect(kinds('const a = <Icon size={size} />')).not.toContain('raw-size')
    expect(kinds('const a = <Button size="sm" />')).not.toContain('raw-size')
  })

  it('描边', () => {
    const head = "import { Check } from 'lucide-react'\n"
    expect(kinds(head + 'const a = <Check strokeWidth={3} />')).toContain('raw-stroke')
    expect(kinds(head + 'const a = <Check strokeWidth={ICON_STROKE.emphasis} />')).not.toContain('raw-stroke')
    expect(kinds(head + 'const a = <Check absoluteStrokeWidth />')).toContain('absolute-stroke')
    expect(kinds('const a = <path strokeWidth={1} />')).not.toContain('raw-stroke')
  })

  it('别名引入', () => {
    expect(kinds("import { AlertTriangle } from 'lucide-react'")).toContain('alias-import')
    expect(kinds("import { TriangleAlert, type LucideIcon } from 'lucide-react'")).not.toContain(
      'alias-import',
    )
    expect(kinds("import { AlertTriangle } from 'somewhere-else'")).not.toContain('alias-import')
  })

  it('原生 summary', () => {
    expect(kinds('const a = <details><summary>x</summary></details>')).toContain('native-summary')
    expect(kinds('const a = <Details><Summary>x</Summary></Details>')).not.toContain('native-summary')
  })

  it('字符图标与 emoji', () => {
    expect(kinds('const a = <button>✕</button>')).toContain('glyph')
    expect(kinds('const a = <span>▸</span>')).toContain('glyph')
    // 单独一个 × 是「删除」图标；夹在尺寸里的 × 是乘号，下面那条负例守着它
    expect(kinds('const a = <span aria-hidden>×</span>')).toContain('glyph')
    expect(kinds("const a = t('x', { hint: '🎉 done' })")).toContain('glyph')
    expect(kinds('const a = <span>{`⇧${MOD}S`}</span>')).not.toContain('glyph')
    expect(kinds('const a = <span>80 × 57 mm</span>')).not.toContain('glyph')
    expect(kinds('const a = <span>×{used}</span>')).not.toContain('glyph')
    expect(kinds('const a = <span>{w} × {h}</span>')).not.toContain('glyph')
  })
})

describe('IconProvider：不写尺寸就拿到默认档', () => {
  async function mount(node: React.ReactNode) {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    await act(async () => root.render(node))
    return {
      svg: (id: string) => host.querySelector<SVGSVGElement>(`[data-testid="${id}"]`)!,
      unmount: async () => {
        await act(async () => root.unmount())
        host.remove()
      },
    }
  }

  it('默认 sm 档 + regular 描边 + 不被压扁', async () => {
    const m = await mount(
      <IconProvider>
        <X data-testid="plain" />
        <ChevronRight data-testid="md" size={ICON_SIZE.md} />
      </IconProvider>,
    )
    const plain = m.svg('plain')
    expect(plain.getAttribute('width')).toBe(String(ICON_SIZE.sm))
    expect(plain.getAttribute('height')).toBe(String(ICON_SIZE.sm))
    expect(plain.getAttribute('stroke-width')).toBe(String(ICON_STROKE.regular))
    expect(plain.getAttribute('class')).toContain('shrink-0')
    // 描边按比例：改了尺寸，属性值不变（viewBox 24 里的 1.75，画到 16px 上自然变细）
    const md = m.svg('md')
    expect(md.getAttribute('width')).toBe(String(ICON_SIZE.md))
    expect(md.getAttribute('stroke-width')).toBe(String(ICON_STROKE.regular))
    await m.unmount()
  })

  it('对照：没套 Provider 时 lucide 自己的默认是 24 / 2——这正是每个根都要套的原因', async () => {
    const m = await mount(<X data-testid="bare" />)
    expect(m.svg('bare').getAttribute('width')).toBe('24')
    expect(m.svg('bare').getAttribute('stroke-width')).toBe('2')
    await m.unmount()
  })
})

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
