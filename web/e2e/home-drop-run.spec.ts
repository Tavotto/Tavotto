import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * 主页拖入 .py = 打开它所在的文件夹**并运行这个脚本**，图放进当前画布（2026-10-21 诊断包：同一文件夹里
 * 拖进不同脚本，打开的都是同一个项目、恢复的是上次的排版，画布上永远是第一张图）。
 *
 * 浏览器拿得到 `text/uri-list` 里的 file:// 路径时走的是与桌面壳系统拖放同一个 `openFolder`
 * （`HomeView.useScriptImport`）；壳那条事件本身由 `HomeView.test.tsx` 钉。判据的主语是脚本自己写在项目
 * **之外**的运行日志（谁跑了、跑了几次）与画布上的对象数，不是 toast。
 */

const COMPLETED = JSON.stringify({
  schemaVersion: 1,
  flowVersion: 2,
  status: 'completed',
  currentStep: null,
  completedSteps: [],
  skippedSteps: [],
  hintSeen: {},
  startedAt: 1,
  completedAt: 2,
  tutorialProjectId: null,
  tutorialDocumentId: null,
  pausedBy: null,
})

const script = (log: string, name: string): string =>
  [
    'import json',
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    '',
    `with open(${JSON.stringify(log)}, "a", encoding="utf-8") as f:`,
    `    f.write(json.dumps({"run": ${JSON.stringify(name)}}) + "\\n")`,
    'fig, ax = plt.subplots(figsize=(3, 2))',
    'ax.plot([0, 1, 2], [1, 3, 2])',
    `fig.savefig(${JSON.stringify(`${name}.pdf`)})`,
    '',
  ].join('\n')

const runs = (log: string): string[] =>
  existsSync(log)
    ? readFileSync(log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => (JSON.parse(l) as { run: string }).run)
    : []

/** 往主页拖放区放一个带本机路径的文件（浏览器里 file:// URI 是唯一能带路径的形状） */
async function dropScript(page: Page, file: string) {
  const zone = page.locator('[data-home-dropzone]')
  await expect(zone).toBeVisible({ timeout: 30_000 })
  const dt = await page.evaluateHandle(
    ([uri, name]) => {
      const d = new DataTransfer()
      d.setData('text/uri-list', uri)
      d.items.add(new File(['# py\n'], name, { type: 'text/x-python' }))
      return d
    },
    [pathToFileURL(file).href, path.basename(file)] as const,
  )
  await zone.dispatchEvent('dragenter', { dataTransfer: dt })
  await zone.dispatchEvent('drop', { dataTransfer: dt })
}

test('同一文件夹里先后拖入两个不同的 .py：各自运行一次，第二张图也进了当前画布', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-homedrop-'))
  const project = path.join(root, 'proj')
  const log = path.join(root, 'runs.jsonl')
  mkdirSync(project, { recursive: true })
  // 诊断包里的现场：同一个文件夹里本来就有好几个脚本（素材扫描 → 3 个素材），画布上只放过第一个
  writeFileSync(path.join(project, 'a.py'), script(log, 'a'), 'utf-8')
  writeFileSync(path.join(project, 'b.py'), script(log, 'b'), 'utf-8')
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    await page.addInitScript((blob) => window.localStorage.setItem('tavotto.onboarding', blob), COMPLETED)
    a = await app({ noProject: true })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    await dropScript(page, path.join(project, 'a.py'))
    // 不再点任何东西：a 跑完、恰好一张图 → 放进画布并进入编辑
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    await expect.poll(() => runs(log), { timeout: 30_000 }).toEqual(['a'])

    // 回主页，拖同一文件夹里的另一个脚本：跑的是 b，不是只把上次的排版恢复出来
    await page.keyboard.press('Escape')
    await page.locator('[data-home-button]').first().click()
    await dropScript(page, path.join(project, 'b.py'))
    await expect.poll(() => runs(log), { timeout: 180_000 }).toEqual(['a', 'b'])
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })

    // 回到排版：当前画布上 a、b 两张图都在
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
  } finally {
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})
