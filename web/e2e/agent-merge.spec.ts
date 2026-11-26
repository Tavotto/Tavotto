/**
 * 「把 Agent 加的并进来」（ADR 0208 §五、第 2 期 ③）的真浏览器全程，真后端、真引擎。维护者 2026-11-25：只多了 Agent 加的标注时
 * 没有要用户裁决的事，**不提示**——
 *
 * 项目里有一份排版 → 桌面从「项目里的排版」打开它（绑定文件、记下基线）→ 插件那条引擎路往同一个文件里追加标注
 * （按引擎写入的格式直接写文件，见 `agentAnnotates`）→ 窗口回到前台：标注**自动并进画布**（状态栏一句、可撤销）→
 * 桌面这边改一处 → Agent 又加一批 → 直接 ⌘S：**不弹对话框**，并进来接着保存，磁盘上三处都在、圆点灭。
 *
 * 判据量的是**磁盘上那个文件**。
 */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

const NAME = '组图'

function writeLayout(figures: string): string {
  const dir = path.join(figures, 'tavottofile')
  mkdirSync(dir, { recursive: true })
  const file = path.join(dir, `${NAME}.json`)
  const doc = {
    schema: 3,
    project: { id: 'p_e2emerge', name: NAME },
    canvases: [
      {
        id: 'c_e2emerge',
        name: 'Figure 1',
        page: { w: 180, h: 100 },
        objects: [
          {
            id: 'Fig1_kinetics.pdf',
            type: 'panel',
            x: 10,
            y: 15,
            // 写成 PDF 的实际尺寸：写错了，图第一次构建完桌面会把子图校正过来（一次真实改动）
            w: 75.13,
            h: 58.54,
            fileId: 'Fig1_kinetics.pdf',
            fileKind: 'pdf',
            nativeW: 75.13,
            nativeH: 58.54,
            overrides: [],
          },
        ],
        guides: [],
      },
    ],
    activeCanvasId: 'c_e2emerge',
    createdAt: 0,
    updatedAt: 0,
  }
  writeFileSync(file, JSON.stringify(doc, null, 1), 'utf-8')
  return file
}

/**
 * 替插件往项目排版里追加一批标注——**按引擎写入的格式**直接写文件（不起 Python，Windows 打包冒烟上也能跑）：
 * 先在数据目录 `state/agent-undo/<项目键>/<排版名>/` 存写前字节与 `{before, after}`，再换排版文件。格式与
 * `engine/layoutannotate.write_canvas` / `_undo_dir` 同一份（修订号 = sha256 前 32 位；项目键 = 真实路径在
 * 大小写不敏感的卷上小写后的 sha256 前 16 位）；引擎改了格式这条会红，桌面就认不出插件写的了。
 */
function caseInsensitive(p: string): boolean {
  const base = path.basename(p)
  const flipped = [...base].map((c) => (c === c.toLowerCase() ? c.toUpperCase() : c.toLowerCase())).join('')
  if (flipped === base) return process.platform !== 'linux'
  try {
    return statSync(path.join(path.dirname(p), flipped)).ino === statSync(p).ino
  } catch {
    return false
  }
}

function agentAnnotates(figures: string, dataDir: string, batch = 1): { added: string[] } {
  const file = path.join(figures, 'tavottofile', `${NAME}.json`)
  const before = readFileSync(file)
  const rev = (b: Buffer | string) => createHash('sha256').update(b).digest('hex').slice(0, 32)
  const doc = JSON.parse(before.toString('utf-8'))
  const dy = (batch - 1) * 12
  const anchor = { v: 1, by: 'agent', anchors: [{ role: 'at', panel: { id: 'Fig1_kinetics.pdf', point: 'ne' } }] }
  const ids = [`a_e2e${batch}arw`, `t_e2e${batch}txt`]
  doc.canvases[0].objects.push(
    {
      id: ids[0], type: 'arrow', x: 96, y: 11 + dy, w: 6, h: 4, start: { rx: 1, ry: 0 }, end: { rx: 0, ry: 1 },
      color: '#000000', strokePt: 0.75, head: 'end', anchor,
    },
    {
      id: ids[1], type: 'text', x: 103, y: 9 + dy, w: 22, h: 4, text: batch === 1 ? 'Agent note' : `Agent note ${batch}`,
      sizePt: 8, bold: false, color: '#000000', align: 'left', anchor,
    },
  )
  const after = Buffer.from(JSON.stringify(doc, null, 1), 'utf-8')
  let project = realpathSync.native(figures)
  if (caseInsensitive(project)) project = project.toLowerCase()
  const key = createHash('sha256').update(project, 'utf-8').digest('hex').slice(0, 16)
  const undo = path.join(realpathSync.native(dataDir), 'state', 'agent-undo', key, NAME)
  mkdirSync(undo, { recursive: true })
  const token = `u${Date.now().toString(16)}${batch}`
  writeFileSync(path.join(undo, `${token}.json`), before)
  writeFileSync(path.join(undo, `${token}.meta`), JSON.stringify({ before: rev(before), after: rev(after) }))
  writeFileSync(file, after)
  return { added: ids }
}

