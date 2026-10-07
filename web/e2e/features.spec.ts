import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { expect, openElementsTab, test } from './fixtures'

/**
 * 功能登记表的核心路径用例（ADR 0097，`docs/features/registry.json`）。
 *
 * 每条用例都带 `@feature:<id>` 标签，登记表把它列在那个功能名下；门禁
 * （`scripts/ci/feature_registry.py`）判两件事：登记的用例在、没有被跳过（静态），
 * 以及合并态那次真跑里它确实执行并通过（posix-e2e 的运行报告）。
 *
 * 判据只认**用户看得见的结果**，不认「DOM 里有这个节点」：
 *   * 元素在视口内、点得到（`expectInViewport`）；
 *   * 操作之后画面真的变了——对象的屏幕位置、引擎重画出来的 SVG 里那个元素的框、
 *     落盘文件的文件头；
 *   * 引擎那一侧以 `data-display="exact"` + `data-display-key` 换版为准：画布此刻挂的
 *     是这一版文档自己的精确图，而不是上一张暂挂的。
 *
 * 选择器按 web/AGENTS.md 认稳定 `data-*`；只有产品没给锚点的地方才用可达名，
 * 并在旁边写明。
 */

// 画布对象拖动量的是「位移 = 鼠标位移」，吸附会有意把落点拽到对齐线上——与这把尺子
// 正交（吸附有自己的用例）。同 fake-realtime.spec.ts：在关掉吸附的画布上量。
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }))
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

const VIEWPORT = { width: 1440, height: 900 }
const CARD = '[data-card="Fig1_kinetics.pdf"]'

/** 元素整个框都在视口里（不是「DOM 里有」，也不是「有一个像素露出来」）。 */
async function expectInViewport(page: Page, loc: Locator, what: string) {
  await expect(loc, `${what} 应当可见`).toBeVisible()
  const b = await loc.boundingBox()
  expect(b, `${what} 没有布局框`).not.toBeNull()
  const vp = page.viewportSize()!
  expect(b!.width, `${what} 宽度为 0`).toBeGreaterThan(0)
  expect(b!.height, `${what} 高度为 0`).toBeGreaterThan(0)
  expect(b!.x, `${what} 左边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b!.y, `${what} 上边出了视口`).toBeGreaterThanOrEqual(0)
  expect(b!.x + b!.width, `${what} 右边出了视口`).toBeLessThanOrEqual(vp.width + 0.5)
  expect(b!.y + b!.height, `${what} 下边出了视口`).toBeLessThanOrEqual(vp.height + 0.5)
}

/** 点得到：框中心那一点的最上层元素就是它（或它的后代），没有被浮层 / 遮罩挡住。 */
async function expectHittable(loc: Locator, what: string) {
  const hit = await loc.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return !!top && (top === el || el.contains(top))
  })
  expect(hit, `${what} 中心被别的元素挡住了`).toBe(true)
}

/** 双击素材卡打开这张图（快速编辑，当场在图内编辑态），等引擎的 SVG 画出来。 */
async function openFigure(page: Page, baseURL: string) {
  await page.setViewportSize(VIEWPORT)
  await page.goto(baseURL)
  await page.locator(CARD).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg]')).toHaveCount(1, { timeout: 120_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await waitExact(page)
}

/** 画布上唯一那张图此刻挂的是这一版文档自己的精确图；返回它的版本键。 */
async function waitExact(page: Page, notKey?: string | null): Promise<string | null> {
  const host = page.locator('[data-display]')
  await expect(host).toHaveCount(1)
  await expect
    .poll(
      async () => {
        const kind = await host.getAttribute('data-display')
        const key = await host.getAttribute('data-display-key')
        return kind === 'exact' && (notKey === undefined || key !== notKey) ? 'ok' : `${kind}:${key}`
      },
      { timeout: 90_000, message: '画布没有换成这一版文档的精确图' },
    )
    .toBe('ok')
  return host.getAttribute('data-display-key')
}

/** 引擎 SVG 里某个图内元素的屏幕框（图内编辑宿主是单例，先断言再取）。 */
function gidBox(page: Page, gid: string) {
  return page.evaluate((id) => {
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`) as SVGGraphicsElement | null
    if (!n) return null
    const r = n.getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

/** 画布上（排版模式）的对象框，按文档里的对象 id 取。 */
function objectBoxes(page: Page) {
  return page.locator('[data-canvas-stage] [data-object-id]').evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect()
      return { id: e.getAttribute('data-object-id'), x: r.x, y: r.y, w: r.width, h: r.height }
    }),
  )
}

