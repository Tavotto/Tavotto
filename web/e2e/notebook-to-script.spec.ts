import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * ADR 0179：项目文件夹里只有一个 `.ipynb`（带 `%matplotlib inline` 和一个画图单元）——素材库的脚本区列出它，
 * 点「转成脚本并运行」→ 后端抽成它旁边的同名 `.py` → 走「打开并运行」→ 图出现在画布上。
 *
 * 判据的主语：磁盘上的两个文件（笔记本字节不变、生成的 `.py` 带注释掉的魔法行与自己的标记）和画布上真出现的图，
 * 不是 toast。
 */

const notebook = {
  nbformat: 4,
  nbformat_minor: 5,
  metadata: { kernelspec: { name: 'python3', display_name: 'Python 3', language: 'python' } },
  cells: [
    { cell_type: 'markdown', metadata: {}, source: ['# 光谱\n', '一张图'] },
    {
      cell_type: 'code',
      metadata: {},
      execution_count: 3,
      outputs: [{ output_type: 'stream', name: 'stdout', text: ['done\n'] }],
      source: ['%matplotlib inline\n', 'import matplotlib\n', 'matplotlib.use("Agg")\n', 'import matplotlib.pyplot as plt'],
    },
    {
      cell_type: 'code',
      metadata: {},
      execution_count: 4,
      outputs: [],
      source: ['fig, ax = plt.subplots(figsize=(3, 2))\n', 'ax.plot([0, 1, 2], [1, 3, 2])\n', 'fig.savefig("spectrum.pdf")'],
    },
  ],
}

const sha = (file: string) => createHash('sha256').update(readFileSync(file)).digest('hex')

test('素材库里把笔记本转成脚本并运行：图出现，原笔记本一个字节不动', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-nb-'))
  const project = path.join(root, 'proj')
  mkdirSync(project, { recursive: true })
  const nb = path.join(project, 'spectrum.ipynb')
  writeFileSync(nb, JSON.stringify(notebook, null, 1), 'utf-8')
  const before = sha(nb)
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    // 脚本区单独一组「Jupyter 笔记本」，一行一个，主动作是行里那颗钮
    const row = page.locator('[data-notebook-row="spectrum.ipynb"]')
    await expect(row).toBeVisible({ timeout: 60_000 })
    await expect(page.locator('[data-script-group="notebook"]')).toBeVisible()
    await expect(page.locator('[data-element-svg] svg')).toHaveCount(0) // 点之前画布上没有图
    await row.locator('[data-notebook-run]').click()

    // 不再点任何东西：抽成 .py、运行、恰好一张图 → 放进画布并进入编辑
    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })

    // 磁盘：笔记本字节不变；旁边多了同名 .py，魔法行被注释并保留原文，文件头带自己的标记
    expect(sha(nb)).toBe(before)
    const py = path.join(project, 'spectrum.py')
    expect(existsSync(py)).toBe(true)
    const text = readFileSync(py, 'utf-8')
    expect(text).toContain('# [notebook] %matplotlib inline')
    expect(text).toContain('fig.savefig("spectrum.pdf")')
    expect(text).toMatch(/^# .*\n# .*\n# tavotto-notebook: spectrum\.ipynb sha256=[0-9a-f]{64}\n/)
    expect(text).not.toContain('done') // 输出不带
    expect(readdirSync(project).filter((n) => n.endsWith('.py'))).toEqual(['spectrum.py'])
  } finally {
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})

test('浏览器里把笔记本拖到画布上（拿不到路径）：内容交给后端抽成项目里的 .py 并运行，图出现', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-nb-'))
  const project = path.join(root, 'proj')
  mkdirSync(project, { recursive: true })
  // 项目里放着另一个笔记本：打开项目后编辑器就绪、画布在；被拖的那一个不在项目里（只有内容）
  writeFileSync(path.join(project, 'other.ipynb'), JSON.stringify(notebook), 'utf-8')
  let a: Awaited<ReturnType<typeof app>> | undefined
  try {
    a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-notebook-row="other.ipynb"]')).toBeVisible({ timeout: 60_000 })

    const stage = page.locator('[data-world-transform]').locator('..')
    await expect(stage).toBeVisible()
    const dt = await page.evaluateHandle((nbJson) => {
      const d = new DataTransfer()
      d.items.add(new File([nbJson], 'dropped.ipynb', { type: 'application/x-ipynb+json' }))
      return d
    }, JSON.stringify(notebook))
    await stage.dispatchEvent('dragover', { dataTransfer: dt })
    await stage.dispatchEvent('drop', { dataTransfer: dt })

    await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 180_000 })
    const py = path.join(project, 'dropped.py')
    expect(existsSync(py)).toBe(true)
    expect(readFileSync(py, 'utf-8')).toContain('# [notebook] %matplotlib inline')
    expect(existsSync(path.join(project, 'dropped.ipynb'))).toBe(false) // 上传的笔记本本身不存进项目
  } finally {
    await a?.stop().catch(() => undefined)
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})
