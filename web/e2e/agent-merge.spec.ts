/**
 * 「把 Agent 加的并进来」（ADR 0208 §五、第 2 期 ③）的真浏览器全程，真后端、真引擎。维护者 2026-11-25：只多了 Agent 加的标注时
 * 没有要用户裁决的事，**不提示**——
 *
 * 项目里有一份排版 → 桌面从「项目里的排版」打开它（绑定文件、记下基线）→ 插件那条引擎路往同一个文件里追加标注
 * （`tests/support/e2e_agent_annotate.py`，同一个数据目录）→ 窗口回到前台：标注**自动并进画布**（状态栏一句、可撤销）→
 * 桌面这边改一处 → Agent 又加一批 → 直接 ⌘S：**不弹对话框**，并进来接着保存，磁盘上三处都在、圆点灭。
 *
 * 判据量的是**磁盘上那个文件**。
 */
import { spawnSync } from 'node:child_process'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')
const PYTHON = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')
const SRC = path.join(REPO, 'src')
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
            w: 80,
            h: 60,
            fileId: 'Fig1_kinetics.pdf',
            fileKind: 'pdf',
            nativeW: 80,
            nativeH: 60,
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

function agentAnnotates(figures: string, dataDir: string, batch = 1): { revision: string; added: string[] } {
  const r = spawnSync(PYTHON, [path.join(REPO, 'tests', 'support', 'e2e_agent_annotate.py'), figures, NAME, String(batch)], {
    encoding: 'utf-8',
    env: {
      ...process.env,
      TAVOTTO_DATA_DIR: dataDir,
      PYTHONPATH: [SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    },
  })
  if (r.status !== 0) throw new Error(`Agent 写入失败：\n${r.stdout}\n${r.stderr}`)
  return JSON.parse(r.stdout.trim().split('\n').at(-1)!)
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
    await openFromProject(page)
    await expect(page.locator('[data-save-destination]')).toHaveAttribute('data-save-destination', 'project')
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