test(
  '功能：素材放上画布渲染出真图；点选出单选浮动栏；拖动真的移动；撤销 / 重做回到对应位置',
  {
    tag: [
      '@feature:canvas.place-and-render',
      '@feature:canvas.select-and-drag',
      '@feature:canvas.single-context-bar',
      '@feature:edit.undo-redo',
    ],
  },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    // 回到排版：上下文栏上唯一那颗返回入口
    await page.locator('[data-context-back]').click()
    await expect(page.locator('[data-element-svg]')).toHaveCount(0)

    const panel = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(panel).toHaveCount(1, { timeout: 30_000 })
    await expectInViewport(page, panel, '画布上的面板')

    // ── 首次渲染：面板里是真图，不是空框 / 碎图标 / 占位 ──────────────────
    // 位图走像素：画进 canvas 数颜色；SVG 走几何：有面积的绘制节点数。两种都要「有内容」。
    await expect
      .poll(
        () =>
          panel.evaluate(async (el) => {
            const img = el.querySelector('img') as HTMLImageElement | null
            if (img) {
              if (!img.complete || img.naturalWidth === 0) return 'img-not-loaded'
              const c = document.createElement('canvas')
              c.width = Math.min(img.naturalWidth, 400)
              c.height = Math.min(img.naturalHeight, 300)
              const ctx = c.getContext('2d')!
              ctx.drawImage(img, 0, 0, c.width, c.height)
              const d = ctx.getImageData(0, 0, c.width, c.height).data
              const colors = new Set<number>()
              let ink = 0
              for (let i = 0; i < d.length; i += 4) {
                colors.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2])
                if (d[i] + d[i + 1] + d[i + 2] < 600 && d[i + 3] > 0) ink++
              }
              return colors.size > 8 && ink > 200 ? 'ok' : `img-blank:${colors.size}:${ink}`
            }
            const svg = el.querySelector('svg')
            if (svg) {
              const drawn = [...svg.querySelectorAll('path, text, use, image')].filter((n) => {
                const r = (n as SVGGraphicsElement).getBoundingClientRect()
                return r.width > 0 && r.height > 0
              })
              return drawn.length > 10 ? 'ok' : `svg-blank:${drawn.length}`
            }
            return 'nothing'
          }),
        { timeout: 60_000, message: '面板里应当画出一张真图' },
      )
      .toBe('ok')

    // ── 单选浮动栏：点一下出现、整条在视口内、「编辑图内元素」点得到 ───────────
    await panel.click()
    const bar = page.locator('[data-context-bar]')
    await expect(bar).toHaveCount(1)
    await expectInViewport(page, bar, '单选浮动栏')
    // 产品没给这颗按钮 data 锚点：按可达名认，收在浮动栏里（不取全页第一个）
    const editBtn = bar.getByRole('button', { name: /改图里的内容|Edit figure elements/ })
    await expect(editBtn).toHaveCount(1)
    await expectHittable(editBtn, '「编辑图内元素」')

    // ── 拖动：屏幕上真的移动了 Δ ────────────────────────────────────────────
    const [b0] = await objectBoxes(page)
    const dx = 120
    const dy = 60
    await page.mouse.move(b0.x + b0.w / 2, b0.y + b0.h / 2)
    await page.mouse.down()
    for (let i = 1; i <= 12; i++) await page.mouse.move(b0.x + b0.w / 2 + (dx * i) / 12, b0.y + b0.h / 2 + (dy * i) / 12)
    await page.mouse.up()
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.abs(b.x - b0.x - dx) < 2 && Math.abs(b.y - b0.y - dy) < 2
      }, { message: '面板应当跟着鼠标平移 (120, 60)' })
      .toBe(true)
    const [b1] = await objectBoxes(page)
    // 松手后单选浮动栏仍在（选区没丢）
    await expect(bar).toBeVisible()

    // ── 撤销 / 重做：回到拖动前 / 拖动后的位置 ──────────────────────────────
    await page.keyboard.press('ControlOrMeta+z')
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.hypot(b.x - b0.x, b.y - b0.y)
      }, { message: '撤销后面板应当回到拖动前的位置' })
      .toBeLessThan(1)
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await expect
      .poll(async () => {
        const [b] = await objectBoxes(page)
        return Math.hypot(b.x - b1.x, b.y - b1.y)
      }, { message: '重做后面板应当回到拖动后的位置' })
      .toBeLessThan(1)
  },
)

test(
  '功能：多选浮动栏在视口内，点「左对齐」后各对象左边真的对齐；撤销还原',
  { tag: ['@feature:canvas.multi-context-bar-align'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    const stage = page.locator('[data-canvas-stage]')
    await expect(stage).toBeVisible({ timeout: 30_000 })

    // 三段文字，左边各不相同（文字工具的快捷键是 T，见快捷键帮助）
    for (const [x, y, s] of [
      [300, 200, 'alpha'],
      [460, 320, 'beta'],
      [380, 440, 'gamma'],
    ] as const) {
      await page.keyboard.press('Escape')
      await page.keyboard.press('t')
      await stage.click({ position: { x, y } })
      await page.keyboard.type(s)
      await page.keyboard.press('Escape')
    }
    await page.keyboard.press('Escape')
    const objs = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(objs).toHaveCount(3)
    const before = (await objectBoxes(page)).map((b) => Math.round(b.x))
    expect(new Set(before).size, '三段文字的左边应当各不相同').toBe(3)

    await page.keyboard.press('ControlOrMeta+a')
    const bar = page.locator('[data-multi-selection-context-bar]')
    await expect(bar).toHaveCount(1)
    await expectInViewport(page, bar, '多选浮动栏')
    await expect(bar.locator('[data-selection-count]')).toHaveAttribute('data-selection-count', '3')

    // 宽屏下对齐按钮直接在栏上；窄的变体收在「对齐」弹层里——两种都认
    let left = bar.locator('[data-align-mode="left"]')
    if (!(await left.count())) {
      await bar.locator('[data-multi-menu="align"]').click()
      left = page.locator('[data-align-mode="left"]')
    }
    await expect(left).toHaveCount(1)
    await expectHittable(left, '「左对齐」')
    await left.click()
    await expect
      .poll(async () => new Set((await objectBoxes(page)).map((b) => Math.round(b.x))).size, {
        message: '左对齐之后三段文字的左边应当是同一条线',
      })
      .toBe(1)
    // 对齐后选区还在、浮动栏还在
    await expect(bar).toBeVisible()

    await page.keyboard.press('ControlOrMeta+z')
    await expect
      .poll(async () => (await objectBoxes(page)).map((b) => Math.round(b.x)).sort().join(','), {
        message: '撤销后应当回到对齐前的位置',
      })
      .toBe([...before].sort().join(','))
  },
)

test(
  '功能：同一窗口里复制对象，切到另一张画布粘贴，对象出现在原坐标；原画布不受影响',
  { tag: ['@feature:canvas.copy-paste-across-canvases'] },
  async ({ app, page }) => {
    const a = await app()
    // 对象剪贴板走系统剪贴板（`lib/clipboard.ts`）：Chromium 里读写都要授权
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: a.baseURL })
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    const stage = page.locator('[data-canvas-stage]')
    await expect(stage).toBeVisible({ timeout: 30_000 })

    // 第一张画布：一段文字（文字工具的快捷键是 T），选中它 ⌘C
    await page.keyboard.press('t')
    await stage.click({ position: { x: 360, y: 260 } })
    await page.keyboard.type('alpha')
    await page.keyboard.press('Escape')
    // 开着的其它画布标签也挂在舞台里（`CanvasLayers`：整层 display:none），数对象只数看得见的
    const objs = page.locator('[data-canvas-stage] [data-object-id]').filter({ visible: true })
    // 两张画布的缩放 / 平移各自记着，屏幕坐标不可比：换算成「相对纸面左上角、以纸宽为单位」
    // ——两张纸同尺寸（都是默认页面）时，这就是文档坐标。纸与对象在**同一帧**里量，并等两次
    // 读数一致：刚进页面时视图还在做适配缩放，分两次量会把缩放过程算进位移里
    const read = () =>
      objs.evaluateAll((els) => {
        // 纸面也只数看得见的（别的画布标签的纸同样 display:none），且必须恰好一张
        const sheets = [...document.querySelectorAll('[data-page-sheet]')]
          .map((e) => e.getBoundingClientRect())
          .filter((r) => r.width > 0)
        if (sheets.length !== 1) throw new Error(`看得见的纸面应恰有一张，实际 ${sheets.length}`)
        const sh = sheets[0]
        return els.map((e) => {
          const r = e.getBoundingClientRect()
          return {
            id: e.getAttribute('data-object-id'),
            x: (r.x - sh.x) / sh.width,
            y: (r.y - sh.y) / sh.width,
            aspect: sh.height / sh.width,
          }
        })
      })
    const boxes = async () => {
      let prev = JSON.stringify(await read())
      await expect
        .poll(async () => {
          const cur = JSON.stringify(await read())
          const same = cur === prev
          prev = cur
          return same
        }, { message: '画布视图一直没停下来' })
        .toBe(true)
      return JSON.parse(prev) as Awaited<ReturnType<typeof read>>
    }
    await expect(objs).toHaveCount(1)
    await objs.first().click()
    await page.keyboard.press('ControlOrMeta+c')
    // 播报区认 `data-status-live`（同 cross-tab-paste.spec.ts）
    await expect(page.locator('[data-status-live]')).toHaveText(/已复制/)
    const [src] = await boxes()

    // 新建第二张画布：它被激活、上面什么都没有。「+」认 `data-new-canvas-tab`，并断言它是单例
    // （左栏画布列表里另有一颗同名按钮，按可达名认会挑中哪一颗不确定）
    const tabs = page.locator('[data-canvas-tab]')
    await expect(tabs).toHaveCount(1)
    const first = await tabs.first().getAttribute('data-canvas-tab')
    const newTab = page.locator('[data-new-canvas-tab]')
    await expect(newTab).toHaveCount(1)
    await newTab.click()
    await expect(tabs).toHaveCount(2)
    const active = page.locator('[data-canvas-tab][data-active]')
    await expect(active).not.toHaveAttribute('data-canvas-tab', first!)
    await expect(objs).toHaveCount(0)

    await stage.click({ position: { x: 700, y: 500 } })
    await page.keyboard.press('ControlOrMeta+v')
    await expect(page.locator('[data-status-live]')).toHaveText(/已粘贴 1 个对象/, { timeout: 10_000 })
    await expect(objs).toHaveCount(1)
    await expectInViewport(page, objs.first(), '粘贴出来的对象')
    await expect(objs.first()).toContainText('alpha')
    // 跨画布粘贴保持原坐标（同一张画布才错开 4 mm）。0.005 纸宽 ≈ 0.75 mm（默认纸宽 150 mm），
    // 远小于同画布那 4 mm 的错开
    const [dst] = await boxes()
    expect(dst.aspect, '两张画布的纸面尺寸应当相同').toBeCloseTo(src.aspect, 3)
    expect(Math.hypot(dst.x - src.x, dst.y - src.y), JSON.stringify({ src, dst })).toBeLessThan(0.005)
    expect(dst.id).not.toBe(src.id)

    // 回到第一张画布：原对象还在、只有它一个
    await page.locator(`[data-canvas-tab="${first}"]`).click()
    await expect(objs).toHaveCount(1)
    const [back] = await boxes()
    expect(back.id).toBe(src.id)
  },
)

