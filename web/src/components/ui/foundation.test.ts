/**
 * Design Constitution 的门禁（docs/ux/DESIGN_CONSTITUTION.md）。
 *
 * 2026-09-11 收敛之前，圆角、字号、hover 透明度、动效时长在页面里各写各的：
 * `rounded-[3px]` 18 处、`text-[11px]` 52 处、`hover:bg-ink/[.0xx]` 九种透明度。
 * token 定好之后要有一道门，否则下一次「顺手写个 `rounded-[5px]`」几分钟就把
 * 阶梯打回随机。判据是**类名字面量**：改动的是「写法」，没有语义可 AST。
 *
 * 读文件走 `import.meta.glob('?raw')`，与 `nativeSelect.test.ts` 同一手法同一理由
 * （src 归 tsconfig.app.json 管，不引 node:fs）。注释先剥掉——解释「为什么不用
 * `text-[11px]`」的那句话不该被自己咬到。
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

const stripComments = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[\s;{}()])\/\/.*$/gm, '$1')

interface Rule {
  name: string
  pattern: RegExp
  fix: string
  /** 一条能被抓住的样例与一条不该被抓住的样例：证明判据是活的、且不过宽 */
  catches: string
  spares: string
  /**
   * 按文件豁免，带个数与理由。个数写死：豁免的是「这几处」，不是「这个文件」，
   * 文件里多写一处照样红。
   */
  exempt?: Record<string, { count: number; why: string }>
  /** 只判这些路径（不给 = 全部 web/src） */
  only?: RegExp
}

// 逐页阶段迁移的豁免表（LATER_PHASE）已删空删掉：shadow-card 的素材卡、问题卡、左轨激活态在左栏阶段迁完，
// 版本对话框的缩略图框在对话框阶段迁到 Card（2026-10-07）。

