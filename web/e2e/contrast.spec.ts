/**
 * 自算对比度这把**尺子本身**的判据（#130）。
 *
 * a11y.spec.ts 量的是真实界面；这里用一张受控的小页面把尺子的每条规则钉死——
 * 界面上的红绿会随内容变，尺子的行为不该。
 */
import { expect, test } from './fixtures'
import { lowContrastNodes } from './contrast'

const PAGE = `
<div style="background:#ffffff;padding:8px">
  <p id="a" style="color:#222222">够黑的正文</p>
  <p id="b" style="color:#222222;opacity:.3">自己被淡化</p>
  <div style="opacity:.3"><p id="c" style="color:#222222">祖先被淡化</p></div>
  <p id="d" style="color:rgba(34,34,34,.3)">颜色自带 alpha</p>
  <button id="e" disabled style="color:#222222;opacity:.3">禁用态</button>
  <p id="f" style="color:#cccccc">颜色本身就浅</p>
</div>`

test('自算对比度：CSS opacity 计入有效 alpha，禁用态按 WCAG 排除', async ({ page }) => {
  await page.setContent(PAGE)
  const bad = (await lowContrastNodes(page)).join(' | ')

  // 够黑的正文 & 禁用态：不该报
  expect(bad, '够黑的正文被误报了').not.toContain('够黑的正文')
  // WCAG 1.4.3 明确排除 "inactive user interface component"；axe 也跳过禁用态。
  // 不排除的话，本仓库每一个 `disabled:opacity-35` 的按钮都会变成一条假红。
  expect(bad, '禁用态不该进对比度判据').not.toContain('禁用态')

  // 三种「看起来一样淡」的写法都要被抓住
  expect(bad, 'CSS opacity 没有计入——getComputedStyle().color 不含 group opacity')
    .toContain('自己被淡化')
  expect(bad, '祖先的 opacity 没有累乘进来').toContain('祖先被淡化')
  expect(bad, '颜色自带的 alpha 没有合成').toContain('颜色自带 alpha')
  expect(bad, '颜色本身就浅的情况漏了').toContain('颜色本身就浅')

  // 报出来的那几条要带上有效 alpha，排障时一眼看得出是被谁淡化的
  expect(bad).toMatch(/有效 alpha 0\.\d+/)
})