test(
  '功能：换画布尺寸后页面重新取景居中；比页面大的图加进来等比缩到约 1.15 倍页面，伸出部分画淡',
  { tag: ['@feature:canvas.page-fit-and-placement'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    const stage = page.locator('[data-canvas-stage]')
    const sheet = page.locator('[data-page-sheet]')
    await expect(sheet).toBeVisible({ timeout: 30_000 })

    // 页面改成 40 × 30 mm（示例图 Fig1_kinetics 是 73 × 58 mm，约 1.9 倍）。
    // 尺寸行没有逐框的 data 锚点：行上有 `data-page-size-row`，里面依次是 W、H 两个输入框
    const size = page.locator('[data-page-size-row] input')
    await expect(size).toHaveCount(2)
    await size.nth(0).fill('40')
    await size.nth(0).press('Enter')
    await size.nth(1).fill('30')
    await size.nth(1).press('Enter')

    // 重新取景：页面整张在视口内、在舞台中间，并且撑开到舞台的大半——视口不动的话
    // 40 × 30 mm 的页面只剩舞台里的一小块。
    //
    // **等取景补间落定再量**（`viewportStore.animateTo`，180 ms）：第二次回车时页面高度
    // 瞬间变成 30 mm，zoom / pan 却是从上一个落点补间过去的——途中页面水平居中（宽没变）、
    // 竖直偏开，落定才居中。此前的判据是「撑满舞台的比例 > 0.6」，第一次回车（W = 40）的
    // 取景就已满足，于是第一次轮询就放行、在补间半路上量；CI 上两次回车隔 116 ms 时实测
    // 偏 3.5 / 3.8 px（#720 的 posix-e2e，trace 逐帧看得到页面中心 413 → 499 走完）。
    // 明确条件：页面已是 40 : 30，且舞台上没有在走的视口补间（`data-view-tweening`）
    await expect
      .poll(async () => {
        const s = (await sheet.boundingBox())!
        return Math.abs(s.width / s.height - 40 / 30) < 0.01
      }, { message: '页面应当换成 40 × 30' })
      .toBe(true)
    await expect(page.locator('[data-world-transform][data-view-tweening]')).toHaveCount(0)
    const s0 = (await sheet.boundingBox())!
    const g0 = (await stage.boundingBox())!
    expect(Math.max(s0.width / g0.width, s0.height / g0.height), '换尺寸后页面应当重新撑满舞台').toBeGreaterThan(
      0.6,
    )
    await expectInViewport(page, sheet, '页面')
    expect(Math.abs(s0.x + s0.width / 2 - (g0.x + g0.width / 2)), '页面应当水平居中').toBeLessThan(2)
    // 垂直方向居中于「工具条之上的可用区」：上边距 36、下边距让到工具条顶边之上（60），
    // 所以页面中心比舞台中心高 (60 − 36) / 2 = 12 px（`TOOLBAR_FIT_CLEARANCE`，#770）
    expect(Math.abs(s0.y + s0.height / 2 - (g0.y + g0.height / 2 - 12)), '页面应当垂直居中（让出底部工具条）').toBeLessThan(2)
    expect(s0.width / s0.height).toBeCloseTo(40 / 30, 2)

    // 素材卡选中后 Shift+Enter = 「添加到画布」（与卡上那颗就近入口同一个动作）
    await page.locator(CARD).click()
    await page.keyboard.press('Shift+Enter')
    const obj = page.locator('[data-canvas-stage] [data-object-id]')
    await expect(obj).toHaveCount(1, { timeout: 30_000 })

    const outline = page.locator('[data-page-outline]')
    await expect
      .poll(async () => {
        const o = (await obj.boundingBox())!
        const p = (await outline.boundingBox())!
        return Math.max(o.width / p.width, o.height / p.height)
      }, { message: '图应当缩到约 1.15 倍页面（稍大于页面，但远小于原图的 1.9 倍）' })
      .toBeGreaterThan(1.1)
    // 取景是一段补间：等页面轮廓连续两次读数一样再量，否则图与色带是在不同帧量的
    let last = ''
    await expect
      .poll(async () => {
        const now = JSON.stringify(await outline.boundingBox())
        const same = now === last
        last = now
        return same
      }, { message: '加图后的取景补间应当停下来', intervals: [100] })
      .toBe(true)
    const o = (await obj.boundingBox())!
    const p = (await outline.boundingBox())!
    expect(Math.max(o.width / p.width, o.height / p.height)).toBeLessThanOrEqual(1.151)
    expect(o.width / o.height, '等比缩放').toBeCloseTo(73.33 / 57.77, 1)
    // 取景「页面 ∪ 这张图」：图和页面轮廓都整张在视口里
    await expectInViewport(page, obj, '新加的图')
    await expectInViewport(page, outline, '页面轮廓')

    // 伸出页面的那截被画淡：那一点上有一条不透明度 > 0 的遮罩色带
    const overflowPt =
      o.y < p.y - 1
        ? { x: o.x + o.width / 2, y: (o.y + p.y) / 2 }
        : { x: (o.x + p.x) / 2, y: o.y + o.height / 2 }
    const dimmed = await page.evaluate(({ x, y }) => {
      const mask = document.querySelector('[data-page-outside-mask]')
      if (!mask) return false
      return [...mask.children].some((el) => {
        if (el.hasAttribute('data-page-outline')) return false
        const r = el.getBoundingClientRect()
        const inside = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
        const bg = getComputedStyle(el).backgroundColor
        const alpha = /rgba?\(([^)]+)\)/.exec(bg)?.[1].split(/[ ,/]+/).filter(Boolean)[3]
        return inside && bg !== 'transparent' && (alpha == null || Number(alpha) > 0)
      })
    }, overflowPt)
    expect(dimmed, '伸出页面的部分应当被遮罩画淡').toBe(true)

    // 取景矩形跟着画布会话走（#706 评审 P2）：切到新画布再切回，图仍整张在舞台里；
    // 之后窗口缩放按同一块矩形重算，图也仍整张在舞台里。只记「在适应模式」的话切回来
    // 按页面取景，伸出页面的那截会跑到舞台外
    const inStage = async () => {
      const b = (await obj.boundingBox())!
      const g = (await stage.boundingBox())!
      return (
        b.x >= g.x - 0.5 &&
        b.y >= g.y - 0.5 &&
        b.x + b.width <= g.x + g.width + 0.5 &&
        b.y + b.height <= g.y + g.height + 0.5
      )
    }
    const tabs = page.locator('[data-canvas-tab]')
    const first = await tabs.first().getAttribute('data-canvas-tab')
    await page.locator('[data-new-canvas-tab]').click()
    await expect(tabs).toHaveCount(2)
    // 别的画布标签整层 display:none 但还挂在舞台里：数对象只数看得见的
    await expect(obj.filter({ visible: true })).toHaveCount(0)
    await page.locator(`[data-canvas-tab="${first}"]`).click()
    await expect(obj.filter({ visible: true })).toHaveCount(1)
    await expect.poll(inStage, { message: '切回画布后新加的图应当整张在舞台里' }).toBe(true)
    await page.setViewportSize({ width: VIEWPORT.width - 200, height: VIEWPORT.height - 120 })
    await expect.poll(inStage, { message: '窗口缩放后新加的图应当仍整张在舞台里' }).toBe(true)
  },
)

