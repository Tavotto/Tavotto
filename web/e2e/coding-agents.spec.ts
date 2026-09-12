import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'
import { lowContrastNodes as sharedLowContrastNodes } from './contrast'
import { horizontalOffenders } from './overflow'
import type { Page } from '@playwright/test'

/**
 * 设置 → 编码 Agent 的黄金路径（真浏览器，真后端探测）。
 *
 * jsdom 没有布局引擎，量不出溢出、也量不出行高——**「不横向溢出」「返回后
 * 滚动位置还在」这类断言只有在这里才是真的**。列表内容随跑测试的机器变化
 * （装没装 codex / claude 都可能），所以断言全部打在「结构与行为」上，
 * 不打在具体某个 Agent 的状态上。
 *
 * **定位一律走稳定 `data-*`，不拿可见文案当选择器**（清单在 web/AGENTS.md）。
 * 2026-09-07 这条纪律是被打红打出来的：审计 T44 把两个小标题按用户目标改了
 * 名（「在 A 中使用 B / 在 B 中使用 A」→「配置改图助手 / 连接外部工具」），
 * 两条用例当场找不到元素。把字符串换成新文案只是**把同一个赌注再下一次**；
 * 更糟的是那两条 `toHaveCount(0)` 的反向断言——文案一改它们恒真，红都不会红
 * 一下，直接从「守着」变成「假绿」。
 */
async function openAgentSettings(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.locator('[data-rail="settings"]').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 30_000 })
  // 分区 id 是持久化格式的一部分（AiPanel 的「打开设置」按它跳转），
  // 比导航项上那句会被改名的文案稳得多
  await dialog.getByRole('navigation').locator('[data-section="ai"]').click()
  return dialog
}

test('编码 Agent：列表 → 详情 → 返回，状态与滚动都还在', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  // 一级页面：分组列表 + 两个方向的小节
  await expect(dialog.locator('[data-agent-section="in-app"]')).toBeVisible()
  await expect(dialog.locator('[data-agent-section="external"]')).toBeVisible()
  // 反方向那一行**在反方向那一节里**——两节的归属才是这条用例要守的东西
  await expect(
    dialog.locator('[data-agent-section="external"] [data-agent-codex-integration]'),
  ).toBeVisible()

  // **一级页面不许有任何输入框**（路径 / Base URL / 密钥全在详情里）。
  // 后两条量的是**端点编辑器与概览字段整个不在这一层**，不是「某两句话没出现」：
  // 后者在文案改名后恒真，守不住任何东西。
  await expect(dialog.locator('input[type="text"], input[type="password"]')).toHaveCount(0)
  await expect(page.locator('[data-endpoint-step]')).toHaveCount(0)
  await expect(dialog.locator('[data-agent-field]')).toHaveCount(0)

  // 行主体点进详情
  const row = dialog.locator('[data-agent-open="codex"]')
  await expect(row).toBeVisible()
  await row.click()
  await expect(dialog.locator('[data-agent-detail="codex"]')).toBeVisible()
  // 头部常驻的是状态与版本；「来源 / 可执行文件 / 上次检测」收进了高级设置里的
  // 「概览」折叠（Visual Consolidation）：在 DOM 里、默认不可见——两句都钉，
  // 只写后半句的话把这一段整个删掉也是绿的。
  await expect(dialog.locator('[data-agent-field="state"]')).toBeVisible()
  await expect(dialog.locator('[data-agent-field="source"]')).toHaveCount(1)
  await expect(dialog.locator('[data-agent-field="source"]')).toBeHidden()
  // 高级设置默认折叠。**直接量 `<details>` 的 open**，不拿「输入框不在」当代理：
  // 那个输入框要点过「使用自定义可执行文件」才渲染，折叠与否它都不在——用它当
  // 判据，把 details 强行改成默认展开也照样绿（变异验过，就是这么漏的）。
  await expect(dialog.locator('[data-agent-fold="custom-executable"] summary')).toBeVisible()
  const folds = dialog.locator('details')
  expect(await folds.count()).toBeGreaterThan(0)
  for (const fold of await folds.all()) {
    expect(await fold.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(false)
  }
  // 折叠着 → 里面的东西量得到「不可见」，这条才是「默认不制造噪音」的兑现。
  // **先钉住它确实在 DOM 里**：`toBeHidden()` 对一个根本不存在的元素同样通过，
  // 只写后半句的话，这颗按钮被删掉、锚点被摘掉都是绿的——那是这一轮到处在修的
  // 同一种假绿（这条锚点没有别的正向断言替它兜底，所以得自己钉）。
  const customExe = dialog.locator('[data-agent-custom-exe]')
  await expect(customExe).toHaveCount(1)
  await expect(customExe).toBeHidden()

  // 返回：列表还在
  await dialog.locator('[data-agent-back]').click()
  await expect(dialog.locator('[data-agent-section="in-app"]')).toBeVisible()
})