const textsOnDisk = (file: string): string[] =>
  (JSON.parse(readFileSync(file, 'utf-8')) as { canvases: { objects: { type: string; text?: string }[] }[] }).canvases
    .flatMap((c) => c.objects.filter((o) => o.type === 'text').map((o) => o.text ?? ''))
    .sort()

/** 浅 / 深两张截图（界面评审用，随测试产物留下） */
async function shots(page: Page, name: string, out: (p: string) => string) {
  await page.waitForTimeout(300) // 通知轨换一条提示有约 0.1 s 的过渡，等它走完再截
  await page.screenshot({ path: out(`${name}-light.png`) })
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  await page.waitForTimeout(600) // 换主题有过渡，等它走完再截
  await page.screenshot({ path: out(`${name}-dark.png`) })
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'))
  await page.waitForTimeout(600)
}

/** 在打开排版**之前**挂上：图的第一次渲染回包（`POST /api/engine/render`） */
function firstRender(page: Page) {
  return page.waitForResponse((r) => r.url().includes('/api/engine/render') && r.request().method() === 'POST', {
    timeout: 120_000,
  })
}

/**
 * 起点要干净，等**确定的信号**、不拿墙钟赌：第一次渲染回包里的图幅（`manifest.size_mm`）就是桌面接下来要把子图校正到的
 * 尺寸（`useEngineSync.nativeSizeFixes`，容差 0.05 mm）。Windows 打包产物上字体不同、图幅与夹具写的差一点，校正是一笔真实改动，
 * 冷启动慢时它落在用例最后一次保存之后，圆点又亮（windows-exe-smoke 2026-11-26 连红两次）。所以：有改动就存，直到磁盘上
 * 那个子图的原生图幅等于渲染回来的、且圆点灭——此后的「未保存」圆点只反映这条用例自己做的事。
 */
async function cleanStart(page: Page, file: string, built: ReturnType<typeof firstRender>) {
  const body = (await (await built).json().catch(() => null)) as { manifest?: { size_mm?: [number, number] } } | null
  const size = body?.manifest?.size_mm
  const dirty = page.locator('[data-project-file-dirty]')
  await expect
    .poll(
      async () => {
        if (await dirty.count()) {
          await page.keyboard.press('ControlOrMeta+s')
          return false
        }
        if (!size) return true
        type Obj = { type: string; nativeW?: number; nativeH?: number }
        const panel = (JSON.parse(readFileSync(file, 'utf-8')) as { canvases: { objects: Obj[] }[] }).canvases[0].objects.find(
          (o) => o.type === 'panel',
        )
        if (panel?.nativeW === undefined || panel.nativeH === undefined) return false
        return Math.abs(panel.nativeW - size[0]) <= 0.05 && Math.abs(panel.nativeH - size[1]) <= 0.05
      },
      { timeout: 60_000, intervals: [250, 500, 1000] },
    )
    .toBe(true)
}

async function openFromProject(page: Page) {
  await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
  await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: new RegExp(NAME) }).click()
  await expect(dialog).toBeHidden()
}

test(
  'Agent 往项目排版里加了标注：回到前台自动并进画布；⌘S 时直接并进来再保存，不弹对话框',
  { tag: '@feature:layout.agent-merge' },
  async ({ app, page }, testInfo) => {
    const a = await app()
    const file = writeLayout(a.figures)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(a.baseURL)
    await waitForDocumentReady(page)
    const built = firstRender(page)
    await openFromProject(page)
    await expect(page.locator('[data-save-destination]')).toHaveAttribute('data-save-destination', 'project')
    await cleanStart(page, file, built)
    const stage = page.locator('[data-canvas-stage]')

    // 插件那条路往同一个文件里追加了两个标注 → 回到前台：自动并进画布，状态栏一句
    expect(agentAnnotates(a.figures, a.dataDir).added).toHaveLength(2)
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(stage.getByText('Agent note', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.locator(':not([data-status-live])').getByText('已并入 2 个标注', { exact: false }).last()).toBeVisible()
    await shots(page, 'agent-merge-auto', (p) => testInfo.outputPath(p))
    // 可撤销：⌘Z 拿掉，⇧⌘Z 放回
    await page.keyboard.press('ControlOrMeta+z')
    await expect(stage.getByText('Agent note', { exact: true })).toHaveCount(0)
    await page.keyboard.press('ControlOrMeta+Shift+z')
    await expect(stage.getByText('Agent note', { exact: true })).toBeVisible()

    // 桌面这边改一处；Agent 又加一批（这回不回前台）；直接 ⌘S：不弹对话框，并进来接着保存
    await page.getByRole('button', { name: '文字' }).click()
    await stage.click({ position: { x: 380, y: 420 } })
    await page.keyboard.type('桌面这边')
    await page.keyboard.press('Escape')
    agentAnnotates(a.figures, a.dataDir, 2)
    await page.keyboard.press('ControlOrMeta+s')
    await expect.poll(() => textsOnDisk(file)).toEqual(['Agent note', 'Agent note 2', '桌面这边'].sort())
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(stage.getByText('Agent note 2', { exact: true })).toBeVisible()
    await expect(page.locator('[data-project-file-dirty]')).toHaveCount(0)
    await expect(page.getByText(/已在别处改过/)).toHaveCount(0)
  },
)