test(
  '功能：图内点选标题改字号，引擎重画后标题真的变大；撤销回到原字号',
  { tag: ['@feature:figure.select-and-edit'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const h0 = (await gidBox(page, 'axes_0.title'))!
    expect(h0, '样例图应当有标题').not.toBeNull()

    // 在图上直接点标题（图内元素的选中），属性栏换成标题的字号
    await page.mouse.click(h0.x + h0.w / 2, h0.y + h0.h / 2)
    const size = page.locator('[data-prop="fontsize"] input')
    await expect(size).toHaveCount(1, { timeout: 15_000 })
    await expectInViewport(page, size, '字号输入框')
    const v0 = Number(await size.inputValue())
    expect(v0).toBeGreaterThan(0)

    const key0 = await waitExact(page)
    await size.fill(String(v0 + 8))
    await size.press('Enter')
    const key1 = await waitExact(page, key0)
    const h1 = (await gidBox(page, 'axes_0.title'))!
    expect(h1.h, `字号 ${v0} → ${v0 + 8} 之后标题应当变高（${h0.h} → ${h1.h}）`).toBeGreaterThan(h0.h * 1.2)

    // 撤销：把焦点从输入框挪走再按（输入框里的 ⌘Z 归文本编辑管）
    await page.locator('[data-canvas-stage]').focus()
    await page.keyboard.press('ControlOrMeta+z')
    await waitExact(page, key1)
    await expect
      .poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h0.h), {
        message: '撤销后标题应当回到原来的高度',
      })
      .toBeLessThan(0.5)
  },
)

