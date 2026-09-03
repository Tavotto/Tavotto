/**
 * 自动化可访问性门禁（审计 P1-09）。
 *
 * 三层：axe 扫描（critical/serious 必须为 0——这是发布门禁，不是建议）、
 * 对话框焦点纪律（trap + Escape 关闭后焦点恢复）、键盘可达性底线。
 *
 * axe 那一层**不只看 violations**（issue #130）：`incomplete` 是「axe 查不了」，
 * 把它当通过就是把「没查到」和「查不了」混成一件事。这里两条一起守——每一类
 * 「查不了」必须在 `INCOMPLETE_COVERED_ELSEWHERE` 里有去处，而对比度另有一条
 * 不依赖 axe 的自算判据。
 * 完整的「纯键盘走完核心流程」在 issue #37 里继续扩：这里守住的是
 * 「不倒退」的底线，先有门禁再逐步抬高。
 *
 * **语言无关写法**：本 spec 同时跑在 zh-CN（chromium / webkit）与 en-US
 * （chromium-en）三个 project 下，选择器一律 role + 双语正则，不写死文案。
 */
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import os from 'node:os'
import path from 'node:path'
import { expect, test, writeRuntimeNamedProject } from './fixtures'
import { lowContrastNodes } from './contrast'

/**
 * **axe 的 `incomplete` 不是「通过」，是「axe 查不了」**（issue #130）。
 *
 * 只断言 `results.violations` 的门禁把两件事混成了一件：「查过了，没问题」和
 * 「根本没查」。实证：我们「整行可点」的写法（`absolute inset-0` 的按钮盖在行
 * 内容上）让 axe 算不出背景色，于是 color-contrast 整片进 `incomplete`；把
 * `--color-warn` 改成明显不达标的 `#e8c98f`，只看 violations 的检查照样绿。
 *
 * 所以每条用例都要把 `incomplete` 交代清楚。两条纪律：
 *
 * 1. **豁免绑在场景上，不是绑在规则 id 上。** 一张全局表会让「项目选择器上冒出
 *    aria-hidden-focus」也被静默接受，理由却写着「由导出对话框那条用例的 focus
 *    trap 覆盖」——那是在更粗的粒度上重造同一个失灵。
 * 2. **每条豁免都得把 axe 没做完的那件事自己做一遍**，并逐节点核对。只写一句
 *    「由别处覆盖」而不验，等于换个地方写「查不了 = 通过」。
 *
 * （两条都是 Codex 在 PR #167 上指出的，成立。）
 */
interface IncompleteAllowance {
  rule: string
  /** axe 查不了，但这件事由谁覆盖 */
  why: string
  /** 逐节点核对：拿到 axe 报的 target 选择器，自己去页面里把那件事查一遍 */
  verify: (page: Page, targets: string[]) => Promise<void>
}

/**
 * 模态对话框打开时，Radix 把背景整片标成 `aria-hidden` 并插入焦点哨兵；axe 判不出
 * 「它同时也进不去焦点」，于是整片进 incomplete。
 *
 * 核对的是**语义**而不是选择器长相：每个节点要么是 Radix 的焦点哨兵，要么处在
 * 一棵 `aria-hidden` 的子树里，且都在对话框**之外**。而「焦点确实困在对话框里」
 * 由同一条用例紧接着那圈 Tab 断言覆盖。
 */
