#!/usr/bin/env node
/**
 * Tailwind 扫描面的两向门禁（issue #322）。读 `pnpm build` 刚写出的产物 CSS，
 * **不重跑扫描器**——判的是「产物里到底有什么」，不是「配置写了什么」。
 *
 * 扫描面由 `src/index.css` 第一行的 `source('../src')` 声明：只有 `web/src` 被扫。
 * 声明写错的两种失败方向各钉一条判据：
 *
 *   * **正向**（扫描面收窄到把真实用法漏掉）：`web/src` 里几处真实用法——字符串字面量、
 *     模板字符串、`cn()` 参数、变体、任意值、主题 token 工具类——生成的规则**必须**在
 *     产物里。`source('../nowhere')` 构建时就抛；`source('../src/lib')` 这种指向了一个
 *     存在的子目录的错误是静默的，只有这条抓得到。
 *   * **反向**（扫描面又漫出去了）：`web/AGENTS.md` 常驻一个产物里不存在的工具类名
 *     （`CANARY`），它在扫描面之外——去掉 `source(…)` 之后 Tailwind 会扫整个 `web/`、
 *     连 `.md` 一起扫，这条规则就会凭空出现在产物里。**文档里那处金丝雀被删掉时这里
 *     同样红**：少了输入的反向判据是恒真的，不算通过。
 *
 * 变异清单（改完扫描面声明或换金丝雀之后都要重跑一遍，退出码为准）：
 *   ① `index.css` 去掉 `source('../src')` → 反向红（金丝雀规则出现）
 *   ② `source('../src/lib')` → 正向红（组件里的真实用法被漏掉）
 *   ③ `source('../nowhere')` → vite build 自己红（目录不存在）
 *   ④ 把 AGENTS.md 里的金丝雀删掉 → 这里红（输入缺失）
 *   ⑤ 复原 → 绿
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DIST_ASSETS = path.join(WEB, 'dist', 'assets')
const PROSE_OUTSIDE_SCAN = path.join(WEB, 'AGENTS.md')

/**
 * 产物里不许出现的工具类。它是 Tailwind 默认主题里的真实工具类（`letter-spacing`
 * 的最宽一档），所以一旦被扫到就一定会生成规则——拿一个 Tailwind 根本不认识的名字
 * 当金丝雀，反向判据永远绿。产品里没人用它（`foundation.test.ts` 禁手拼大写小标题，
 * 宽字距的主要用途就是那个）；哪天真要用，换一个并把 AGENTS.md 里那处一起换。
 */
const CANARY = 'tracking-widest'

/** `web/src` 里的真实用法，各代表一种 Tailwind 要从文本里认出来的形态 */
const REAL_USAGES = [
  ['pointer-events-none', 'src/canvas/OverlaySvg.tsx（className 字面量）'],
  ['min-h-12', 'src/components/settings/CodingAgentsSection.tsx（模板字符串）'],
  ['hover:text-sel', 'src/playground/PlaygroundApp.tsx（变体）'],
  ['text-[19px]', 'src/playground/components/PlaygroundLanding.tsx（任意值）'],
  ['data-[state=open]:animate-pop-in', 'src/components/ui/Popover.tsx（data 变体 + 自定义动画）'],
  ['focus-visible:focus-ring', 'src/index.css 的 @utility + 变体'],
  ['shadow-pop', '主题 token 工具类（--shadow-pop）'],
  ['bg-surface-2', '主题颜色'],
  ['animate-pulse', 'src/canvas/PanelView.tsx'],
  ['w-max', 'src/canvas/WorkspaceContextBar.tsx'],
  ['sr-only', 'src/playground/components/ExampleCard.tsx'],
]

/** Tailwind 写进选择器时对类名的转义（`.hover\:text-sel`、`.text-\[19px\]`） */
const escapeClass = (cls) => cls.replace(/[[\]:=./%()#,!]/g, (c) => `\\${c}`)
const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** 产物里有没有 `.<类名>` 这条规则：类名后面必须是选择器的边界，`.min-h-12` 不算 `.min-h-120` */
function ruleCount(css, cls) {
  const re = new RegExp(`\\.${escapeRegex(escapeClass(cls))}(?=[{,:[>\\s])`, 'g')
  return (css.match(re) ?? []).length
}

function main() {
  const failures = []

  const cssFiles = fs.existsSync(DIST_ASSETS)
    ? fs.readdirSync(DIST_ASSETS).filter((f) => /^index-.*\.css$/.test(f))
    : []
  if (cssFiles.length !== 1) {
    console.error(
      `tailwind-scan-check: 在 ${DIST_ASSETS} 里应有且只有一个 index-*.css，实际 ${cssFiles.length} 个——先 vite build`,
    )
    process.exit(2)
  }
  const cssPath = path.join(DIST_ASSETS, cssFiles[0])
  const css = fs.readFileSync(cssPath, 'utf8')

  // 正向：真实用法一个都不许漏
  let present = 0
  for (const [cls, where] of REAL_USAGES) {
    if (ruleCount(css, cls) > 0) present += 1
    else failures.push(`正向：产物里没有 .${cls} 的规则（真实用法在 ${where}）——扫描面把它漏掉了`)
  }

  // 反向：扫描面之外的散文里那个金丝雀必须**在**（输入存在）且**不进**产物
  const prose = fs.readFileSync(PROSE_OUTSIDE_SCAN, 'utf8')
  if (!prose.includes(CANARY)) {
    failures.push(`反向：${path.relative(WEB, PROSE_OUTSIDE_SCAN)} 里找不到金丝雀 \`${CANARY}\`——少了这个输入，反向判据是恒真的`)
  }
  const canaryRules = ruleCount(css, CANARY)
  if (canaryRules > 0) {
    failures.push(
      `反向：产物里出现了 .${CANARY}（${canaryRules} 条）。它只写在扫描面之外的散文里——` +
        `src/index.css 的 source('../src') 是不是没了？`,
    )
  }

  const rules = (css.match(/\{/g) ?? []).length
  console.log(
    `tailwind-scan-check: ${path.relative(WEB, cssPath)} ${css.length} 字节、${rules} 个规则块；` +
      `正向 ${present}/${REAL_USAGES.length} 在、金丝雀 .${CANARY} ${canaryRules} 条`,
  )
  if (failures.length) {
    for (const f of failures) console.error(`  ✗ ${f}`)
    process.exit(1)
  }
}

main()
