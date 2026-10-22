import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { addSecondFigure, expect, test } from './fixtures'

/**
 * 新手黄金路径（渐进式编辑 P06）：普通编辑 → 排版往返 → 出版规范检查，三条各走一遍真实界面，
 * 断言落在稳定的 `data-*` 锚点与磁盘上的产物文件上，**不认任何显示文案**（chromium 与 chromium-en 同一份）。
 *
 *   A 打开 → 首次渲染 → 双击进入编辑 → 改标题 → 撤销 / 重做 → 原图导出 → 产物尺寸与原图规格一致
 *   B 快编 → 回排版（对象不重复）→ 加第二张图 → 选两张 → 对齐 → 按画布导出 → 再进快编再回排版：文档逐项不变
 *   C 排版里选页面预设 + 出版规范 → 导出检查摘要 → 按画布导出并核对真实尺寸
 *
 * 模式由画布上的图数推出：一张图 = 单图编辑，第二张进来才是多图排版（`addSecondFigure`，认 `[data-card-add]`，不认文案）；
 * `[data-context-back]` 只是「退出图内编辑」的出口，不再是进排版的路。
 */

const VIEWPORT = { width: 1440, height: 900 }
const CARD = (name: string) => `[data-card="${name}"]`
const EXPORT_URL = (r: { url(): string; request(): { method(): string } }) =>
  new URL(r.url()).pathname === '/api/export/start' && r.request().method() === 'POST'

test.beforeEach(async ({ page }) => {
  // 吸附会有意把落点拽到对齐线上，与本文件量的东西正交
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

/** 双击素材卡 = 打开（快速编辑），等引擎精确图画出来 */
async function openFastEdit(page: Page, baseURL: string, card = 'Fig1_kinetics.pdf') {
  await page.setViewportSize(VIEWPORT)
  await page.goto(baseURL)
  await page.locator(CARD(card)).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await waitExact(page)
}

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

function objectBoxes(page: Page) {
  return page.locator('[data-canvas-stage] [data-object-id]').evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect()
      return { id: e.getAttribute('data-object-id'), x: r.x, y: r.y, w: r.width, h: r.height }
    }),
  )
}

/** 左栏轨道是开关：按 aria-expanded 判，不在才点（同 fixtures.openElementsTab 的纪律） */
async function openRail(page: Page, id: string) {
  const nav = page.locator(`[data-rail="${id}"]`)
  await expect(nav).toHaveCount(1)
  await expect(async () => {
    if ((await nav.getAttribute('aria-expanded')) !== 'true') await nav.click()
    await expect(nav).toHaveAttribute('aria-expanded', 'true', { timeout: 3_000 })
  }).toPass({ timeout: 30_000 })
}

/** 退出图内编辑（唯一出口锚点；单图编辑里仍是单图编辑，多图排版里回到排版画布） */
async function backToLayout(page: Page) {
  await page.locator('[data-context-back]').first().click()
  await expect(page.locator('[data-element-svg]')).toHaveCount(0)
}

/** 打开导出对话框，只勾给定格式，知情确认（若有）勾上，点开始；返回导出目录与请求体 */
async function exportWith(page: Page, formats: string[], filename: string) {
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  for (const f of ['pdf', 'png', 'eps', 'tiff', 'svg']) {
    const box = page.locator(`[data-export-format="${f}"] input`)
    if ((await box.count()) === 0) continue
    const want = formats.includes(f)
    if ((await box.isChecked()) !== want) {
      if (want) await box.check()
      else await box.uncheck()
    }
  }
  await page.locator('#export-filename').fill(filename)
  const confirm = page.locator('input[data-export-confirm]')
  if (await confirm.count()) await confirm.check()
  const start = page.locator('[data-export-start]')
  await expect(start).toBeEnabled()
  const started = page.waitForResponse(EXPORT_URL)
  await start.click()
  const response = await started
  expect(response.ok()).toBe(true)
  const request = response.request().postDataJSON() as {
    scope: string
    formats: string[]
    ppi: number | null
    canvas?: { page_w_mm: number; page_h_mm: number }
  }
  const job = (await response.json()) as { export_dir: string }
  await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 240_000 })
  await expect(page.locator('[data-export-failed]')).toHaveCount(0)
  return { dir: job.export_dir, request, dialog }
}

