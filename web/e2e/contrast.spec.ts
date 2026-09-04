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

/**
 * 背景要按**绘制顺序**取，不能只走 DOM 祖先链（issue #210）。
 *
 * 两个 `<p>` 颜色、字号、DOM 深度完全一样，差别只在**画在它们下面的是谁**：
 * 一个压在兄弟白纸上（`#6b6b64` on `#ffffff` = 5.37:1，达标），一个直接坐在
 * 画布灰上（`#6b6b64` on `#eaeae6` = 4.45:1，不达标）。这就是空画布提示被诬告
 * 的那个形状——它绝对定位在纸面中心，白纸是它的**兄弟**而不是祖先，只走祖先链
 * 的尺子一路找到画布灰，报了一条差 0.05 的假红。
 *
 * 一条用例同时钉两个方向：白纸那条**不许**报（否则就是假红），灰底那条**必须**
 * 报（否则是把判据抽空了换来的绿）。
 */
const SIBLING_PAGE = `
<div style="position:relative;width:320px;height:240px;background:#eaeae6">
  <div style="position:absolute;left:0;top:0;width:320px;height:110px;background:#ffffff"></div>
  <div style="position:absolute;left:20px;top:30px">
    <p style="margin:0;color:#6b6b64">压在兄弟白纸上</p>
  </div>
  <div style="position:absolute;left:20px;top:160px">
    <p style="margin:0;color:#6b6b64">直接坐在画布灰上</p>
  </div>
</div>`

test('自算对比度：背景按绘制顺序取，画在下面的兄弟层也算', async ({ page }) => {
  await page.setContent(SIBLING_PAGE)
  const bad = (await lowContrastNodes(page)).join(' | ')

  expect(
    bad,
    '压在兄弟白纸上的文字被诬告了——尺子只走了 DOM 祖先链，没看见画在下面的兄弟层',
  ).not.toContain('压在兄弟白纸上')
  expect(
    bad,
    '真的坐在画布灰上（4.45:1）的那条漏报了——判据被抽空了',
  ).toContain('直接坐在画布灰上')
})

/**
 * **绘制顺序不等于 DOM 顺序**：z-index / 层叠上下文这一维不能漏（#261 评审）。
 *
 * 形状照抄 `web/src/components/left/LeftPanel.tsx` 的 narrow 档——抽屉是
 * `absolute inset-y-0 z-30`，在 DOM 里排在画布**之前**，却画在画布**之上**
 * （实测 900px 视口下 `position:absolute` / `z-index:30`）。两个 `<p>` 颜色、
 * 字号、几何位置全一样，唯一的差别是那块白面板**画在它上面还是画在它下面**：
 *
 * - `z-30` 那块画在**上面** → 文字真正的背景是画布灰，4.45:1，**必须报**。
 *   把 DOM 顺序当绘制顺序的实现会在这块白底上停下，算出 5.37:1 放行。
 * - 同样 DOM 位置、同样几何、但**没有** z-index 且不定位 → 它就在文字下面，
 *   背景真的是白的，5.37:1，**不许报**。
 *
 * 两条一起钉住的正是「遮挡层在上时不能当背景，真在背后时又必须算进去」。
 */
const ZINDEX_PAGE = `
<div style="position:relative;width:360px;height:120px;background:#eaeae6">
  <aside style="position:absolute;inset-block:0;left:0;width:360px;background:#ffffff;z-index:30"></aside>
  <div style="position:relative">
    <p style="margin:0;padding:40px 0 0 20px;color:#6b6b64">被 z-30 抽屉盖住</p>
  </div>
</div>
<div style="position:relative;width:360px;height:120px;background:#eaeae6">
  <aside style="position:absolute;inset-block:0;left:0;width:360px;background:#ffffff"></aside>
  <div style="position:relative">
    <p style="margin:0;padding:40px 0 0 20px;color:#6b6b64">白面板真在文字背后</p>
  </div>
</div>`

test('自算对比度：绘制顺序按 z-index/层叠上下文，不是 DOM 顺序', async ({ page }) => {
  await page.setContent(ZINDEX_PAGE)
  const bad = (await lowContrastNodes(page)).join(' | ')

  expect(
    bad,
    '把 DOM 顺序当成了绘制顺序——z-30 的抽屉画在文字**上面**，它的白底不是这段文字的背景',
  ).toContain('被 z-30 抽屉盖住')
  expect(
    bad,
    '真在文字背后的那块白面板没算进去——遮挡层不算不等于背后的层也不算',
  ).not.toContain('白面板真在文字背后')
})

