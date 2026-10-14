import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import type { Page } from '@playwright/test'
import os from 'node:os'
import path from 'node:path'
import { expect, openWorkspace, test } from './fixtures'
import { card, primary } from './prepCard'

/**
 * 接入中心的逐行试运行（T09b，ADR 0116）——真后端、真 worker、真浏览器。
 *
 * 判据的主语是脚本自己写在项目**之外**的运行日志（每次执行一行，带 argv）与项目里的注册表文件，不是 toast：
 *
 *   1. 默认（准备面板开）：「试运行并连接」让接入中心让开、打开同一个准备面板并认领 run_when_ready（V2 PR4：点一次就执行，
 *      无参数跑一次、图名登记进注册表）。再用 `--extra` 跑一次（并入），然后回到无参数配置：复用已有会话、不重复执行；
 *   2. 开关关闭：同一颗按钮委派素材库那台旧状态机（`/api/registry/probe`），执行一次、登记好，一个准备会话请求都不发。
 *
 * 全程不联网；项目、日志都在临时目录。
 */

const REPO = path.resolve(import.meta.dirname, '..', '..')

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
function project(root: string, log: string, twin = false): string {
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
      `    names = list(NAMES or ${JSON.stringify(twin ? ['Base_map', 'Runtime_map'] : ['Runtime_map'])})`,
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

test('接入中心「试运行并连接」打开同一个准备面板并认领 run_when_ready：执行一次并登记；带参数再跑并入；回到无参数配置复用已有会话不重跑', async ({
  app,
  page,
}) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-reg-prep-'))
  const log = path.join(root, 'runs.jsonl')
  // 默认就出两张图（V2 PR4：单张图会自动进入编辑、卡片收起；这条用例要看卡片上的参数与恢复提示，所以让它停在「画好了」）
  const dir = project(root, log, true)
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

    // 接入中心让开，同一张引导卡接手：「试运行并连接」就是「打开并运行」（V2 PR4）——检查完接着认领 run_when_ready，
    // 无参数跑一次；两张图要用户挑，所以不自动进入编辑，卡片停在「画好了」上
    await expect(center).toHaveCount(0)
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    expect(runs(log)).toEqual([{ argv: [] }])
    await expect.poll(() => registeredStems(dir), { timeout: 30_000 }).toEqual(['Base_map', 'Runtime_map'])

    // 在卡里带上 `--extra`（原样参数）：继续 = 按新参数重新检查并接着运行（这个条目是从「打开并运行」入口打开的）
    await card(page).locator('[data-testid="argv-raw-render_map.py"] > summary').click()
    await card(page).getByRole('button', { name: '添加参数' }).click()
    await card(page).getByRole('textbox', { name: 'render_map.py 的第 1 个参数' }).fill('--extra')
    await expect(card(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await primary(page).click()
    await expect.poll(() => runs(log).length, { timeout: 120_000 }).toBe(2)
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    expect(runs(log)).toEqual([{ argv: [] }, { argv: ['--extra'] }])
    await expect.poll(() => registeredStems(dir), { timeout: 30_000 }).toEqual(['Base_map', 'Extra_map', 'Runtime_map'])

    // 去掉参数、回到无参数配置：同一个目标（脚本 + 运行配置）复用已有的会话，**不会再执行一次**（那份结果就在那里）。
    // （「无参数运行替换掉旧图名」的恢复提示由 `PreparationCard.test.tsx` 与后端 `test_registry_probe*` 钉；
    // 它需要一个全新的无参数会话，而这里无参数会话是第一个、还活着。）
    const raw = card(page).locator('[data-testid="argv-raw-render_map.py"]')
    if ((await raw.getAttribute('open')) === null) await raw.locator('> summary').click()
    await card(page).getByRole('button', { name: '删除 render_map.py 的第 1 个参数' }).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await primary(page).click()
    await expect(card(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 60_000 })
    await expect(card(page).locator('[data-prep-figures]')).toContainText('Base_map')
    await page.waitForTimeout(1500)
    expect(runs(log)).toEqual([{ argv: [] }, { argv: ['--extra'] }])
    expect(registeredStems(dir)).toEqual(['Base_map', 'Extra_map', 'Runtime_map'])

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
    // 引导卡可能因导入即扫描自动弹出（那只是扫描结果，不建会话）；但不会有任何会话卡
    await expect(page.locator('[data-prep-card][data-prep-session]')).toHaveCount(0)
    expect(sessions).toEqual([])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
