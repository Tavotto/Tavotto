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
}

const RULES: Rule[] = [
  {
    name: '圆角只有 xs / sm / md / lg / full 五档，没有像素字面量',
    pattern: /\brounded(-[trbl]|-[trbl][lr])?-\[\d+px\]/,
    fix: 'rounded-xs(3) / rounded-sm(6) / rounded-md(8) / rounded-lg(12)',
    catches: '<div className="rounded-[5px]" />',
    spares: '<div className="rounded-sm rounded-t-xs rounded-full" />',
  },
  {
    name: 'Tailwind 自带的 xl 以上圆角已被清掉，写了也不生效',
    pattern: /\brounded(-[trbl]|-[trbl][lr])?-(xl|2xl|3xl|4xl)\b/,
    fix: '对话框用 rounded-lg(12)，再大的圆角不在体系里',
    catches: '<div className="rounded-xl" />',
    spares: '<div className="rounded-lg" />',
  },
  {
    name: '字号只有 xs(11) / sm(12) / base(13) / lg(14) 四档，没有像素字面量',
    pattern: /\btext-\[\d+px\]/,
    fix: 'text-xs / text-sm / text-base / text-lg，或六个 type-* 角色',
    catches: '<p className="text-[11px]" />',
    spares: '<p className="text-xs type-meta" />',
    exempt: {
      '/src/components/left/LeftRail.tsx': {
        count: 1,
        why: '轨道图标角上的问题计数：9px 是 28px 图标钮上唯一放得下两位数的字号',
      },
      '/src/playground/components/PlaygroundLanding.tsx': {
        count: 1,
        why: '网站 /try 的首屏标题（19px）：营销页的展示级字号，不是产品界面',
      },
      '/src/playground/components/PlaygroundLoading.tsx': {
        count: 1,
        why: '/try 的加载页标题（15px）：同上，营销页',
      },
      '/src/playground/components/ExampleCodeSheet.tsx': {
        count: 1,
        why: '/try 代码抽屉的文件名（15px）：同上，营销页',
      },
    },
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
    name: '投影只有 shadow-pop（浮层专用），没有 Tailwind 预设投影',
    pattern: /\bshadow(-sm|-md|-lg|-xl|-2xl)\b/,
    fix: '浮层 shadow-pop；常驻表面不用投影',
    catches: '<div className="shadow-sm" />',
    spares: '<div className="shadow-pop shadow-[inset_0_1px_0_0_var(--color-accent)]" />',
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
        count: 3,
        why: '刻度 / 边框示意图里的开关是画在图上的位置块，语义是 switch、外形是图的一部分',
      },
    },
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