test(
  '功能：图例位置网格点「左上」，引擎重画后图例真的挪到坐标区左上角',
  { tag: ['@feature:legend.properties'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const axes = (await gidBox(page, 'axes_0.patch')) ?? (await gidBox(page, 'axes_0'))
    expect(axes, '样例图应当有坐标区').not.toBeNull()
    const legend0 = (await gidBox(page, 'axes_0.legend'))!
    expect(legend0, '样例图应当有图例').not.toBeNull()
    const cx = (b: { x: number; w: number }) => b.x + b.w / 2
    const cy = (b: { y: number; h: number }) => b.y + b.h / 2
    // 起点：图例在坐标区的右半边（样例图默认右下）
    expect(cx(legend0)).toBeGreaterThan(cx(axes!))

    await openElementsTab(page)
    await page.locator('[data-el="axes_0.legend"]').click()
    const upperLeft = page.locator('[role="radiogroup"] [role="radio"][data-option="upper left"]')
    await expect(upperLeft).toHaveCount(1, { timeout: 15_000 })
    await expectInViewport(page, upperLeft, '图例位置「左上」')
    const key0 = await waitExact(page)
    await upperLeft.click()
    await expect(upperLeft).toHaveAttribute('aria-checked', 'true')
    await waitExact(page, key0)
    await expect
      .poll(async () => {
        const l = (await gidBox(page, 'axes_0.legend'))!
        return cx(l) < cx(axes!) && cy(l) < cy(axes!)
      }, { message: '图例应当落在坐标区的左上四分之一' })
      .toBe(true)
  },
)

test(
  '功能：左栏「样式」改标题字号，图跟着重画；「恢复原样」回到原来',
  { tag: ['@feature:style.panel'] },
  async ({ app, page }) => {
    const a = await app()
    await openFigure(page, a.baseURL)
    const h0 = (await gidBox(page, 'axes_0.title'))!
    expect(h0, '样例图应当有标题').not.toBeNull()

    const rail = page.locator('[data-rail="style"]')
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    const panel = page.locator('[data-style-panel]')
    await expect(panel).toBeVisible({ timeout: 30_000 })
    const size = panel.locator('[data-style-cell="title.size"] input')
    await expect(size).toHaveCount(1, { timeout: 30_000 })
    await expectInViewport(page, size, '样式面板「标题」字号')
    const v0 = Number(await size.inputValue())
    expect(v0).toBeGreaterThan(0)

    const key0 = await waitExact(page)
    await size.fill(String(v0 + 8))
    await size.press('Enter')
    const key1 = await waitExact(page, key0)
    const h1 = (await gidBox(page, 'axes_0.title'))!
    expect(h1.h, `样式面板把标题字号改大之后，图上的标题应当变高（${h0.h} → ${h1.h}）`).toBeGreaterThan(h0.h * 1.2)

    const restore = page.locator('[data-style-restore]')
    await expect(restore).toBeEnabled({ timeout: 30_000 })
    await restore.click()
    await waitExact(page, key1)
    await expect
      .poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h0.h), {
        message: '「恢复原样」之后标题应当回到原来的高度',
      })
      .toBeLessThan(0.5)
  },
)

test(
  '功能：导出对话框一次导出 PDF / PNG / TIFF，三个文件真的落盘、文件头对得上',
  { tag: ['@feature:export.pdf', '@feature:export.png', '@feature:export.tiff'] },
  async ({ app, page }) => {
    test.setTimeout(300_000)
    const a = await app()
    await openFigure(page, a.baseURL)
    await page.locator('[data-context-back]').click()
    await expect(page.locator('[data-canvas-stage] [data-object-id]')).toHaveCount(1)

    await page.keyboard.press('ControlOrMeta+e')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expectInViewport(page, dialog, '导出对话框')
    // 格式名不翻译（PDF / PNG / TIFF）；复选框的可达名就是它
    for (const fmt of ['PDF', 'PNG', 'TIFF']) {
      const box = dialog.getByRole('checkbox', { name: fmt, exact: true })
      await expect(box).toHaveCount(1)
      if (!(await box.isChecked())) await box.check()
      await expect(box).toBeChecked()
    }
    const eps = dialog.getByRole('checkbox', { name: 'EPS', exact: true })
    if (await eps.isChecked()) await eps.uncheck()

    const confirm = dialog.locator('input[data-export-confirm]')
    if (await confirm.count()) await confirm.check()

    // 导出走后台作业：`/api/export/start` 当场回作业（带导出目录），结果经 SSE 推给界面。
    // 端点形态不是这条用例的主语——导出目录取自 start 的回包，结果认界面那句话 + 磁盘上的文件
    const started = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST',
    )
    // 产品没给主按钮 data 锚点：按可达名认，收在对话框里
    const start = dialog.getByRole('button', { name: /开始导出/ })
    await expect(start).toBeEnabled()
    // 导出目录不一定是空的：夹具拷的是仓库里的 examples/figures，Windows 那条腿在 e2e 之前跑的
    // 冒烟会往它的 tavottofile/export 里导出 smoke_*.pdf（#676 full-ci 首跑就红在这里）。
    // 主语是**这一次导出写出的文件**：按修改时间认点「开始导出」之后写的（留 1 s 给文件系统的时间精度）
    const t0 = Date.now() - 1000
    await start.click()
    const job = (await (await started).json()) as { export_dir?: string }
    expect(job.export_dir, 'start 回包里应当有导出目录').toBeTruthy()
    const exportDir = job.export_dir!

    // 界面说出了结果（不是只有请求成功）
    await expect(dialog.getByText(/已保存到/)).toBeVisible({ timeout: 240_000 })
    // 文件按扩展名认（同目录里还有预检报告等别的产物）；文件头要对得上格式，不认「有个文件」
    const magic: Record<string, (b: Buffer) => boolean> = {
      '.pdf': (b) => b.subarray(0, 5).toString('latin1') === '%PDF-',
      '.png': (b) => b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
      '.tiff': (b) =>
        b.subarray(0, 4).equals(Buffer.from([0x49, 0x49, 0x2a, 0x00])) ||
        b.subarray(0, 4).equals(Buffer.from([0x4d, 0x4d, 0x00, 0x2a])),
    }
    const extOf = (f: string) => {
      const e = path.extname(f).toLowerCase()
      return e === '.tif' ? '.tiff' : e
    }
    const listed = () =>
      (existsSync(exportDir) ? readdirSync(exportDir) : []).filter(
        (f) => statSync(path.join(exportDir, f)).mtimeMs >= t0,
      )
    await expect
      .poll(() => Object.keys(magic).filter((e) => !listed().some((f) => extOf(f) === e)), {
        timeout: 60_000,
        message: `这次导出应当在 ${exportDir} 写出 PDF / PNG / TIFF 三个文件`,
      })
      .toEqual([])
    for (const ext of Object.keys(magic)) {
      const names = listed().filter((f) => extOf(f) === ext)
      expect(names, `${ext} 应当恰好一个：${names.join(', ')}`).toHaveLength(1)
      const bytes = readFileSync(path.join(exportDir, names[0]))
      expect(bytes.length, `${names[0]} 太小`).toBeGreaterThan(900)
      expect(magic[ext](bytes), `${names[0]} 的文件头不是 ${ext}`).toBe(true)
    }
  },
)