const dialogBackgroundIsInert: IncompleteAllowance = {
  rule: 'aria-hidden-focus',
  why: '对话框背景整片 aria-hidden；焦点进不去这一点由同一条用例的 focus trap 断言覆盖',
  verify: async (page, targets) => {
    const bad = await page.evaluate((sels) => {
      const out: string[] = []
      for (const sel of sels) {
        let el: Element | null = null
        try {
          el = document.querySelector(sel)
        } catch {
          out.push(`${sel}（选择器解析不了）`)
          continue
        }
        if (!el) continue // 扫描之后已经消失：不构成放行理由，也不构成失败
        const guard = el.hasAttribute('data-radix-focus-guard')
        const hidden = el.closest('[aria-hidden="true"], [data-aria-hidden="true"]') != null
        const inDialog = el.closest('[role="dialog"]') != null
        if (inDialog || (!guard && !hidden)) {
          out.push(`${sel}（guard=${guard} hidden=${hidden} inDialog=${inDialog}）`)
        }
      }
      return out
    }, targets)
    expect(
      bad,
      'aria-hidden-focus 的 incomplete 里混进了不属于「对话框背景已 inert」这一类的节点',
    ).toEqual([])
  },
}

/**
 * 被覆盖层遮住的文字，axe 算不出背景色 → 整片进 incomplete。
 *
 * 这**正是自算对比度那条判据存在的理由**：它不依赖 axe 能不能算出背景色，扫的是
 * 整个 root，且把 CSS `opacity` 计入有效 alpha。所以这里核对的是「axe 报的每一个
 * 节点，我们自己那把尺子确实量得到」——量不到的要红，不能靠规则 id 一刀放行。
 */
const contrastCoveredByOurOwnRuler: IncompleteAllowance = {
  rule: 'color-contrast',
  why: '覆盖层下 axe 算不出背景色；由本文件的自算对比度判据逐节点覆盖',
  verify: async (page, targets) => {
    const unreachable = await page.evaluate((sels) => {
      const out: string[] = []
      for (const sel of sels) {
        let el: Element | null = null
        try {
          el = document.querySelector(sel)
        } catch {
          out.push(`${sel}（选择器解析不了）`)
          continue
        }
        if (!el) continue
        // 自算判据只看「自己直接持有文字」的元素；禁用态按 WCAG 1.4.3 本就不在
        // 范围内。两者都不是就说明它落在我们的尺子之外，这条豁免对它不成立。
        const direct = [...el.childNodes]
          .filter((n) => n.nodeType === 3)
          .map((n) => n.textContent ?? '')
          .join('')
          .trim()
        const disabled = el.closest('[disabled], [aria-disabled="true"], fieldset[disabled]')
        if (!direct && !disabled) out.push(`${sel}（没有直接文字，自算判据扫不到它）`)
      }
      return out
    }, targets)
    expect(
      unreachable,
      'color-contrast 的 incomplete 里有自算判据也覆盖不到的节点——不能按规则 id 放行',
    ).toEqual([])
  },
}

/**
 * axe 判不了标题层级时（跨 landmark、`role="heading"` 的自定义标题）也会进
 * incomplete。同样不按 id 放行：这里**自己把那件事查一遍**——文档顺序上标题层级
 * 不许一次跳超过一级（`h2` 之后直接 `h4` 是屏幕阅读器用户真会迷路的那种）。
 */
const headingOrderCheckedByOurselves: IncompleteAllowance = {
  rule: 'heading-order',
  why: 'axe 判不了跨 landmark 的标题顺序；这里自己按文档顺序核对不跳级',
  verify: async (page) => {
    const jumps = await page.evaluate(() => {
      const levelOf = (h: Element): number =>
        Number(h.getAttribute('aria-level') ?? h.tagName.slice(1))
      const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')]
        .filter((h) => (h as HTMLElement).getClientRects().length > 0)
      const out: string[] = []
      let prev = 0
      for (const h of heads) {
        const lvl = levelOf(h)
        if (prev && lvl > prev + 1) {
          out.push(`${(h.textContent ?? '').trim().slice(0, 16)}：h${prev} → h${lvl}`)
        }
        prev = lvl
      }
      return out
    })
    expect(jumps, '标题层级跳级了（axe 判不了这一条，所以这里自己查）').toEqual([])
  },
}

interface AxeReport {
  violations: unknown[]
  incomplete: { id: string; impact: string | null | undefined; targets: string[] }[]
}