async function artifact(dir: string, file: string): Promise<Buffer> {
  const p = path.join(dir, file)
  await expect.poll(() => existsSync(p), { timeout: 60_000, message: `${p} 应当落盘` }).toBe(true)
  return readFileSync(p)
}

/** PNG IHDR 的像素宽高 */
function pngSize(b: Buffer): { w: number; h: number } {
  expect(b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe(true)
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) }
}

/** PDF 第一个 MediaBox（pt）。matplotlib 与导出器都把它写成明文 */
function mediaBoxPt(b: Buffer): { w: number; h: number } {
  const m = /\/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]/.exec(b.toString('latin1'))
  expect(m, 'PDF 里应当有明文 MediaBox').not.toBeNull()
  return { w: Number(m![3]) - Number(m![1]), h: Number(m![4]) - Number(m![2]) }
}
const ptToMm = (pt: number) => (pt * 25.4) / 72

/** 磁盘上的自动保存文档（真实后端回读，不是前端状态）：等对象数到位再取 */
async function savedDoc(page: Page, baseURL: string, docId: string, objects: number) {
  let doc: { canvases: { objects: Record<string, unknown>[] }[] } | undefined
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`${baseURL}/api/autosave/${encodeURIComponent(docId)}`)
        if (!r.ok()) return -1
        const body = await r.json()
        doc = body.doc ?? body
        return (doc!.canvases ?? []).flatMap((c) => c.objects).length
      },
      { timeout: 30_000, message: '自动保存的文档应当有 ' + objects + ' 个对象' },
    )
    .toBe(objects)
  return doc!
}

/** 落盘文档稳定：连续三次回读（间隔 0.8 s）完全一致才算——渲染回来的图幅修正、对齐的写入都是异步落盘的 */
async function settledObjects(page: Page, baseURL: string, docId: string, objects: number) {
  const pick = (doc: Awaited<ReturnType<typeof savedDoc>>) =>
    doc.canvases.flatMap((c) => c.objects).map((o) => {
      const { id, x, y, w, h, overrides } = o as Record<string, unknown>
      return { id, x, y, w, h, overrides }
    })
  let last = ''
  let same = 0
  let snap: ReturnType<typeof pick> = []
  await expect
    .poll(
      async () => {
        snap = pick(await savedDoc(page, baseURL, docId, objects))
        const now = JSON.stringify(snap)
        same = now === last ? same + 1 : 0
        last = now
        return same
      },
      { timeout: 60_000, intervals: [800], message: '落盘文档一直在变，没有稳定下来' },
    )
    .toBeGreaterThanOrEqual(3)
  return snap
}

function autosaveDocId(page: Page): Promise<string> {
  return page
    .waitForResponse((r) => r.request().method() === 'PUT' && /^\/api\/autosave\/[^/]+$/.test(new URL(r.url()).pathname))
    .then((r) => decodeURIComponent(new URL(r.url()).pathname.split('/').pop()!))
}