test(
  '功能：命令面板 ⌘K 搜索并执行命令——加一段文字、打开快捷键帮助',
  { tag: ['@feature:command.palette'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await expect(page.locator('[data-canvas-stage]')).toBeVisible({ timeout: 30_000 })
    const objs = page.locator('[data-canvas-stage] [data-object-id]')
    const n0 = await objs.count()

    const openPalette = async () => {
      await page.keyboard.press('ControlOrMeta+k')
      const list = page.locator('[role="listbox"]:has([data-cmd-id])')
      await expect(list).toHaveCount(1)
      await expectInViewport(page, list, '命令面板')
      return list
    }

    // 搜「text」收窄到「加文字」，回车执行：画布上真的多了一个对象
    let list = await openPalette()
    await page.keyboard.type('text')
    const addText = list.locator('[data-cmd-id="add-text"]')
    await expect(addText).toHaveCount(1)
    await expect(list.locator('[data-cmd-id="export"]')).toHaveCount(0)
    await expect(addText).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('Enter')
    await expect(list).toHaveCount(0)
    await expect(objs).toHaveCount(n0 + 1)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')

    // 再开一次，点「快捷键帮助」：帮助真的打开、分组都在
    list = await openPalette()
    await page.keyboard.type('shortcut')
    const help = list.locator('[data-cmd-id="shortcut-help"]')
    await expect(help).toHaveCount(1)
    await help.click()
    await expect(page.locator('[data-shortcut-group]').first()).toBeVisible()
    await expect(page.locator('[data-shortcut-group="file"]')).toBeVisible()
  },
)

/** 画布视口（`data-canvas-stage`）的屏幕框。 */
async function stageBox(page: Page) {
  const b = await page.locator('[data-canvas-stage]').boundingBox()
  expect(b, '画布视口没有布局框').not.toBeNull()
  return b!
}

/** 排版画布上唯一那个对象：回到排版、等它出现在视口里。 */
async function toLayoutWithPanel(page: Page, baseURL: string) {
  await openFigure(page, baseURL)
  await page.locator('[data-context-back]').click()
  await expect(page.locator('[data-element-svg]')).toHaveCount(0)
  const panel = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(panel).toHaveCount(1, { timeout: 30_000 })
  await expectInViewport(page, panel, '画布上的面板')
  return panel
}

test(
  '功能：缩放到选区——⇧2、缩放菜单、命令面板都把选中对象放大到铺满画布视口',
  { tag: ['@feature:canvas.zoom-to-selection'] },
  async ({ app, page }) => {
    const a = await app()
    const panel = await toLayoutWithPanel(page, a.baseURL)
    await panel.click()
    await expect(page.locator('[data-context-bar]')).toHaveCount(1)

    const zoomBtn = page.locator('[data-zoom-menu]')
    const zoomLabel = () => zoomBtn.getAttribute('aria-label')

    /** 先缩小两档，让选中对象明显变小（每个入口都从「对象小」出发） */
    const shrink = async () => {
      const before = (await panel.boundingBox())!
      await page.keyboard.press('ControlOrMeta+-')
      await page.keyboard.press('ControlOrMeta+-')
      await expect
        .poll(async () => (await panel.boundingBox())!.width, { message: '缩小后对象应当变小' })
        .toBeLessThan(before.width * 0.9)
      const s = await stageBox(page)
      const b = (await panel.boundingBox())!
      // 出发点：对象只占视口的一小块
      expect(Math.max(b.width / s.width, b.height / s.height)).toBeLessThan(0.6)
      return { box: b, label: await zoomLabel() }
    }

    /** 缩放到选区之后：读数换了；对象整个框在画布视口里，并铺满其中一个方向的大半 */
    const expectFilled = async (from: { box: { width: number }; label: string | null }, via: string) => {
      await expect
        .poll(
          async () => {
            const s = await stageBox(page)
            const b = (await panel.boundingBox())!
            const fill = Math.max(b.width / s.width, b.height / s.height)
            const inside =
              b.x >= s.x - 0.5 &&
              b.y >= s.y - 0.5 &&
              b.x + b.width <= s.x + s.width + 0.5 &&
              b.y + b.height <= s.y + s.height + 0.5
            return inside && fill > 0.7 && b.width > from.box.width * 1.3 ? 'ok' : `fill=${fill.toFixed(2)} inside=${inside}`
          },
          { timeout: 10_000, message: `${via}：选中对象应当放大到铺满画布视口、且整个在视口里` },
        )
        .toBe('ok')
      await expect.poll(zoomLabel, { message: `${via}：缩放读数应当变了` }).not.toBe(from.label)
      await expectInViewport(page, panel, `${via} 后的选中对象`)
      // 选区没丢
      await expect(page.locator('[data-context-bar]')).toHaveCount(1)
    }

    // ── ⇧2 ──
    let from = await shrink()
    await page.keyboard.press('Shift+Digit2')
    await expectFilled(from, '⇧2')

    // ── 缩放菜单「缩放到选中」 ──
    from = await shrink()
    await zoomBtn.click()
    const item = page.locator('[data-zoom-selection-item]')
    await expectInViewport(page, item, '缩放菜单里的「缩放到选中」')
    await expectHittable(item, '「缩放到选中」')
    await expect(item).not.toHaveAttribute('data-disabled')
    await item.click()
    await expect(item).toHaveCount(0)
    await expectFilled(from, '缩放菜单')

    // ── 命令面板 zoom-selection ──
    from = await shrink()
    await page.keyboard.press('ControlOrMeta+k')
    const list = page.locator('[role="listbox"]:has([data-cmd-id])')
    await expect(list).toHaveCount(1)
    await page.keyboard.type('zoom')
    const cmd = list.locator('[data-cmd-id="zoom-selection"]')
    await expectInViewport(page, cmd, '命令面板里的「缩放到选中」')
    await cmd.click()
    await expect(list).toHaveCount(0)
    await expectFilled(from, '命令面板')
  },
)

test(
  '功能：空白画布右键菜单——在视口内，「全选」选中对象，「标尺」开关真的收起 / 放出标尺',
  { tag: ['@feature:canvas.empty-menu'] },
  async ({ app, page }) => {
    const a = await app()
    const panel = await toLayoutWithPanel(page, a.baseURL)
    // 回到排版时面板仍是选中的：先清掉选区，「全选」才看得出效果
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-context-bar]')).toHaveCount(0)

    /** 画布视口里一块空白处（最上层不是任何对象，且就在画布视口里） */
    const emptyPoint = async () => {
      const s = await stageBox(page)
      const pt = await page.evaluate(
        ({ x0, y0, w, h }) => {
          for (let fy = 0.06; fy < 0.95; fy += 0.04) {
            for (let fx = 0.06; fx < 0.95; fx += 0.04) {
              const x = x0 + w * fx
              const y = y0 + h * fy
              const top = document.elementFromPoint(x, y)
              const stage = document.querySelector('[data-canvas-stage]')!
              // 选中框的手柄 / 沿边命中带 / 端点右键开的是对象菜单（Codex #833），不算空白
              const onHandle = top?.closest('[data-handle],[data-edge-strip],[data-endpoint]')
              if (top && stage.contains(top) && !top.closest('[data-object-id]') && !top.closest('[data-context-bar]') && !onHandle) {
                return { x, y }
              }
            }
          }
          return null
        },
        { x0: s.x, y0: s.y, w: s.width, h: s.height },
      )
      expect(pt, '画布上找不到空白处').not.toBeNull()
      return pt!
    }
    const openMenu = async () => {
      const p = await emptyPoint()
      await page.mouse.click(p.x, p.y, { button: 'right' })
      const menu = page.locator('[data-canvas-menu]')
      await expect(menu).toHaveCount(1)
      await expectInViewport(page, menu, '空白画布右键菜单')
      return menu
    }

    // ── 全选：对象真的被选中（单选浮动栏出现） ──
    let menu = await openMenu()
    const selectAll = menu.locator('[data-canvas-menu-item="select-all"]')
    await expectInViewport(page, selectAll, '「全选」')
    await expectHittable(selectAll, '「全选」')
    await selectAll.click()
    await expect(menu).toHaveCount(0)
    await expect(page.locator('[data-context-bar]')).toHaveCount(1)
    await expectInViewport(page, page.locator('[data-context-bar]'), '全选后的浮动栏')
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-context-bar]')).toHaveCount(0)

    // ── 标尺开关：默认开着；关掉后单位角消失、画布视口左上角退回到边上；再开回来 ──
    const unit = page.locator('[data-ruler-unit]')
    await expect(unit).toBeVisible()
    const s0 = await stageBox(page)
    const panel0 = (await panel.boundingBox())!
    menu = await openMenu()
    const rulers = menu.locator('[data-canvas-menu-item="rulers"]')
    await expectInViewport(page, rulers, '「标尺」开关')
    await expectHittable(rulers, '「标尺」开关')
    await expect(rulers).toHaveAttribute('aria-checked', 'true')
    await rulers.click()
    await expect(unit).toHaveCount(0)
    await expect(rulers).toHaveAttribute('aria-checked', 'false')
    await expect
      .poll(async () => {
        const s = await stageBox(page)
        return s.x < s0.x - 10 && s.y < s0.y - 10 && s.width > s0.width + 10
      }, { message: '收起标尺后画布视口应当占回标尺那一条' })
      .toBe(true)
    // 开关项选了不关菜单：再点一次放回来
    await rulers.click()
    await expect(unit).toBeVisible()
    await expect(rulers).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)
    await expect
      .poll(async () => {
        const s = await stageBox(page)
        return Math.abs(s.x - s0.x) < 0.5 && Math.abs(s.y - s0.y) < 0.5
      })
      .toBe(true)
    // 菜单不是对象的右键菜单：面板没被动过
    const panel1 = (await panel.boundingBox())!
    expect(Math.abs(panel1.width - panel0.width)).toBeLessThan(1)
  },
)