/** 扫描一次，拿回 critical/serious 违规与**全部** incomplete（含每个节点的 target）。
 *  扫描前把动效关掉：对话框/抽屉的进出场动画进行到一半时，axe 对
 *  颜色对比的取样会撞上过渡态，产出不可复现的假阳性；应用本来就支持
 *  prefers-reduced-motion，这也是它的一次真实行使。 */
async function axeReport(page: Page): Promise<AxeReport> {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const results = await new AxeBuilder({ page }).analyze()
  return {
    violations: results.violations
      .filter((v) => v.impact === 'critical' || v.impact === 'serious')
      .map((v) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.slice(0, 5).map((n) => ({
          target: n.target.join(' '),
          why: n.failureSummary?.split('\n').slice(0, 3).join(' '),
        })),
      })),
    incomplete: results.incomplete.map((v) => ({
      id: v.id,
      impact: v.impact,
      targets: v.nodes.map((n) => n.target.join(' ')),
    })),
  }
}

/**
 * 一处界面的完整可访问性判据：
 *   ① axe 没有 critical/serious **违规**；
 *   ② axe 说「查不了」的，**这个场景**必须逐条声明去处，且每条都把那件事自己
 *      查一遍、逐节点核对；
 *   ③ **自算对比度**没有不达标的文字——不依赖 axe 能不能算出背景色，并且把 CSS
 *      `opacity` 计入有效 alpha（禁用态按 WCAG 1.4.3 排除）。
 *
 * `allow` 默认是空的：一处界面**本来就该**没有「查不了」。要放行就得写清楚并验。
 */
async function expectAccessible(
  page: Page,
  { allow = [] as IncompleteAllowance[], root = 'body' } = {},
) {
  const report = await axeReport(page)
  expect(report.violations).toEqual([])

  const byRule = new Map(allow.map((a) => [a.rule, a]))
  const unexplained = report.incomplete
    .filter((v) => !byRule.has(v.id))
    .map((v) => ({ id: v.id, impact: v.impact, n: v.targets.length }))
  expect(
    unexplained,
    '这个场景冒出了没有交代的「axe 查不了」——先定性它由谁覆盖，再在该用例上声明并验',
  ).toEqual([])

  for (const v of report.incomplete) {
    await byRule.get(v.id)!.verify(page, v.targets)
  }
  // 刻意不断言「声明了就必须出现」：同一处界面的 incomplete 会随抽屉开合、卡片
  // 数量、浏览器而变（实测工作台在 chromium 上有 color-contrast、chromium-en 上
  // 没有）。每条豁免都带着真核对，用不上并不构成放行。

  expect(
    await lowContrastNodes(page, root),
    '自算 WCAG 对比度不达标（axe 可能因为覆盖层根本没测到这些节点）',
  ).toEqual([])
}

test('项目选择器：axe 无违规、无未定性的「查不了」、自算对比度达标', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  await expectAccessible(page)
})

test('工作台（项目已开、画布有面板）：axe 无违规、无未定性的「查不了」、自算对比度达标', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  // 等面板真的渲染出来再扫，扫到一半加载的骨架屏没有意义
  await expect(page.locator('[data-canvas-stage] img, [data-canvas-stage] svg').first())
    .toBeVisible({ timeout: 60_000 })
  // 素材卡上的角标是「覆盖层下的文字」，axe 算不出背景色；标题层级它也判不了。
  // 两条都不按规则 id 放行——各自带一遍真核对。
  await expectAccessible(page, {
    allow: [contrastCoveredByOurOwnRuler, headingOrderCheckedByOurselves],
  })
})

