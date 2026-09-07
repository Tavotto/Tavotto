/**
 * 门禁：e2e 里不许拿**活动区的 role** 去指代一个具体对象。
 *
 * 这条规则是 PR #296 在合并队列上那次真回归的结构化留档。当时
 * `e2e/twin-axes-pick.spec.ts` 用
 *
 *     document.querySelector('[role="status"]')
 *
 * 指代「状态播报区」。而 `role="status"` 全产品十几个产出点，这句话真正的含义
 * 是「文档里排在最前的那个 status」。UI 审计 T06 给快速编辑浮动条加了一行同样
 * 带 `role="status"` 的常驻说明，它在 DOM 里排在播报区前面——判据的主语就这么
 * 从「应用刚播报了什么」静悄悄变成了「那行说明写着什么」，而产品行为完好。
 *
 * **为什么这条规则可以是绝对的**：`status / alert / log / marquee / timer` 都是
 * 活动区。活动区天生是复数且与 DOM 顺序相关（谁先出现谁被 `querySelector`
 * 取到），所以「the status region」这个说法本身就不成立。它不像 `role=dialog`
 * ——那个还有「把 axe 扫描收进对话框」这类正当的**限定**用法，因此那一族没法
 * 用源码结构判死，硬加只会逼出一张越来越长的豁免表（空门禁比没有门禁更坏）。
 * 这条规则立起来时**豁免为零**，那才说明它是真的。
 *
 * 正确写法：给那块区域一个稳定 `data-*` 并认它。播报区是
 * `data-status-live`（`components/StatusBar.tsx`），清单在 `web/AGENTS.md`。
 *
 * 只管 `e2e/`：jsdom 单测里一次只挂载一个组件，`document` 就是那次渲染的根，
 * 主语唯一（`AgentDetailView.test.tsx` / `CodexIntegrationPanel.test.tsx` 取
 * `[role="alert"]` 是对的）。把它们一起点名，就是在制造那张豁免表。
 *
 * 用 AST 走字符串字面量，不用正则：本文件与 `twin-axes-pick.spec.ts` 的注释里
 * 都写着 `[role="status"]` 这几个字，正则会把讲解这条规则的文字判成违反它。
 */
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const E2E_SOURCES = import.meta.glob('/e2e/**/*.ts', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

/** 活动区的 role：命名其中任何一个都等于在赌 DOM 顺序 */
const LIVE_ROLES = ['status', 'alert', 'log', 'marquee', 'timer']

/** `[role=status]` / `[role="status"]` / `[role='status']`，且不吃掉 alertdialog */
const LIVE_ROLE_SELECTOR = new RegExp(
  String.raw`\[\s*role\s*=\s*['"]?(${LIVE_ROLES.join('|')})['"]?\s*[\]~^$*|]`,
)

/** 源文件里所有字符串字面量（含模板串的静态段）——注释与标识符都不算 */
function stringLiterals(source: string, path: string): string[] {
  const sf = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true)
  const out: string[] = []
  const walk = (n: ts.Node) => {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) out.push(n.text)
    else if (ts.isTemplateHead(n) || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) out.push(n.text)
    ts.forEachChild(n, walk)
  }
  walk(sf)
  return out
}

describe('e2e 选择器：活动区不能用 role 指代', () => {
  it('扫到了 e2e 源码（判据本身得先是活的）', () => {
    expect(Object.keys(E2E_SOURCES).length).toBeGreaterThan(10)
  })

  it('没有任何 e2e 用 [role=status|alert|log|marquee|timer] 当选择器', () => {
    const offenders: string[] = []
    for (const [path, src] of Object.entries(E2E_SOURCES)) {
      for (const lit of stringLiterals(src, path)) {
        if (LIVE_ROLE_SELECTOR.test(lit)) offenders.push(`${path}: ${lit}`)
      }
    }
    expect(
      offenders,
      '活动区天生是复数，用 role 指代它等于赌「以后没人在它前面插一个同类」。\n' +
        '改成认那块区域的稳定 data-*（播报区是 data-status-live，见 web/AGENTS.md）。',
    ).toEqual([])
  })
})