test('A：打开示例 → 首次渲染 → 双击进入编辑 → 改标题 → 撤销 / 重做 → 原图导出，产物尺寸与原图规格一致', async ({ app, page }) => {
  test.setTimeout(300_000)
  const a = await app()
  // 引擎每次渲染回来的图幅（manifest size_mm）：「原图规格」第一档就是它（ADR 0028），
  // 比对的锚点是引擎自己渲出来的图幅，不是仓库里随附的 PDF 文件——后者是别的平台 / 字体栈
  // 上生成的，Linux 上 tight bbox 的图幅可以差 1–2 mm（合并队列 posix-e2e 实测 75.13 vs 73.33）。
  const renderedSizes: Array<[number, number]> = []
  page.on('response', async (res) => {
    if (!res.url().includes('/api/engine/render') || res.request().method() !== 'POST') return
    try {
      const m = /"size_mm":\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]/.exec(await res.text())
      if (m) renderedSizes.push([Number(m[1]), Number(m[2])])
    } catch {
      /* 页面关闭时 body 取不到，忽略 */
    }
  })
  await openFastEdit(page, a.baseURL)

  // 首次渲染：精确图里是真内容，标题在
  const h0 = (await gidBox(page, 'axes_0.title'))!
  expect(h0, '示例图应当有标题').not.toBeNull()
  expect(h0.w).toBeGreaterThan(0)

  // 改标题字号（真实元素）
  await page.mouse.click(h0.x + h0.w / 2, h0.y + h0.h / 2)
  const size = page.locator('[data-prop="fontsize"] input')
  await expect(size).toHaveCount(1, { timeout: 15_000 })
  const v0 = Number(await size.inputValue())
  expect(v0).toBeGreaterThan(0)
  const key0 = await waitExact(page)
  await size.fill(String(v0 + 8))
  await size.press('Enter')
  const key1 = await waitExact(page, key0)
  const h1 = (await gidBox(page, 'axes_0.title'))!
  expect(h1.h).toBeGreaterThan(h0.h * 1.2)

  // 撤销 → 回到原高；重做 → 回到改后的高
  await page.locator('[data-canvas-stage]').focus()
  await page.keyboard.press('ControlOrMeta+z')
  const key2 = await waitExact(page, key1)
  await expect.poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h0.h)).toBeLessThan(0.5)
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await waitExact(page, key2)
  await expect.poll(async () => Math.abs((await gidBox(page, 'axes_0.title'))!.h - h1.h)).toBeLessThan(0.5)

  // 原图导出：快速编辑下默认范围就是原图
  const out = await exportWith(page, ['pdf', 'png'], `golden-a-${Date.now()}`)
  expect(out.request.scope).toBe('original')
  const name = (ext: string) => readdirSync(out.dir).find((f) => f.startsWith('golden-a-') && f.endsWith(ext))!
  await expect.poll(() => readdirSync(out.dir).some((f) => f.startsWith('golden-a-') && f.endsWith('.png'))).toBe(true)
  const pdf = await artifact(out.dir, name('.pdf'))
  const png = await artifact(out.dir, name('.png'))
  expect(pdf.subarray(0, 5).toString('latin1')).toBe('%PDF-')

  // 原图规格：请求里带的 original.w_mm/h_mm 必须是引擎最近一次渲回来的图幅（不被画布变换带偏、
  // 不是编出来的占位值 80×60），导出的 PDF 页面与位图像素再与它逐项对上
  const spec = (out.request as unknown as { original: { w_mm: number; h_mm: number } }).original
  expect(renderedSizes.length, '应当捕获到引擎渲染回来的图幅').toBeGreaterThan(0)
  const [rw, rh] = renderedSizes[renderedSizes.length - 1]
  expect(Math.abs(spec.w_mm - rw), '原图规格与引擎渲染图幅的宽').toBeLessThan(0.05)
  expect(Math.abs(spec.h_mm - rh), '原图规格与引擎渲染图幅的高').toBeLessThan(0.05)
  const exported = mediaBoxPt(pdf)
  expect(Math.abs(ptToMm(exported.w) - spec.w_mm)).toBeLessThan(0.05)
  expect(Math.abs(ptToMm(exported.h) - spec.h_mm)).toBeLessThan(0.05)
  const ppi = out.request.ppi!
  expect(ppi).toBeGreaterThan(0)
  const px = pngSize(png)
  expect(Math.abs(px.w - (spec.w_mm / 25.4) * ppi)).toBeLessThanOrEqual(1.5)
  expect(Math.abs(px.h - (spec.h_mm / 25.4) * ppi)).toBeLessThanOrEqual(1.5)
  expect(statSync(path.join(out.dir, name('.pdf'))).size).toBeGreaterThan(900)
})