test('导出对话框：axe 干净 + 焦点 trap + Escape 关闭后焦点恢复', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })

  const exportButton = page.getByRole('button', { name: /导出|Export/ }).first()
  await exportButton.focus()
  await page.keyboard.press('Enter') // 键盘打开——鼠标才能开的导出不算可达
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()

  // 三条允许，各自带真核对：背景整片 aria-hidden（「焦点确实困在对话框里」由
  // 紧接着那圈 Tab 断言覆盖）；覆盖层下 axe 算不出背景色的节点由自算尺子逐个
  // 核对；跨 landmark 的标题顺序由本文件自己按文档顺序核对。三条进不进
  // incomplete 都随抽屉开合、卡片数量与浏览器而变（`color-contrast` 这条用例
  // 实测过两种都出现过），而豁免带着真核对，用不上并不构成放行。
  //
  // `heading-order` 是跑 #210 的门禁时抓到的一条**旧**偶发：双击加面板后右栏会
  // 切到「图元素」页，它那个 `h2` 与对话框的 `h2` 分属两个 landmark，axe 判不了
  // 顺序就丢进 incomplete，而这条用例没声明它 → 红。切没切过去取决于扫描那一刻
  // 右栏落定没有，所以时红时绿。交错 A/B 实测：**与 #210 的改动无关**，
  // origin/main 上单跑 10 次同样红 3 次（chromium-en）。工作台/问题面板那两条
  // 用例早就声明了它。
  await expectAccessible(page, {
    allow: [dialogBackgroundIsInert, contrastCoveredByOurOwnRuler, headingOrderCheckedByOurselves],
  })

  // 焦点 trap：连按 Tab 一整圈，焦点永远落在对话框里
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab')
    const inside = await page.evaluate(() => {
      const el = document.activeElement
      return !!el?.closest('[role="dialog"]')
    })
    expect(inside, `第 ${i + 1} 次 Tab 后焦点跑出了对话框`).toBe(true)
  }

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  // 关闭后焦点回到触发它的控件（Radix 的承诺，这里钉死成回归门禁）
  await expect(exportButton).toBeFocused()
})

test('项目接入状态：axe 干净 + 焦点 trap + Escape 关闭后焦点恢复', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)

  // 从常驻轨道进（键盘打开——鼠标才能开的入口不算可达）。轨道是 <nav>，
  // 这样不会跟横幅上那个同名按钮撞上。
  const railButton = page
    .getByRole('navigation')
    .getByRole('button', { name: /项目接入状态|Project readiness/ })
  await railButton.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  // 每一行都在（报告取回来了才算真的打开，空壳上扫 axe 什么都证明不了）
  await expect(dialog.getByText(/技术详情|Technical details/).first()).toBeVisible({
    timeout: 30_000,
  })

  // 两条「查不了」的允许各自带真核对：背景整片 aria-hidden（焦点进不去由下面
  // 那圈 Tab 覆盖）；覆盖层下 axe 算不出背景色的节点由自算尺子逐个核对。后者是
  // 这个对话框第一次在真浏览器里跑出来的——`--list` 收得到 ≠ 跑得过。
  //
  // **判据扫全页**，不收进 `[role="dialog"]`：模态打开时全页扫描会把背后的工作台
  // 一并量进来，那正是要的——同一批节点在「有没有模态」下必须给出同一个结论。
  // 曾经收进对话框是为了绕开一处不一致，而那处不一致是**尺子**的缺陷（背景只走
  // DOM 祖先链，看不见画在下面的兄弟层），已在 issue #210 里定性并修掉；
  // `contrast.spec.ts` 里有它的两向判据。
  await expectAccessible(page, {
    allow: [dialogBackgroundIsInert, contrastCoveredByOurOwnRuler],
  })

  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab')
    const inside = await page.evaluate(() => {
      const el = document.activeElement
      return !!el?.closest('[role="dialog"]')
    })
    expect(inside, `第 ${i + 1} 次 Tab 后焦点跑出了接入状态`).toBe(true)
  }

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(railButton).toBeFocused()
})