test(
  '功能：设置里把界面语言切成英文，整页当场换语言，刷新后仍是英文',
  { tag: ['@feature:i18n.language'] },
  async ({ app, page }) => {
    const a = await app()
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN', { timeout: 30_000 })

    await page.locator('[data-rail="settings"]').click()
    const shell = page.locator('[data-settings-shell]')
    await expect(shell).toBeVisible()
    await shell.locator('[data-section="general"]').click()
    // 语言下拉没有 data 锚点；它是「常规」分区里的一个组合框，可达名按当前语言认
    const lang = page.locator('[data-settings-content]').getByRole('combobox', { name: '语言' })
    await expectInViewport(page, lang, '语言下拉')
    await lang.click()
    // 选项名是各语言的自称（LOCALE_LABELS），不随界面语言变
    await page.getByRole('option', { name: 'English' }).click()

    // 当场生效：<html lang> 与设置里的分区名一起换成英文
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US')
    await expect(shell.locator('[data-section="general"]')).toContainText('General')
    await page.keyboard.press('Escape')

    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US', { timeout: 30_000 })
    expect(await page.evaluate(() => localStorage.getItem('tavotto.locale'))).toBe('en-US')
    await page.locator('[data-rail="settings"]').click()
    await expect(page.locator('[data-settings-shell] [data-section="general"]')).toContainText('General')
  },
)

/**
 * 一张两联图：(a)(b) 左右并排，面板标签写在各自坐标系里（`ax.text(0, 1.03, "(a)")`），
 * 每个子图里各有一段 6 pt 的注释——低于绝对下限 8 pt，两个子图各一条阻断。
 */
function writeTwoPanelProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-twopanel-')), 'figures')
  mkdirSync(dir, { recursive: true })
  const script = [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    'from pathlib import Path',
    '',
    '',
    'def main():',
    '    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(6, 2.6))',
    '    for ax, tag in ((ax_a, "(a)"), (ax_b, "(b)")):',
    '        ax.plot([0, 1, 2, 3], [1, 3, 2, 4])',
    '        ax.text(0, 1.03, tag, transform=ax.transAxes, fontsize=9, fontweight="bold")',
    '        ax.text(0.5, 0.5, "tiny " + tag, transform=ax.transAxes, fontsize=6)',
    '    fig.tight_layout()',
    '    fig.savefig(Path(__file__).with_name("Fig_two.pdf"))',
    '    plt.close(fig)',
    '',
    '',
    'if __name__ == "__main__":',
    '    main()',
    '',
  ].join('\n')
  writeFileSync(path.join(dir, 'fig_two.py'), script, 'utf-8')
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ version: 1, scripts: { 'fig_two.py': { entry: 'main', cost: 'light', stems: ['Fig_two'] } } }),
    'utf-8',
  )
  // 占位 PDF：打开时引擎按脚本重画，不需要跑测试的机器另装 matplotlib 去烤它（同 twin-axes-pick）
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_two.pdf'),
  )
  return dir
}