const RULES: Rule[] = [
  {
    name: '圆角只来自 token（xs / sm / md / lg / panel / full），没有任意值',
    pattern: /\brounded(-[trbl]|-[trbl][lr])?-\[/,
    fix: 'rounded-xs(4) / rounded-sm(6) / rounded-md(8) / rounded-lg(12) / rounded-panel(16) / rounded-full',
    catches: '<div className="rounded-[5px] rounded-t-[var(--r)]" />',
    spares: '<div className="rounded-sm rounded-t-xs rounded-full rounded-panel" />',
  },
  {
    name: '圆角不写内联样式（borderRadius）：同一件事只有 token 一条路',
    pattern: /\bborderRadius\s*:/,
    fix: 'className="rounded-md"（或 rounded-lg / rounded-panel）',
    catches: '<div style={{ borderRadius: 6 }} />',
    spares: '<div className="rounded-md" style={{ width: 6 }} />',
  },
  {
    name: 'Tailwind 自带的 xl 以上圆角已被清掉，写了也不生效',
    pattern: /\brounded(-[trbl]|-[trbl][lr])?-(xl|2xl|3xl|4xl)\b/,
    fix: '对话框 / 工作面板 / 命令面板用 rounded-panel(16)，再大的圆角不在体系里',
    catches: '<div className="rounded-xl" />',
    spares: '<div className="rounded-lg" />',
  },
  {
    name: '字号只有 xs(11) / sm(12) / base(13) / lg(14) / xl(15) 五档，没有像素字面量',
    pattern: /\btext-\[\d+px\]/,
    fix: 'text-xs / text-sm / text-base / text-lg / text-xl，或六个 type-* 角色',
    catches: '<p className="text-[11px]" />',
    spares: '<p className="text-xs type-meta" />',
  },
  {
    name: '不用 :empty 藏整行——<input> 也是空元素，会把它所在的那一行一起藏掉（2026-09-15 审计 D01）',
    pattern: /has-\[[^\]]*:empty\][^\s"']*:hidden/,
    fix: '没话说的行由组件自己 return null，不靠选择器',
    catches: '<div className="has-[>div>:only-child:empty]:hidden" />',
    spares: '<div className="empty:hidden" />',
  },
  {
    name: '交互面用 surface-hover / surface-active / selected 三档 token，不写 ink 透明度',
    pattern: /\b(hover|active|focus-visible|data-\[[^\]]+\]):bg-ink\/\[/,
    fix: 'hover:bg-surface-hover / active:bg-surface-active / bg-selected',
    catches: '<div className="hover:bg-ink/[.055]" />',
    spares: '<div className="hover:bg-surface-hover bg-ink/[.72] hover:bg-ink/90" />',
  },
  {
    name: '动效时长只来自 token（duration-fast / base / slow / exit）',
    pattern: /\bduration-\d+\b/,
    fix: 'duration-fast(120) / duration-base(180) / duration-slow(240)',
    catches: '<div className="transition duration-150" />',
    spares: '<div className="transition duration-fast" />',
  },
  {
    name: '分区小标题是 type-section，不手拼大写 + 字距',
    pattern: /\buppercase tracking-/,
    fix: 'className="type-section"',
    catches: '<h3 className="text-xs uppercase tracking-[.06em]" />',
    spares: '<h3 className="type-section" />',
  },
  {
    name: '卡片的抬升只有 ui/Card 一处：shadow-card 不在页面里手写（2026-10-07 设计审计 §5）',
    pattern: /\bshadow-card\b/,
    fix: 'import { Card } from "@/components/ui/Card"（appearance / padding / interactive / selected）',
    catches: '<div className="rounded-md bg-surface p-3 shadow-card" />',
    spares: '<Card padding="md" className="shadow-pop" />',
    exempt: {
      '/src/components/ui/Card.tsx': { count: 1, why: '它就是那一处实现（raised）' },
      '/src/App.tsx': {
        count: 1,
        why: '工作面板（data-work-panel，宪法第二十五节）不是卡，是一块面板；它的抬升与卡同一档是拍板过的，常驻豁免',
      },
    },
  },
  {
    name: '投影只有 shadow-pop（浮层专用），没有 Tailwind 预设投影',
    pattern: /\bshadow(-sm|-md|-lg|-xl|-2xl)\b/,
    fix: '浮层 shadow-pop；常驻表面不用投影',
    catches: '<div className="shadow-sm" />',
    spares: '<div className="shadow-pop shadow-[inset_0_1px_0_0_var(--color-accent)]" />',
  },
  {
    name: '600 字重只在原语里：选中态（页签 / 分段 / 列表行）、表单分区标题、危险浅底胶囊（2026-10-07 设计审计 §2）',
    pattern: /\bfont-semibold\b/,
    fix: '页面里只有 400 / 500；标题走 type-title / type-heading / type-display（600 在角色里），选中行走 listRowClass',
    catches: '<span className="font-semibold" />',
    spares: '<span className="font-medium type-title" />',
    exempt: {
      '/src/components/ui/tabClass.ts': { count: 1, why: '选中的页签：600 + ink，与未选中的 400 + ink-3 拉开两档' },
      '/src/components/ui/Segmented.tsx': { count: 1, why: '选中的分段项：白色 thumb 上 600 + ink' },
      '/src/components/inspector/controls/OptionGrid.tsx': { count: 1, why: '样张网格与 Segmented 同一副皮（2026-10-07 §9.2）：选中格浮起 + 600' },
      '/src/components/ui/listRow.ts': { count: 1, why: '选中的列表 / 树行：selected 底 + 600（2026-10-07 §10.3）' },
      '/src/components/ui/FormSection.tsx': { count: 1, why: '表单分区标题 13 / 600（没有对应的 type 角色，只此一处）' },
      '/src/components/ui/buttonClass.ts': { count: 1, why: '对话框页脚的危险浅底胶囊（danger-tinted）：600' },
    },
  },
  {
    name: '复选框只有 ui/Checkbox 一种',
    pattern: /<input[^>]*type="checkbox"/,
    fix: 'import { Checkbox } from "@/components/ui/Checkbox"',
    catches: '<input type="checkbox" checked />',
    spares: '<Checkbox checked />',
    exempt: {
      '/src/components/ui/Checkbox.tsx': { count: 1, why: '它就是那一处实现' },
    },
  },
  {
    name: '滑动开关只有 ui/Toggle 一种',
    pattern: /role="switch"/,
    fix: 'import { Toggle } from "@/components/ui/Toggle"',
    catches: '<button role="switch" aria-checked />',
    spares: '<Toggle checked aria-label="x" />',
    exempt: {
      '/src/components/ui/Toggle.tsx': { count: 1, why: '它就是那一处实现' },
      '/src/components/inspector/controls/TickAndSpineDiagram.tsx': {
        count: 2,
        why: '刻度 / 边框示意图里四条边的开关是画在图上的位置块，语义是 switch、外形是图的一部分（网格 X / Y 已改回 Toggle）',
      },
    },
  },
  {
    name: '分段选择器只有 ui/Segmented 一种：role="radio" 不在页面里手拼',
    pattern: /role="radio"/,
    fix: 'import { Segmented } from "@/components/ui/Segmented"（空间型选择器走 OptionGrid）',
    catches: '<button role="radio" aria-checked />',
    spares: '<Segmented value={v} items={items} onChange={set} />',
    exempt: {
      '/src/components/ui/Segmented.tsx': {
        count: 3,
        why: '它就是那一处实现（另两处是键盘漫游里的 closest 选择器与滑动选中底量测用的选择器字面量）',
      },
      '/src/components/inspector/controls/OptionGrid.tsx': {
        count: 1,
        why: '视觉选择器的二维网格：radiogroup 语义 + 方向键漫游，与 Segmented 同一套约定',
      },
      '/src/components/inspector/controls/ColormapPicker.tsx': {
        count: 1,
        why: '色图样张网格，OptionGrid 的同族（样张要自己画）',
      },
      '/src/components/inspector/controls/LegendPositionPicker.tsx': {
        count: 4,
        why: '九宫格 + 外侧带的空间型 radio（含一处 querySelector 字面量），自带 roving tabindex',
      },
    },
  },
  {
    name: '下拉 / 弹层触发器来自 ui/（Select / Popover / Menu），不手写 aria-haspopup',
    pattern: /aria-haspopup=/,
    fix: 'Select（取值）/ Popover + OptionGrid（带样张的取值）/ Menu（命令）',
    catches: '<button aria-haspopup="listbox" aria-expanded />',
    spares: '<Select value={v} options={opts} onChange={set} />',
  },
  {
    name: '禁用态只有一档：opacity-40（宪法第五节）',
    pattern: /\bdisabled:opacity-(?!40\b)\d+/,
    fix: 'disabled:cursor-not-allowed disabled:opacity-40',
    catches: '<button className="disabled:opacity-35" />',
    spares: '<button className="disabled:cursor-not-allowed disabled:opacity-40" />',
  },
  {
    // 上一条只抓 `disabled:` 前缀。浮动栏的「水平等距」不用原生 disabled（原生 disabled
    // 不发 pointer 事件，tooltip 里那句「需要三个对象」会一起消失），走 aria-disabled +
    // 手写 opacity——那条路上的 `opacity-35` 三个月没人发现（2026-09-15 打磨 F5）
    name: '禁用态只有一档：aria-disabled 那条路的 opacity 也是 40（宪法第五节）',
    pattern: /cursor-not-allowed (?:[a-z-]+ )*opacity-(?!40\b)\d+|opacity-(?!40\b)\d+ (?:[a-z-]+ )*cursor-not-allowed/,
    fix: "cn(blocked && 'cursor-not-allowed opacity-40')",
    catches: "<Button className={cn(blocked && 'cursor-not-allowed opacity-35')} />",
    spares: "<Button className={cn(blocked && 'cursor-not-allowed opacity-40')} />",
  },
  {
    name: '禁用态不用 pointer-events-none（会把 title / tooltip 一起吞掉）',
    pattern: /(disabled:|disabled && ['"])pointer-events-none/,
    fix: '原生 disabled 已经挡住点击；光标用 cursor-not-allowed，原因走 title',
    catches: '<div className={cn(disabled && \'pointer-events-none opacity-40\')} />',
    spares: '<div className={cn(disabled && \'cursor-not-allowed opacity-40\')} />',
  },
  {
    name: '焦点环不透明：不写 ring-accent/N',
    pattern: /\bring-accent\/\d+/,
    fix: 'focus-visible:focus-ring 或 ring-2 ring-accent（不透明；透明版对底色不到 2:1）',
    catches: '<span className="peer-focus-visible:ring-2 peer-focus-visible:ring-accent/50" />',
    spares: '<span className="peer-focus-visible:ring-2 peer-focus-visible:ring-accent" />',
  },
  {
    name: '加载只有四种写法：sweep / 静态骨架 / text-shimmer / 转圈——没有 Tailwind 的 animate-pulse（宪法第七节）',
    pattern: /\banimate-pulse\b/,
    fix: '不定进度 ProgressBar（animate-sweep）；骨架静态 bg-surface-hover + opacity-65；进行中的字 text-shimmer；按钮 / 行内 LoaderCircle animate-spin；一次性「看这里」animate-attention',
    catches: '<span className="h-2 w-2 animate-pulse rounded-full" />',
    spares: '<span className="animate-sweep animate-spin text-shimmer animate-attention" />',
  },
  {
    name: '层级只来自 z-index token（z-sticky / z-canvas-chrome / z-drawer / z-overlay / z-dialog / z-popover / z-tooltip / z-toast / z-onboarding），没有数字',
    pattern: /(?:^|[\s'"`:])-?z-(?:\d+|\[)/,
    fix: 'index.css 的 --z-* 表里挑一档；新的一层先在那张表里加 token',
    catches: '<div className="fixed z-50" /> <div className="z-[59]" />',
    spares: '<div className="fixed z-dialog sticky z-sticky" />',
  },
  {
    name: '内联样式的 zIndex 也只来自 token',
    pattern: /\bzIndex\s*:\s*-?\d/,
    fix: "zIndex: 'var(--z-onboarding)'",
    catches: 'const style = { zIndex: 60 }',
    spares: "const style = { zIndex: 'var(--z-onboarding)' }",
  },
  {
    name: '光标一律箭头：没有手形光标类（宪法第五节；可拖的卡用抓手，不在此列）',
    pattern: /\bcursor-pointer\b/,
    fix: '删掉它；index.css 的 base 层已把 button / summary / label / 复选单选兜成箭头，手形只给真正的 <a>',
    catches: '<button className="cursor-pointer" />',
    spares: '<div className="cursor-grab active:cursor-grabbing cursor-default" />',
  },
  {
    name: '原语里没有写死的白（bg-white / text-white）：界面外观走 surface token，暗色只换值',
    pattern: /\b(?:bg|text)-white\b/,
    only: /^\/src\/components\/ui\//,
    fix: 'bg-surface / text-surface（「纸」——图与页面内容——才是真白，不在 ui/ 里）',
    catches: '<span className="bg-ink text-white" />',
    spares: '<span className="bg-ink text-surface" />',
  },
  {
    name: '按钮层级是 primary / secondary / ghost / danger，没有 outline',
    pattern: /variant=["']outline["']/,
    fix: 'variant="secondary"',
    catches: '<Button variant="outline" />',
    spares: '<Button variant="secondary" />',
  },
]

const sources = () =>
  Object.entries(SOURCES).filter(
    ([path]) => !path.endsWith('.test.ts') && !path.endsWith('.test.tsx'),
  )

describe('Design Constitution：token 之外没有字面量', () => {
  for (const rule of RULES) {
    it(rule.name, () => {
      const offenders: string[] = []
      const exemptSeen: Record<string, number> = {}
      for (const [path, raw] of sources()) {
        if (rule.only && !rule.only.test(path)) continue
        const src = stripComments(raw)
        const hits = src.match(new RegExp(rule.pattern.source, 'g'))?.length ?? 0
        if (hits === 0) continue
        const ex = rule.exempt?.[path]
        if (ex) {
          exemptSeen[path] = hits
          if (hits > ex.count) offenders.push(`${path}（豁免 ${ex.count} 处，实际 ${hits} 处）`)
          continue
        }
        offenders.push(`${path}（${hits} 处）`)
      }
      expect(offenders, `改法：${rule.fix}`).toEqual([])
      // 豁免表里的每一条都还在用：没人用的豁免是过期的盲区，该删
      for (const [path, ex] of Object.entries(rule.exempt ?? {})) {
        expect(exemptSeen[path], `${path} 的豁免已经没人用了，删掉它：${ex.why}`).toBe(ex.count)
      }
    })
  }

  it('自检：每条判据抓得住反例、放得过正例（不是空门禁）', () => {
    for (const rule of RULES) {
      expect(rule.pattern.test(stripComments(rule.catches)), rule.name).toBe(true)
      expect(rule.pattern.test(stripComments(rule.spares)), rule.name).toBe(false)
      // 注释里提到禁写法不算：那正是在解释为什么不用它
      expect(rule.pattern.test(stripComments(`// 别写 ${rule.catches}`)), rule.name).toBe(false)
    }
  })
})
