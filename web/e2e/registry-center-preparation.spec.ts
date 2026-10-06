import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, openWorkspace, test } from './fixtures'

/**
 * 接入中心的逐行试运行（T09b，ADR 0116）——真后端、真 worker、真浏览器。
 *
 * 判据的主语是脚本自己写在项目**之外**的运行日志（每次执行一行，带 argv）与项目里的注册表文件，不是 toast：
 *
 *   1. 默认（准备面板开）：「试运行并连接」让接入中心让开、打开同一个准备面板——点下去那一刻**不执行**；确认并运行之后
 *      执行一次、图名登记进注册表。再用 `--extra` 跑一次（并入），然后不带参数再跑：面板在同一句里说清哪些旧图不再关联、
 *      怎么恢复（T03 已知缺口的可恢复提示），注册表照旧整条替换（格式不变）；
 *   2. 开关关闭：同一颗按钮委派素材库那台旧状态机（`/api/registry/probe`），执行一次、登记好，一个准备会话请求都不发。
 *
 * 全程不联网；项目、日志都在临时目录。
 */

const REPO = path.resolve(import.meta.dirname, '..', '..')
const panel = (page: Page) => page.locator('[data-preparation-panel]')
const primary = (page: Page) => panel(page).locator('[data-prep-primary]')

interface Run {
  argv: string[]
}

const runs = (log: string): Run[] =>
  existsSync(log)
    ? readFileSync(log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l) as Run)
    : []

const registeredStems = (project: string): string[] => {
  const file = path.join(project, 'tavotto_registry.json')
  if (!existsSync(file)) return []
  const cfg = JSON.parse(readFileSync(file, 'utf-8')) as { scripts?: Record<string, { stems?: string[] }> }
  return [...(cfg.scripts?.['render_map.py']?.stems ?? [])].sort()
}

/** 静态解不出图名的脚本（图名来自运行期）：项目里那张图的状态是 `needs_probe`，接入中心那一行给「试运行并连接」 */
function project(root: string, log: string): string {
  const dir = path.join(root, '项目')
  mkdirSync(dir, { recursive: true })
  copyFileSync(path.join(REPO, 'examples', 'figures', 'Fig1_kinetics.pdf'), path.join(dir, 'Runtime_map.pdf'))
  writeFileSync(
    path.join(dir, 'render_map.py'),
    [
      'import json, sys',
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'from pathlib import Path',
      '',
      `LOG = ${JSON.stringify(log)}`,
      'NAMES = None  # 运行期才决定，静态扫描解不出',
      '',
      'def main():',
      '    with open(LOG, "a", encoding="utf-8") as f:',
      '        f.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")',
      '    names = list(NAMES or ["Runtime_map"])',
      '    if "--extra" in sys.argv:',
      '        names.append("Extra_map")',
      '    for name in names:',
      '        fig, ax = plt.subplots(figsize=(2, 2))',
      '        ax.plot([0, 1], [1, 0])',
      '        fig.savefig(Path(f"{name}.pdf"))',
      '        plt.close(fig)',
      '',
      'if __name__ == "__main__":',
      '    main()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'tavotto_registry.json'), JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
  return dir
}

async function openCenter(page: Page) {
  await openWorkspace(page)
  await page.locator('[data-workspace-section="current"] button[aria-haspopup]').click()
  await page.getByRole('menuitem', { name: '项目接入状态…' }).click()
  const center = page.getByRole('dialog', { name: '项目接入状态' })
  await expect(center).toBeVisible()
  return center
}

test('接入中心「试运行并连接」打开同一个准备面板：点下去不执行，确认后执行一次并登记；无参数重跑替换旧图名时说清怎么恢复', async ({
  app,
  page,
}) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-reg-prep-'))
  const log = path.join(root, 'runs.jsonl')
  const dir = project(root, log)
  try {
    const a = await app({ figures: dir })
    await page.setViewportSize({ width: 1400, height: 900 })
    const probes: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/registry/probe')) probes.push(r.url())
    })
    await page.goto(a.baseURL)

    const center = await openCenter(page)
    const row = center.locator('[data-panel-row="Runtime_map.pdf"]')
    await row.getByRole('button', { name: /试运行并连接/ }).click()

    // 接入中心让开，同一个准备面板接手：只读检查，一个执行都没有
    await expect(center).toHaveCount(0)
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    expect(runs(log)).toEqual([])

    // 在面板里带上 `--extra`：按新参数检查 → 确认并运行
    await panel(page).locator('[data-prep-details] > summary').click()
    await panel(page).locator('[data-testid="argv-render_map.py"] > summary').click()
    await panel(page).getByRole('button', { name: '添加参数' }).click()
    await panel(page).getByRole('textbox', { name: 'render_map.py 的第 1 个参数' }).fill('--extra')
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    expect(runs(log)).toEqual([])
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    expect(runs(log)).toEqual([{ argv: ['--extra'] }])
    await expect.poll(() => registeredStems(dir), { timeout: 30_000 }).toEqual(['Extra_map', 'Runtime_map'])

    // 去掉参数、不带参数再跑：注册表按脚本整条替换（格式不变），面板同一句里说清 Extra_map 不再关联、怎么恢复
    await panel(page).getByRole('button', { name: '删除 render_map.py 的第 1 个参数' }).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'completed_unlinked', { timeout: 120_000 })
    await expect(panel(page).locator('[data-prep-line]')).toContainText('此前带其他参数生成的 Extra_map 已不再关联')
    await expect(panel(page).locator('[data-prep-line]')).toContainText('用原参数再运行一次即可恢复')
    expect(runs(log)).toEqual([{ argv: ['--extra'] }, { argv: [] }])
    expect(registeredStems(dir)).toEqual(['Runtime_map'])

    // 接入中心自己一个试运行请求都没发过
    expect(probes).toEqual([])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('开关关闭：接入中心委派素材库那台旧状态机——执行一次、登记好，不发准备会话请求', async ({ app, page }) => {
  await page.addInitScript(() => localStorage.setItem('tavotto.preparationPanel', 'off'))
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-reg-legacy-'))
  const log = path.join(root, 'runs.jsonl')
  const dir = project(root, log)
  try {
    const a = await app({ figures: dir })
    await page.setViewportSize({ width: 1400, height: 900 })
    const sessions: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/engine/preparation-sessions')) sessions.push(r.url())
    })
    await page.goto(a.baseURL)

    const center = await openCenter(page)
    const row = center.locator('[data-panel-row="Runtime_map.pdf"]')
    await row.getByRole('button', { name: /试运行并连接/ }).click()
    await expect.poll(() => registeredStems(dir), { timeout: 120_000 }).toEqual(['Runtime_map'])
    // 那一行读素材库那台状态机的结果（接入中心不再另记一份）
    await expect(center.locator('[data-panel-row="Runtime_map.pdf"]')).toContainText('已连接 Runtime_map', {
      timeout: 30_000,
    })
    expect(runs(log)).toEqual([{ argv: [] }])
    await expect(panel(page)).toHaveCount(0)
    expect(sessions).toEqual([])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