test(
  '功能：问题面板把组图拆成子图卡片，卡片上的「修复」只改那一个子图',
  { tag: ['@feature:problems.subplot-cards'] },
  async ({ app, page }) => {
    const a = await app({ figures: writeTwoPanelProject() })
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig_two.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
    const key0 = await waitExact(page)
    const tinyA0 = (await gidBox(page, 'axes_0.texts_1'))!
    const tinyB0 = (await gidBox(page, 'axes_1.texts_1'))!
    expect(tinyA0, '(a) 里应当有那段 6 pt 注释').not.toBeNull()
    expect(tinyB0, '(b) 里应当有那段 6 pt 注释').not.toBeNull()

    const rail = page.locator('[data-rail="problems"]')
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    // 卡片的主语是图里写着的面板标签（lib/subplotParts.ts），不是「子图 1」
    const card = (tag: string) =>
      page.locator('li[data-problem-card="part"]').filter({ hasText: `子图 ${tag}` })
    for (const tag of ['(a)', '(b)']) {
      await expect(card(tag), `应当有「子图 ${tag}」这张卡片`).toHaveCount(1, { timeout: 60_000 })
      await expect(card(tag)).toHaveAttribute('data-problem-card-rules', /font-below-absolute-floor/)
      await expectInViewport(page, card(tag), `「子图 ${tag}」卡片`)
    }
    // 卡片层不铺逐条清单
    await expect(page.locator('[data-issue-row]')).toHaveCount(0)

    const fixB = card('(b)').getByRole('button', { name: /^修复 \d+$/ })
    await expectInViewport(page, fixB, '「子图 (b)」的修复按钮')
    await expectHittable(fixB, '「子图 (b)」的修复按钮')
    await fixB.click()
    await waitExact(page, key0)

    // 画面真的变了：(b) 那段字被提到下限以上、变高；(a) 那段一个像素没动
    await expect
      .poll(async () => (await gidBox(page, 'axes_1.texts_1'))!.h, {
        message: '修复 (b) 之后，(b) 里那段小字应当变高',
        timeout: 60_000,
      })
      .toBeGreaterThan(tinyB0.h * 1.2)
    const tinyA1 = (await gidBox(page, 'axes_0.texts_1'))!
    expect(Math.abs(tinyA1.h - tinyA0.h), '(a) 不在这次修复的范围里，它的字不该变').toBeLessThan(0.5)
    // 清单跟着变：(b) 不再有字号阻断，(a) 的还在
    await expect(card('(a)')).toHaveAttribute('data-problem-card-rules', /font-below-absolute-floor/)
    await expect
      .poll(async () => ((await card('(b)').count()) ? await card('(b)').getAttribute('data-problem-card-rules') : ''), {
        timeout: 60_000,
      })
      .not.toMatch(/font-below-absolute-floor/)
  },
)

test(
  '功能：问题面板「⋯」打开画布上的问题标记，点标记直达那条问题，关掉后标记消失',
  { tag: ['@feature:problems.canvas-pins'] },
  async ({ app, page }) => {
    const a = await app({ figures: writeTwoPanelProject() })
    await page.setViewportSize(VIEWPORT)
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig_two.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
    await waitExact(page)

    const rail = page.locator('[data-rail="problems"]')
    if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
    await expect(page.locator('li[data-problem-card="part"]').first()).toBeVisible({ timeout: 60_000 })
    // 起点：卡片层，没有逐条清单、没有游标
    await expect(page.locator('[data-issue-row]')).toHaveCount(0)
    await expect(page.locator('[data-problem-cursor]')).toHaveCount(0)

    const pins = page.locator('[data-issue-pin]')
    await expect(pins, '默认不画标记').toHaveCount(0)
    const menu = page.locator('[data-problem-menu]')
    const pinsItem = page.locator('[data-problem-pins]')
    await menu.click()
    await expectInViewport(page, pinsItem, '「⋯」里的画布标记开关')
    await expectHittable(pinsItem, '「⋯」里的画布标记开关')
    await pinsItem.click()
    await expect(pinsItem).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('Escape')
    await expect(pinsItem).toHaveCount(0)

    // 标记画在对象右上角；这张图在 109% 下右缘伸进浮动的右侧面板底下——先收起右侧面板，
    // 让标记露出来（用户也是这么做的），再断言它点得到
    const inspectorClose = page.locator('[data-inspector-close]')
    if (await inspectorClose.count()) await inspectorClose.click()
    // 这一屏只有那张两联图一个对象 → 恰好一枚标记，等级是阻断
    await expect(pins, '打开之后那张有问题的图上应当有一枚标记').toHaveCount(1, { timeout: 30_000 })
    const pin = pins.first()
    await expect(pin).toHaveAttribute('data-issue-pin-severity', 'error')
    await expectInViewport(page, pin, '画布上的问题标记')
    await expectHittable(pin, '画布上的问题标记')
    await pin.click()

    // 画面真的变了：问题面板点开了那条问题所在的卡片、游标落在一行上
    const current = page.locator('[data-issue-row][aria-current="true"]')
    await expect(current, '点标记之后问题面板应当有一行是当前项').toHaveCount(1, { timeout: 30_000 })
    await expect(current).toHaveAttribute('data-issue-rule', /font-below-absolute-floor/)
    await expectInViewport(page, current, '当前那一行问题')
    await expect(page.locator('[data-problem-cursor]')).toBeVisible()

    // 关掉：标记消失
    await menu.click()
    await expect(pinsItem).toHaveAttribute('aria-checked', 'true')
    await pinsItem.click()
    await expect(pinsItem).toHaveAttribute('aria-checked', 'false')
    await page.keyboard.press('Escape')
    await expect(pins, '关掉之后画布上不应再有标记').toHaveCount(0)
  },
)
