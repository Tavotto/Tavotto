/**
 * 「把 Agent 加的并进来」（ADR 0208 §五、第 2 期 ③）的真浏览器全程，真后端、真引擎：
 *
 * 项目里有一份排版 → 桌面从「项目里的排版」打开它（绑定文件、记下基线）→ 插件那条引擎路往同一个文件里追加两个标注
 * （`tests/support/e2e_agent_annotate.py`，同一个数据目录）→ 窗口回到前台：状态栏挂「Agent 往这份排版里加了 2 个标注」胶囊；
 * 不点它，接着改一处再 ⌘S → 撞上冲突：对话框认出「只多了 Agent 加的」，主按钮是「并进来」→ 点它 → 画布上出现 Agent 的字，
 * 并接着把这次保存做完：磁盘上同时有 Agent 加的与桌面这边改的。
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

function agentAnnotates(figures: string, dataDir: string): { revision: string; added: string[] } {
  const r = spawnSync(PYTHON, [path.join(REPO, 'tests', 'support', 'e2e_agent_annotate.py'), figures, NAME], {
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
  'Agent 往项目排版里加了标注：胶囊提醒；⌘S 撞冲突时「并进来」并保存，两边都在',
  { tag: '@feature:layout.agent-merge' },
  async ({ app, page }, testInfo) => {
    const a = await app()
    const file = writeLayout(a.figures)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(a.baseURL)
    await waitForDocumentReady(page)
    await openFromProject(page)
    await expect(page.locator('[data-save-destination]')).toHaveAttribute('data-save-destination', 'project')

    // 插件那条路往同一个文件里追加了两个标注
    const wrote = agentAnnotates(a.figures, a.dataDir)
    expect(wrote.added).toHaveLength(2)

    // 回到前台：状态栏挂胶囊（一句话 + 「并进来」）
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    // 读屏用的 live region 里也有这句话（sr-only），取看得见的那一份
    const capsule = page.locator(':not([data-status-live])').getByText('Agent 往这份排版里加了 2 个标注', { exact: true }).last()
    await expect(capsule).toBeVisible({ timeout: 15_000 })
    await shots(page, 'agent-merge-capsule', (p) => testInfo.outputPath(p))

    // 不点胶囊：桌面这边接着改一处再 ⌘S → 撞冲突
    await page.getByRole('button', { name: '文字' }).click()
    await page.locator('[data-canvas-stage]').click({ position: { x: 380, y: 420 } })
    await page.keyboard.type('桌面这边')
    await page.keyboard.press('Escape')
    await page.keyboard.press('ControlOrMeta+s')
    const dialog = page.getByRole('dialog')
    const merge = dialog.locator('[data-layout-merge-agent]')
    await expect(merge).toBeVisible({ timeout: 15_000 })
    await expect(dialog.locator('[data-layout-conflict]')).toContainText('项目里那份只比你多了 Agent 加的 2 个标注')
    // 这不是要警报的冲突：保存时挂的那条红色「已在别处改过」撤掉，只留对话框
    await expect(page.getByText(/已在别处改过，这次没有写入/)).toHaveCount(0)
    await shots(page, 'agent-merge-dialog', (p) => testInfo.outputPath(p))
    await merge.click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[data-canvas-stage]').getByText('Agent note')).toBeVisible()

    // 「并进来」接着把这次 ⌘S 做完：磁盘上两边都在，不再弹冲突
    await expect.poll(() => textsOnDisk(file)).toEqual(['Agent note', '桌面这边'].sort())
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('[data-project-file-dirty]')).toHaveCount(0)
  },
)