test('编码 Agent：开关是独立控件，不会顺手打开详情', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  const toggle = dialog.getByRole('switch').first()
  await expect(toggle).toBeVisible()
  // 嵌套交互元素：任何 button 里都不该再有 button（HTML 非法，键盘行为不可预期）
  expect(await dialog.locator('button button').count()).toBe(0)

  if (await toggle.isEnabled()) {
    const before = await toggle.getAttribute('aria-checked')
    await toggle.click()
    await expect(dialog.locator('[data-agent-detail]')).toHaveCount(0) // 没进详情
    await expect(toggle).not.toHaveAttribute('aria-checked', before ?? '')
    await toggle.click()                                      // 还原，别留状态
  }
})

test('编码 Agent：重新检测有播报，且不发生布局跳动', async ({ app, page }) => {
  const a = await app()
  const dialog = await openAgentSettings(page, a.baseURL)

  // 量的必须是**同一个东西**：`ul` 的第一个在探测没回来时是骨架屏、回来之后
  // 才是真列表，那样量到的是两个元素，不是同一块内容有没有跳。
  const list = dialog.locator('[data-agent-section="in-app"] ul')
  await expect(list).toBeVisible({ timeout: 30_000 })
  const before = await list.boundingBox()
  await dialog.locator('[data-agent-rescan]').click()
  await expect(dialog.locator('[data-agent-last-checked]')).toBeVisible({ timeout: 30_000 })
  const after = await list.boundingBox()
  // **先证明两次都真的量到了。** `boundingBox()` 量不到时返回 null，原先那句
  // `?? 0` 会把「什么都没量到」记成「x = 0」：一侧为 null 就是一次假红（实测
  // 报过 |433 - 0|），两侧都为 null 则是一次假绿——0 - 0 永远小于 2，判据在
  // 一个字都没量到的情况下照样通过。
  expect(before, '重新检测前没量到列表').not.toBeNull()
  expect(after, '重新检测后没量到列表').not.toBeNull()
  // 高度可以随内容变，但不该整块跳走（左边缘与宽度稳定）
  expect(Math.abs(after!.x - before!.x)).toBeLessThan(2)
  expect(Math.abs(after!.width - before!.width)).toBeLessThan(2)

  // aria-live 区在（检测完成 / 失败都要说一声）
  await expect(dialog.locator('[aria-live="polite"]')).toHaveCount(1)
})

test('编码 Agent：1024×768 窄窗口不横向溢出', async ({ app, page }) => {
  const a = await app()
  await page.setViewportSize({ width: 1024, height: 768 })
  const dialog = await openAgentSettings(page, a.baseURL)

  await expect(dialog.locator('[data-agent-section="in-app"]')).toBeVisible()
  expect(await horizontalOffenders(page, '[role="dialog"]')).toEqual([])

  // 详情页同样：长路径靠省略，不把面板撑开
  await dialog.locator('[data-agent-open]').first().click()
  await expect(dialog.locator('[data-agent-detail]')).toBeVisible()
  expect(await horizontalOffenders(page, '[role="dialog"]')).toEqual([])
})

test('编码 Agent：axe 无 critical/serious 违规（列表与详情各一次）', async ({ app, page }) => {
  const a = await app()
  // 扫描前关掉动效：对话框进出场进行到一半时 axe 取样会撞上过渡态，
  // 产出不可复现的假阳性（与 a11y.spec.ts 同一条约定）
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const dialog = await openAgentSettings(page, a.baseURL)

  const serious = async () =>
    (await new AxeBuilder({ page }).analyze()).violations
      .filter((v) => v.impact === 'critical' || v.impact === 'serious')
      .map((v) => ({ id: v.id, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }))

  // 一级列表：开关与行按钮的可访问名、结构类规则交给 axe
  expect(await serious()).toEqual([])
  // 新加的状态色（含 --color-warn）交给自算——axe 在这一片只报 incomplete
  expect(await sharedLowContrastNodes(page, '[role="dialog"]')).toEqual([])

  // 详情页：概览、模型服务单选、两个折叠区
  await dialog.locator('[data-agent-open]').first().click()
  await expect(dialog.locator('[data-agent-detail]')).toBeVisible()
  for (const d of await dialog.locator('details').all()) {
    await d.locator('summary').click()
  }
  expect(await serious()).toEqual([])
  expect(await sharedLowContrastNodes(page, '[role="dialog"]')).toEqual([])
})
