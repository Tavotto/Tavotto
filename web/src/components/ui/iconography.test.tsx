/**
 * 图标体系的门禁（用户反馈第 7 条：「图标非常不统一」；2026-09-15 换成自绘图标集，ADR 0052）。
 *
 * 统一过一次之后如果没有门禁，下一次「顺手画个 svg」「随手 size={13}」「顺手装个
 * 图标库」几分钟就能把它散回去——这和 `nativeSelect.test.ts` 守原生 `<select>` 是
 * 同一件事。规则的正文在 `Icon.tsx` 与 `docs/ux/ICONOGRAPHY.md`，这里只钉几条能用
 * 源码结构判出来的：
 *
 *   1. 不许手写内联 `<svg>` 当图标。例外只有「画的是用户数据 / 样式样本 / 品牌标」
 *      的那几个文件，加上图标集本体（`icons/createIcon.tsx`），而且**按个数**豁免——
 *      给某个文件豁免一个样本图，不等于允许它再添第二个手绘图标（豁免粒度见文末
 *      SVG_ALLOWLIST）。
 *   2. 图标的 `size` 必须写成 `ICON_SIZE.<档>`，`strokeWidth` 必须写成
 *      `ICON_STROKE.<档>`。任何大写标签上的数字字面量 `size={13}` 都算（通过
 *      `icon: Icon` 间接渲染的也逃不掉），只放过几个「size 不是图标尺寸」的组件
 *      （品牌标、Agent 头像框、AI 面板的生成式加载器）。
 *   3. 图标只从 `components/ui/icons` 引入，名字必须是图标集里有的那个（同一张图
 *      只有一个名字，grep 得出「警告图标一共用在哪」）；**任何第三方图标库都不许
 *      引入**——lucide-react 已经拆掉，别再装回来。
 *   4. 不许拿 Unicode 字符 / emoji 当图标（JSX 文本里单独一个 ✕ ▸ ▾ …）。
 *      快捷键提示里的 ⌘ ⇧ ⏎ 是按键名不是图标，那些在字符串里，不在这条规则内。
 *   5. 折叠块不用原生 `<summary>`：浏览器自带的实心三角每家长得都不一样，也对不上
 *      树 / 检查器里的折叠箭头。统一走 `ui/Details` 的 `Summary`（ChevronRight）。
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
import { describe, expect, it } from 'vitest'

import { ICON_SIZE, ICON_STROKE, IconProvider } from './Icon'
import { ChevronRight, ICON_DEFS, TriangleAlert, Type, X, type IconDef } from './icons'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

const isTest = (path: string) => /\.test\.tsx?$/.test(path)

/**
 * 内联 svg 的豁免表：路径 → 允许的个数。每一条都得是「画的不是图标」：
 * 画布上的形状 / 箭头 / 选框本体、检查器里跟着用户当前样式变的样本图、
 * 品牌标——加上图标集本体那一个 `<svg>`。加一条之前先问：这张图换成图标集里的
 * 哪个会失去信息？答不上来就不该加。
 */
const SVG_ALLOWLIST: Record<string, number> = {
  '/src/components/ui/icons/createIcon.tsx': 1, // 图标集本体：141 个图标都从这一个 <svg> 出来
  '/src/canvas/ShapeView.tsx': 1, // 画布形状本体
  '/src/canvas/ArrowView.tsx': 1, // 画布箭头本体
  '/src/canvas/OverlaySvg.tsx': 1, // 选框 / 手柄 / 参考线覆盖层
  '/src/components/ui/BrandMark.tsx': 1, // 品牌标（唯一出处 lib/brand.ts 的图形侧）
  '/src/components/inspector/controls/TickAndSpineDiagram.tsx': 1, // 四边刻度示意图（网格开关那枚小 svg 已随 Toggle 回收）
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
  // 主页新手版三步卡片的示意：脚本文件 → 认出来的几张图 → 排版画布（画的是产品流程，图标集里没有对应物）
  '/src/components/home/StepIllustrations.tsx': 3,
  '/src/components/settings/CompanionDiagram.tsx': 1, // 「一同移动关联对象」的前后空间关系
  '/src/components/settings/StyleSamplePreview.tsx': 1, // 样式示例图（viewBox 单位就是 pt）
  // 图例位置：子图容器边界 + 图例此刻落在哪（跟着 loc / bbox_to_anchor 走）+ 六个
  // 外侧预设的缩略示意，全在同一张 svg 里。画的是这张图自己的几何，不是图标
  '/src/components/inspector/controls/LegendPositionPicker.tsx': 1,
}