/**
 * **这一刻没被画出来的文字不在范围内**，但「折叠着」不等于「永远不看」（#261 评审）。
 *
 * 折叠 `<details>` 里的内容 `getClientRects()` 有值、`display` 也不是 `none`，
 * 可它根本没画（Chromium 走 `content-visibility`）。实测真实界面里这一档几乎全是
 * `AdvancedDetails` 的 `dt`/`dd`——一屏 63 个文字节点里有 20 个。给它们编一个背景色
 * 就是凭空造读数。
 *
 * 两个方向：折叠时**不许**报（它没被画出来），展开后**必须**报（同一段文字、
 * 同样不达标）。
 */
const COLLAPSED_PAGE = (open: boolean) => `
<div style="background:#ffffff;padding:8px">
  <details${open ? ' open' : ''}>
    <summary style="color:#222222">折叠标题</summary>
    <p style="margin:0;color:#cccccc">折叠里的浅色文字</p>
  </details>
</div>`

test('自算对比度：没被画出来的文字不算，展开之后必须算', async ({ page }) => {
  await page.setContent(COLLAPSED_PAGE(false))
  expect(
    (await lowContrastNodes(page)).join(' | '),
    '折叠 `<details>` 里的文字根本没画出来，却给它编了一个背景色',
  ).not.toContain('折叠里的浅色文字')

  await page.setContent(COLLAPSED_PAGE(true))
  expect(
    (await lowContrastNodes(page)).join(' | '),
    '展开之后仍然漏报——「折叠时跳过」被写成了「永远跳过」',
  ).toContain('折叠里的浅色文字')
})

/**
 * **「判不准」是独立一档**（#261 评审）。
 *
 * 叠到底都没有不透明层时，以前这里按「白纸」算——那是把「不知道」并进了一个
 * 相邻取值，算出来的比值看着跟真的一样自信。现在它必须报出来让人去看。
 */
const NO_OPAQUE_PAGE = `<p style="color:#6b6b64">底下没有不透明层</p>`

test('自算对比度：量不出背景色时报「判不准」，不按白纸编一个数', async ({ page }) => {
  await page.setContent(NO_OPAQUE_PAGE)
  const bad = (await lowContrastNodes(page)).join(' | ')

  expect(bad, '叠到底没有不透明层，却给出了一个自信的比值').toContain('判不准')
  expect(bad, '判不准也要指名道姓说是哪个节点').toContain('底下没有不透明层')
})

/**
 * 对比度是**稳定态**的属性：淡入淡出中间那一帧不是（issue #210）。
 *
 * 两个 `<p>` 走同一条淡入动画，落定后一个够黑、一个本来就浅。扫描必须等动效
 * 结束再量——不等的话，够黑那条会在半透明的中间帧上被报成假红（实测：接入状态
 * 那条用例先 focus 轨道按钮，气泡正在淡出时被量成 `1.27:1（有效 alpha 0.12）`），
 * 而且下一帧就消失，两次扫描结论不一致。
 */
const FADING_PAGE = `
<style>
  @keyframes tavotto-fade { from { opacity: .3 } to { opacity: 1 } }
  .fading { animation: tavotto-fade 900ms linear forwards }
</style>
<div style="background:#ffffff;padding:8px">
  <p class="fading" style="color:#222222">淡入之后够黑</p>
  <p class="fading" style="color:#cccccc">淡入之后仍然浅</p>
</div>`

test('自算对比度：等动效落定再量，不报中间帧', async ({ page }) => {
  await page.setContent(FADING_PAGE)
  const bad = (await lowContrastNodes(page)).join(' | ')

  expect(
    bad,
    '淡入还没结束就量了——中间帧的有效 alpha 会把一条达标的文字报成假红',
  ).not.toContain('淡入之后够黑')
  expect(
    bad,
    '落定之后仍然不达标的那条漏报了——等动效不等于放过它',
  ).toContain('淡入之后仍然浅')
})