test('B：快编 → 回排版（图不重复）→ 加第二张图 → 选两张 → 对齐 → 按画布导出 → 再进快编再回排版，文档逐项不变', async ({ app, page }) => {
  test.setTimeout(420_000)
  const a = await app()
  const docIdP = autosaveDocId(page)
  await openFastEdit(page, a.baseURL)

  // 快编里改一处，确保文档里有一条真实 override 要在往返里保持
  const t = (await gidBox(page, 'axes_0.title'))!
  await page.mouse.click(t.x + t.w / 2, t.y + t.h / 2)
  const size = page.locator('[data-prop="fontsize"] input')
  await expect(size).toHaveCount(1, { timeout: 15_000 })
  const key0 = await waitExact(page)
  await size.fill(String(Number(await size.inputValue()) + 2))
  await size.press('Enter')
  await waitExact(page, key0)

  // 退出图内编辑：画布上已有这张图，对象数不变（1，不重复创建），仍是单图编辑
  await backToLayout(page)
  const objects = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(objects).toHaveCount(1, { timeout: 30_000 })
  const firstId = await objects.first().getAttribute('data-object-id')
  const docId = await docIdP
  await savedDoc(page, a.baseURL, docId, 1)

  // 加第二张图：「添加到画布」第二张进来 = 多图排版
  await addSecondFigure(page, 'Fig2_yield.pdf')
  await expect(objects).toHaveCount(2, { timeout: 30_000 })
  // 第二张图还没渲染过：渲染回来会按真实图幅校正它的 w / h（文档里的合法修正）。先把它打开渲染一遍再退出，
  // 让「往返不改文档」的快照取在校正之后（否则校正落在往返里，被误当成往返改了文档）
  await openRail(page, 'assets')
  await page.locator(CARD('Fig2_yield.pdf')).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await page.waitForTimeout(1500) // 两张图在场，舞台上不止一个 [data-display]：不用 waitExact 的单例判据，等渲染校正落盘
  await backToLayout(page)
  await expect(objects).toHaveCount(2, { timeout: 30_000 })
  expect(await objects.evaluateAll((els) => els.map((e) => e.getAttribute('data-object-id')))).toContain(firstId)

  // 选两张 → 左对齐
  await page.locator('[data-canvas-stage]').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('ControlOrMeta+a')
  const bar = page.locator('[data-multi-selection-context-bar]')
  await expect(bar).toHaveCount(1)
  await expect(bar.locator('[data-selection-count]')).toHaveAttribute('data-selection-count', '2')
  let left = bar.locator('[data-align-mode="left"]')
  if (!(await left.count())) {
    await bar.locator('[data-multi-menu="align"]').click()
    left = page.locator('[data-align-mode="left"]')
  }
  await left.click()
  await expect
    .poll(async () => new Set((await objectBoxes(page)).map((b) => Math.round(b.x))).size, {
      message: '左对齐之后两张图的左边应当是同一条线',
    })
    .toBe(1)

  // 按画布导出：范围是 canvas，页面尺寸与 PNG 像素一致
  await page.keyboard.press('Escape')
  const out = await exportWith(page, ['png'], `golden-b-${Date.now()}`)
  expect(out.request.scope).toBe('canvas')
  const png = pngSize(await artifact(out.dir, readdirSync(out.dir).find((f) => f.startsWith('golden-b-') && f.endsWith('.png')) ?? 'missing.png'))
  const ppi = out.request.ppi!
  expect(Math.abs(png.w - (out.request.canvas!.page_w_mm / 25.4) * ppi)).toBeLessThanOrEqual(1.5)
  expect(Math.abs(png.h - (out.request.canvas!.page_h_mm / 25.4) * ppi)).toBeLessThanOrEqual(1.5)
  await page.locator('[data-export-finish]').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // 快照 S1（磁盘上的自动保存）：两个对象，id 唯一
  const before = await settledObjects(page, a.baseURL, docId, 2)
  expect(new Set(before.map((o) => o.id)).size).toBe(2)
  expect(before.some((o) => Array.isArray(o.overrides) && o.overrides.length > 0), '快编里的修改应当在 overrides 里').toBe(true)

  // 再进图内编辑（素材卡双击 = 打开；文档里已有这张图就只是聚焦，不会再建一个；两张图 = 排版里的子状态）再退出：
  // 对象数、id、x/y/w/h、overrides 逐项不变
  await openRail(page, 'assets')
  await page.locator(CARD('Fig1_kinetics.pdf')).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-workspace-context-bar]')).toHaveAttribute('data-workspace-mode', 'layout', { timeout: 60_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  // 文档里有两个面板，此刻舞台上不止一个 [data-display]：认模式锚点，不用 waitExact 的单例判据
  await backToLayout(page)
  await expect(objects).toHaveCount(2, { timeout: 30_000 })
  await page.waitForTimeout(1500) // 给自动保存留出写入窗口：若往返偷偷改了文档，这里会写出来
  expect(await settledObjects(page, a.baseURL, docId, 2)).toEqual(before)
})