/** `size` 是别的意思（外框边长 / 加载器尺寸）的组件，数字字面量放行 */
const NON_ICON_SIZED = new Set(['BrandMark', 'AgentIcon'])

/** 图标集里不是图标、但允许引入的名字 */
const NON_ICON_EXPORTS = new Set(['ICON_DEFS', 'IconComponent', 'IconProps', 'IconDef', 'IconName'])

/** 第三方图标库：一个都不许出现在 import 里 */
const FOREIGN_ICON_LIBS = new Set([
  'lucide-react',
  'lucide',
  '@heroicons/react',
  'react-icons',
  '@tabler/icons-react',
  '@radix-ui/react-icons',
  '@phosphor-icons/react',
])

/** 图标集的 import 路径：别名、相对（ui/ 内部 `./icons`、隔壁 `../ui/icons`） */
const isIconModule = (specifier: string) =>
  specifier === '@/components/ui/icons' || /(^|\/)icons$/.test(specifier)

const GLYPH_ICONS = /^[✕×✓✔✗▸▾▴▶▼◀▲►◄•●○◦⋯»«‹›]$/u
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/u

export interface Finding {
  kind:
    | 'inline-svg'
    | 'raw-size'
    | 'raw-stroke'
    | 'absolute-stroke'
    | 'alias-import'
    | 'foreign-icon-lib'
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
  const iconLocals = new Set<string>()