test('素材卡的状态角标不引入嵌套交互（axe nested-interactive）', async ({ app, page }) => {
  // **必须是一张「不能编辑」的图**：角标与那条带按钮的说明条只在这种卡上出现。
  // 默认夹具里三张图全都已连上脚本（editable），卡上既没有角标也没有说明条，
  // 于是这条用例什么都没量到——直到它在 windows-exe-smoke 上第一次真跑起来，
  // 才在「按钮找不到」上红出来。运行期命名的那份夹具给的是 needs_probe。
  const dir = path.join(os.tmpdir(), `tavotto-e2e-a11y-${Date.now()}`)
  writeRuntimeNamedProject(dir)
  const a = await app({ figures: dir })
  await page.goto(a.baseURL)
  const card = page.getByRole('option').first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.click() // 选中之后说明条才出现——它带一个真按钮，必须在 listbox 外面

  const results = await new AxeBuilder({ page })
    .withRules(['nested-interactive', 'aria-required-children', 'aria-required-parent'])
    .analyze()
  expect(results.violations.map((v) => ({
    id: v.id,
    nodes: v.nodes.slice(0, 8).map((n) => n.target.join(' ')),
  }))).toEqual([])

  // 「查看接入状态」必须键盘到得了：它不在 option 里，所以 Tab 出列表就能落上去
  await expect(
    page.getByRole('button', { name: /查看接入状态|Project readiness/ }).first(),
  ).toBeVisible()
})

test('问题面板：axe 无违规、行内的「修复」不是「定位」的子节点', async ({
  app,
  page,
}) => {
  const a = await app()
  await page.goto(a.baseURL)
  // 先把一张图放上画布——空画布上问题清单只有页面级那几条，行内动作量不到
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-canvas-stage] img, [data-canvas-stage] svg').first())
    .toBeVisible({ timeout: 60_000 })

  // 从常驻轨道进（键盘打开——鼠标才能开的入口不算可达）
  const rail = page.locator('[data-rail="problems"]')
  await rail.focus()
  await page.keyboard.press('Enter')
  const drawer = page.getByRole('complementary', { name: /问题|Problems/ })
  await expect(drawer).toBeVisible()

  // **嵌套交互是这一屏最容易犯的错**：整行可点 + 行尾一颗「修复」，写成
  // 按钮套按钮的话辅助技术里它是一个读不出来的控件
  const nested = await new AxeBuilder({ page })
    .withRules(['nested-interactive', 'aria-required-children', 'aria-required-parent'])
    .analyze()
  expect(nested.violations.map((v) => ({
    id: v.id,
    nodes: v.nodes.slice(0, 8).map((n) => n.target.join(' ')),
  }))).toEqual([])

  // 覆盖层下 axe 算不出背景色的节点由自算尺子逐个核对（与工作台那条同一条纪律）
  await expectAccessible(page, {
    allow: [contrastCoveredByOurOwnRuler, headingOrderCheckedByOurselves],
  })
})

test('图标按钮都有可访问名（axe button-name / 顶栏抽查）', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await expect(page.getByRole('banner')).toBeVisible() // 顶栏（<header>）
  const results = await new AxeBuilder({ page })
    .withRules(['button-name', 'link-name', 'aria-command-name'])
    .analyze()
  expect(results.violations.map((v) => ({
    id: v.id,
    nodes: v.nodes.slice(0, 8).map((n) => n.target.join(' ')),
  }))).toEqual([])
})

test('键盘可达性底线：Tab 能进入界面且焦点可见', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.getByRole('main')).toBeVisible()
  await page.keyboard.press('Tab')
  const focused = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el === document.body) return null
    const r = el.getBoundingClientRect()
    return { tag: el.tagName, visible: r.width > 0 && r.height > 0 }
  })
  expect(focused, 'Tab 之后焦点仍在 body 上——键盘用户进不了界面').not.toBeNull()
  expect(focused!.visible).toBe(true)
})