test('C：排版里选单栏页宽 + 出版规范 → 导出检查摘要 → 定位真实问题 → 按画布导出，真实尺寸与页宽一致', async ({ app, page }) => {
  test.setTimeout(420_000)
  const a = await app()
  await page.setViewportSize(VIEWPORT)
  await page.goto(a.baseURL)
  await expect(page.locator('[data-canvas-stage]')).toHaveAttribute('data-document-state', 'ready', { timeout: 60_000 })

  // 右栏「画布」页：页面预设下拉里选「单栏」（宽度来自当前绑定的出版规范 widths_mm.single，不是写死的常量）
  await page.locator('[data-inspector-tab="canvas"]').click()
  const inspector = page.locator('[data-inspector-panel]')
  await inspector.getByRole('combobox').first().click()
  await page.locator('[data-page-preset="single"]').click()
  const specs = (await (await page.request.get(`${a.baseURL}/api/profiles/spec`)).json()) as {
    profiles: { data: { widths_mm: { single: number } } }[]
  }
  const singleMm = specs.profiles[0].data.widths_mm.single
  expect(singleMm).toBeGreaterThan(0)
  await expect(inspector.locator('[data-page-size-row] input').first()).toHaveValue(String(singleMm))

  // 空画布：没有可查的内容，摘要是「按规范未发现问题」+ 覆盖说明；规范下拉在，且至少有一套可选
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('[data-export-check-ok]')).toBeVisible({ timeout: 60_000 })
  await expect(dialog.locator('[data-export-check-coverage]')).toBeVisible()
  const profile = dialog.locator('[data-export-group="checks"]').getByRole('combobox')
  await expect(profile).toHaveCount(1)
  await profile.click()
  expect(await page.getByRole('option').count(), '至少有一套可选的出版规范').toBeGreaterThanOrEqual(1)
  await page.keyboard.press('Escape') // 关下拉
  await expect(profile).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Escape') // 关对话框
  await expect(page.locator('[data-dialog-scrim]')).toHaveCount(0)

  // 放进两张图（素材卡双击 = 打开第一张；再「添加到画布」第二张 = 多图排版，才有页面与按画布导出），页宽仍是单栏
  await openRail(page, 'assets')
  await page.locator(CARD('Fig1_kinetics.pdf')).dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await waitExact(page)
  await backToLayout(page)
  await addSecondFigure(page, 'Fig2_yield.pdf')
  const objects = page.locator('[data-canvas-stage] [data-object-id]')
  await expect(objects).toHaveCount(2, { timeout: 30_000 })

  // 检查摘要：这张图在单栏页里有真实问题——计数芯片出现、不再是「未发现问题」，阻断项逐条可定位
  await page.keyboard.press('ControlOrMeta+e')
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('[data-count-chip="danger"]')).toHaveCount(1, { timeout: 60_000 })
  await expect(dialog.locator('[data-export-check-ok]')).toHaveCount(0)
  const rows = dialog.locator('[data-blocking-issue]')
  expect(await rows.count()).toBeGreaterThanOrEqual(1)

  // 定位第一条：对话框让开，工作区真的去了那个问题的位置（两张图 = 排版里的图内编辑）
  await rows.first().locator('button').click()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('[data-workspace-context-bar]')).toHaveAttribute('data-workspace-mode', 'layout', { timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })

  // 回排版导出（知情确认后）：按画布导出，请求里的页宽就是规范的单栏宽，落盘位图像素与之一致
  await backToLayout(page)
  await expect(objects).toHaveCount(2)
  const out = await exportWith(page, ['png'], `golden-c-${Date.now()}`)
  expect(out.request.scope).toBe('canvas')
  expect(out.request.canvas!.page_w_mm).toBeCloseTo(singleMm, 3)
  const file = readdirSync(out.dir).find((f) => f.startsWith('golden-c-') && f.endsWith('.png'))
  const png = pngSize(await artifact(out.dir, file ?? 'missing.png'))
  const ppi = out.request.ppi!
  expect(Math.abs(png.w - (singleMm / 25.4) * ppi), '位图宽度 = 单栏宽 × ppi').toBeLessThanOrEqual(1.5)
  expect(Math.abs(png.h - (out.request.canvas!.page_h_mm / 25.4) * ppi)).toBeLessThanOrEqual(1.5)
})