  const visit = (n: ts.Node) => {
    if (ts.isImportDeclaration(n) && ts.isStringLiteral(n.moduleSpecifier)) {
      const specifier = n.moduleSpecifier.text
      if (FOREIGN_ICON_LIBS.has(specifier) || specifier.startsWith('lucide-react/')) {
        out.push({ kind: 'foreign-icon-lib', line: lineOf(n), detail: specifier })
      }
      if (isIconModule(specifier)) {
        const nb = n.importClause?.namedBindings
        if (nb && ts.isNamedImports(nb)) {
          for (const el of nb.elements) {
            const imported = (el.propertyName ?? el.name).text
            iconLocals.add(el.name.text)
            if (!(imported in ICON_DEFS) && !NON_ICON_EXPORTS.has(imported)) {
              out.push({ kind: 'alias-import', line: lineOf(el), detail: imported })
            }
          }
        }
      }
    }
    if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
      const tag = n.tagName.getText(sf)
      // Provider 本身就是配置点，它身上的 size / strokeWidth 不算「用点手写」
      if (tag === 'IconProvider') {
        ts.forEachChild(n, visit)
        return
      }
      if (tag === 'svg') out.push({ kind: 'inline-svg', line: lineOf(n), detail: '<svg>' })
      if (tag === 'summary') out.push({ kind: 'native-summary', line: lineOf(n), detail: '<summary>' })
      const capitalized = /^[A-Z]/.test(tag)
      for (const attr of n.attributes.properties) {
        if (!ts.isJsxAttribute(attr)) continue
        const name = attr.name.getText(sf)
        if (name === 'absoluteStrokeWidth' && iconLocals.has(tag)) {
          out.push({ kind: 'absolute-stroke', line: lineOf(attr), detail: `<${tag}>` })
        }
        if (!attr.initializer) continue
        const init = attr.initializer
        const expr = ts.isJsxExpression(init) ? init.expression : init
        const text = expr?.getText(sf) ?? ''
        if (name === 'size' && capitalized && !NON_ICON_SIZED.has(tag)) {
          const numeric =
            (expr && ts.isNumericLiteral(expr)) || (ts.isStringLiteral(init) && /^\d+$/.test(init.text))
          const isIcon = iconLocals.has(tag)
          if (numeric || (isIcon && !/^ICON_SIZE(\.(xs|sm|md|lg)|\[.+\])$/.test(text))) {
            out.push({ kind: 'raw-size', line: lineOf(attr), detail: `<${tag} size=${text}>` })
          }
        }
        // 间接渲染（`icon: Icon`）的描边也不许手写数字：加粗只有 emphasis 一档
        const numericStroke = capitalized && !!expr && ts.isNumericLiteral(expr)
        if (name === 'strokeWidth' && (iconLocals.has(tag) || numericStroke)) {
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

describe('图标只有一套：components/ui/icons + Icon.tsx 的阶梯', () => {
  it('没有手写的内联 svg 图标（样本图 / 画布本体 / 品牌标 / 图标集本体按个数豁免）', () => {
    expect(
      offenders('inline-svg'),
      '这些文件里有超出豁免个数的内联 <svg>；图标从 ui/icons 取，样本图要加豁免就写清它画的是什么用户数据',
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

  it('图标的尺寸都来自 ICON_SIZE，没有数字字面量', () => {
    expect(offenders('raw-size'), '尺寸只有 xs/sm/md/lg 四档，写成 size={ICON_SIZE.sm}').toEqual([])
  })

  it('描边粗细由 IconProvider 统一给，不在用点手写', () => {
    expect(offenders('raw-stroke'), '要加粗只有 ICON_STROKE.emphasis 一档（填色方块里的对勾）').toEqual([])
    expect(offenders('absolute-stroke'), '描边按比例缩放，没有 absoluteStrokeWidth 这回事').toEqual([])
  })

  it('从图标集引入的都是图标集里有的名字（别名会让同一张图有两个名字）', () => {
    expect(offenders('alias-import')).toEqual([])
  })

  it('没有任何第三方图标库（lucide-react 已拆掉，别装回来）', () => {
    expect(offenders('foreign-icon-lib')).toEqual([])
  })

  it('没有拿 Unicode 字符或 emoji 当图标', () => {
    expect(offenders('glyph')).toEqual([])
  })

  it('折叠块都走 ui/Details，没有裸的原生 <summary>', () => {
    expect(offenders('native-summary'), '用 Details / Summary（折叠箭头是图标集的，不是浏览器的三角）').toEqual(
      [],
    )
  })

  it('图标集里每个名字都渲染得出来（几何定义不缺项）', () => {
    for (const [name, def] of Object.entries(ICON_DEFS as Record<string, IconDef>)) {
      const parts = [...(def.s ?? []), ...(def.f ?? [])]
      expect(parts.length, `${name} 一条路径都没有`).toBeGreaterThan(0)
      for (const p of [...parts, ...(def.k ?? []), ...(def.o ?? [])]) {
        const d = typeof p === 'string' ? p : p.d
        expect(d, `${name} 的路径不是以 M 开头`).toMatch(/^M[-\d.]/)
        expect(d, `${name} 的路径里有 NaN`).not.toMatch(/NaN|undefined/)
      }
    }
  })
})

describe('自检：判据认得出每一种违规（不是空门禁）', () => {
  const kinds = (src: string) => audit('/x/a.tsx', src).map((f) => f.kind)
  const head = "import { Check } from '@/components/ui/icons'\n"

  it('内联 svg', () => {
    expect(kinds('const a = <svg width="11"><path d="M0 0" /></svg>')).toContain('inline-svg')
    expect(kinds('// 注释里提 <svg> 不算\nconst a = <span />')).not.toContain('inline-svg')
  })

  it('数字尺寸：直接用的与间接渲染的都抓', () => {
    expect(kinds(head + 'const a = <Check size={13} />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size="13" />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size={someVar} />')).toContain('raw-size')
    expect(kinds('const a = <Icon size={12} />')).toContain('raw-size')
    expect(kinds(head + 'const a = <Check size={ICON_SIZE.sm} />')).not.toContain('raw-size')
    expect(kinds(head + 'const a = <Check />')).not.toContain('raw-size')
    expect(kinds('const a = <BrandMark size={20} />')).not.toContain('raw-size')
    expect(kinds('const a = <Icon size={size} />')).not.toContain('raw-size')
    expect(kinds('const a = <Button size="sm" />')).not.toContain('raw-size')
    // ui/ 内部的相对路径也算图标集
    expect(kinds("import { Check } from './icons'\nconst a = <Check size={13} />")).toContain('raw-size')
  })

  it('描边', () => {
    expect(kinds(head + 'const a = <Check strokeWidth={3} />')).toContain('raw-stroke')
    expect(kinds(head + 'const a = <Check strokeWidth={ICON_STROKE.emphasis} />')).not.toContain('raw-stroke')
    expect(kinds(head + 'const a = <Check absoluteStrokeWidth />')).toContain('absolute-stroke')
    expect(kinds('const a = <path strokeWidth={1} />')).not.toContain('raw-stroke')
  })

  it('别名引入与第三方图标库', () => {
    expect(kinds("import { AlertTriangle } from '@/components/ui/icons'")).toContain('alias-import')
    expect(kinds("import { TriangleAlert, type IconComponent } from '@/components/ui/icons'")).not.toContain(
      'alias-import',
    )
    expect(kinds("import { AlertTriangle } from 'somewhere-else'")).not.toContain('alias-import')
    expect(kinds("import { X } from 'lucide-react'")).toContain('foreign-icon-lib')
    expect(kinds("import { X } from 'lucide-react/icons/x'")).toContain('foreign-icon-lib')
    expect(kinds("import { X } from '@/components/ui/icons'")).not.toContain('foreign-icon-lib')
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

describe('图标组件：默认档、选中态、无障碍', () => {
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

  it('默认 sm 档 + regular 描边 + 不被压扁 + 按名字的类名', async () => {
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
    expect(plain.getAttribute('class')).toContain('icon-x')
    // 描边按比例：改了尺寸，属性值不变（viewBox 24 里的 2，画到 16px 上自然是 1.33px）
    const md = m.svg('md')
    expect(md.getAttribute('width')).toBe(String(ICON_SIZE.md))
    expect(md.getAttribute('stroke-width')).toBe(String(ICON_STROKE.regular))
    expect(md.getAttribute('class')).toContain('icon-chevron-right')
    await m.unmount()
  })

  it('没套 Provider 也是同一套默认档（图标集自己的默认就是阶梯）；Provider 只是根上改默认的地方', async () => {
    const m = await mount(
      <>
        <X data-testid="bare" />
        <IconProvider size={ICON_SIZE.lg}>
          <X data-testid="lg" />
        </IconProvider>
      </>,
    )
    expect(m.svg('bare').getAttribute('width')).toBe(String(ICON_SIZE.sm))
    expect(m.svg('bare').getAttribute('stroke-width')).toBe(String(ICON_STROKE.regular))
    expect(m.svg('lg').getAttribute('width')).toBe(String(ICON_SIZE.lg))
    await m.unmount()
  })

  it('filled 渲染实心孪生（有 mask 挖空 + 填色路径）；没有孪生的图标忽略它', async () => {
    const m = await mount(
      <>
        <TriangleAlert data-testid="outline" />
        <TriangleAlert data-testid="filled" filled />
        <Type data-testid="type-filled" filled />
      </>,
    )
    const outline = m.svg('outline')
    const filled = m.svg('filled')
    expect(outline.querySelector('mask')).toBeNull()
    expect(outline.querySelectorAll('path[fill="currentColor"]').length, '描边态只有那颗点是填色的').toBe(1)
    expect(filled.querySelector('mask'), '实心态用蒙版挖出叹号').not.toBeNull()
    expect(filled.querySelectorAll('g[mask] path[fill="currentColor"]').length).toBeGreaterThan(0)
    // 两个实心图标同页，蒙版 id 不能串
    const m2 = await mount(<TriangleAlert data-testid="filled2" filled />)
    expect(m2.svg('filled2').querySelector('mask')!.id).not.toBe(filled.querySelector('mask')!.id)
    // Type 没有孪生：filled 与否渲染一样
    expect(m.svg('type-filled').querySelector('mask')).toBeNull()
    expect(m.svg('type-filled').querySelectorAll('path').length).toBe(2)
    await m2.unmount()
    await m.unmount()
  })

  it('无障碍：默认 aria-hidden，给了 aria-label 就是一张有名字的图', async () => {
    const m = await mount(
      <>
        <X data-testid="deco" />
        <X data-testid="named" aria-label="关闭" />
      </>,
    )
    expect(m.svg('deco').getAttribute('aria-hidden')).toBe('true')
    expect(m.svg('named').getAttribute('aria-hidden')).toBeNull()
    expect(m.svg('named').getAttribute('aria-label')).toBe('关闭')
    await m.unmount()
  })
})

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
