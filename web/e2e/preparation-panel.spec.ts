import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, startApp, test } from './fixtures'

/**
 * 准备面板（T09，ADR 0116）——真后端、真 worker、真浏览器，待测项目开始时**只有脚本与数据**（没有图、没有注册表）。
 *
 * 判据的主语是脚本自己写在项目**之外**的运行日志（每次执行一行 start、跑完一行 done）：执行了几次、拿到的 argv /
 * 数据 / input 回答是什么——不是界面上的 toast，也不是 store 里的期望值。
 *
 *   1. 首跑闭环：检查条 → 准备并运行 → 面板里选运行目录（缺数据的那道门，由原对话框作答）→ 面板里改参数（按新参数
 *      重新检查）→ 确认并运行 → 运行时 input 在面板里答 → 已捕获 → 进入编辑（编辑渲染可用才算）——全程执行恰好 1 次；
 *   2. 恢复：关面板 → 同一问由原对话框接着问；取报告的 HTTP 断开而后台照跑 → 只标「连不上」、不标失败、不重提交，
 *      恢复后读到真实终局；应用重启 → 旧会话 404 → 重建只读检查、不自动重跑；明确停止 → 本会话新建的会话当场关掉。
 *
 * 全程不联网；项目、日志、闸门文件都在临时目录。
 */

const panel = (page: Page) => page.locator('[data-preparation-panel]')
const primary = (page: Page) => panel(page).locator('[data-prep-primary]')

interface Run {
  event: 'start' | 'done'
  argv?: string[]
  values?: number[]
  choice?: string
}

const runs = (log: string): Run[] =>
  existsSync(log)
    ? readFileSync(log, 'utf-8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l) as Run)
    : []
const starts = (log: string) => runs(log).filter((r) => r.event === 'start').length

/**
 * 脚本：读项目根下的 `data/values.txt`（脚本在 tools/ 下 → 首开要先选运行目录），可选 `--scale`，运行时 input
 * 选顺序，闸门文件存在时停住（给「后台还在跑」留出可控的窗口），图名运行后才知道（`curve_<choice>`）。
 */
function script(log: string, hold: string, readsData: boolean): string {
  return [
    'import json, os, sys, time',
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    '',
    `LOG = ${JSON.stringify(log)}`,
    `HOLD = ${JSON.stringify(hold)}`,
    '',
    'def note(event, **kw):',
    '    with open(LOG, "a", encoding="utf-8") as f:',
    '        f.write(json.dumps({"event": event, **kw}) + "\\n")',
    '',
    'def main():',
    '    note("start", argv=sys.argv[1:])',
    '    scale = float(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 1.0',
    readsData
      ? '    values = [float(x) for x in open("data/values.txt", encoding="utf-8").read().split()]'
      : '    values = [1.0, 2.0, 3.0]',
    '    print("1) as is")',
    '    print("2) reversed")',
    '    choice = input("order: ").strip()',
    '    if choice == "2":',
    '        values = values[::-1]',
    '    deadline = time.time() + 90',
    '    while os.path.exists(HOLD) and time.time() < deadline:',
    '        time.sleep(0.1)',
    '    fig, ax = plt.subplots(figsize=(3, 2))',
    '    ax.plot(range(len(values)), [v * scale for v in values])',
    '    fig.savefig("curve_" + choice + ".pdf")',
    '    note("done", argv=sys.argv[1:], values=values, choice=choice)',
    '',
    'if __name__ == "__main__":',
    '    main()',
    '',
  ].join('\n')
}

test('首跑闭环：只有脚本与数据 → 面板里选目录、改参数、答 input → 捕获 → 进入编辑，执行恰好一次', async ({
  app,
  page,
}) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-prep-'))
  const project = path.join(root, '项目 一')
  const log = path.join(root, 'runs.jsonl')
  mkdirSync(path.join(project, 'tools'), { recursive: true })
  mkdirSync(path.join(project, 'data'), { recursive: true })
  writeFileSync(path.join(project, 'tools', 'plot.py'), script(log, path.join(root, 'HOLD'), true), 'utf-8')
  writeFileSync(path.join(project, 'data', 'values.txt'), '3 1 4\n', 'utf-8')
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    // 检查条（导入即扫描）→ 选定目标 → 准备并运行：打开面板，后端只做只读检查
    const bar = page.locator('[data-project-scan]')
    await expect(bar).toBeVisible({ timeout: 30_000 })
    await bar.getByRole('button', { name: '详情' }).click()
    await page.locator('[data-scan-prepare="tools/plot.py"]').click()
    await expect(panel(page)).toBeVisible()

    // 数据只在项目根找得到：先选运行目录（原对话框作答，同一次 PATCH），答完面板只读地重新检查
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'workdir', { timeout: 60_000 })
    expect(starts(log)).toBe(0) // 检查不执行
    await primary(page).click()
    const workdir = page.locator('[data-dialog="workdir-confirm"]')
    await expect(workdir).toBeVisible()
    await workdir.locator('[data-workdir-option="project_root"]').click()
    await workdir.getByRole('button', { name: '运行', exact: true }).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    expect(starts(log)).toBe(0) // 选了目录也只是重新检查，不运行

    // 在面板里改参数：草稿变了 → 「按新参数检查」→ 新的配置修订，句子说出参数个数
    await panel(page).locator('[data-prep-details] > summary').click()
    await panel(page).locator('[data-testid="argv-tools/plot.py"] > summary').click()
    const add = panel(page).getByRole('button', { name: '添加参数' })
    await add.click()
    await add.click()
    await panel(page).getByRole('textbox', { name: 'tools/plot.py 的第 1 个参数' }).fill('--scale')
    await panel(page).getByRole('textbox', { name: 'tools/plot.py 的第 2 个参数' }).fill('3')
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'args_changed')
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    await expect(panel(page).locator('[data-prep-line]')).toContainText('2 个参数')
    expect(starts(log)).toBe(0)

    // 确认并运行 → 运行时 input 在面板里答（同一请求只有一个展示面：原对话框不出现）
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'input', { timeout: 120_000 })
    await expect(page.locator('[data-dialog="script-input"]')).toHaveCount(0)
    const form = panel(page).locator('[data-prep-input]')
    await expect(form.locator('[data-script-input-output]')).toContainText('2) reversed')
    await form.locator('[data-script-input-answer]').fill('2')
    await form.locator('[data-script-input-submit]').click()

    // 捕获 → 进入编辑：用这次捕获的图，编辑渲染可用才说「已进入编辑」
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'completed', { timeout: 120_000 })
    await expect(panel(page).locator('[data-prep-line]')).toContainText('已捕获 1 张图')
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'edit_ready', { timeout: 120_000 })

    // 真值：恰好执行一次，argv 是面板里填的那两个 token，数据是项目根那份（倒序），回答是面板里那一个
    await page.waitForTimeout(1500)
    const all = runs(log)
    expect(all.filter((r) => r.event === 'start')).toEqual([{ event: 'start', argv: ['--scale', '3'] }])
    expect(all.filter((r) => r.event === 'done')).toEqual([
      { event: 'done', argv: ['--scale', '3'], values: [4, 1, 3], choice: '2' },
    ])
    // 执行结束 / 捕获到图分开表达
    await panel(page).locator('[data-prep-details] > summary').click()
    await expect(panel(page).locator('[data-fact="execution_finished"]')).toHaveAttribute('data-value', 'true')
    await expect(panel(page).locator('[data-fact="figure_captured"]')).toHaveAttribute('data-value', 'true')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('恢复：关面板换展示面、HTTP 断开后台照跑、应用重启不重跑、明确停止只关自己的会话', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-prep-r-'))
  const project = path.join(root, 'proj')
  const log = path.join(root, 'runs.jsonl')
  const hold = path.join(root, 'HOLD')
  mkdirSync(project, { recursive: true })
  writeFileSync(path.join(project, 'hold.py'), script(log, hold, false), 'utf-8')
  let second: Awaited<ReturnType<typeof startApp>> | null = null
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    // 素材库脚本行的 ▶：同一个面板（不直接执行）
    const run = page.locator('[data-script-run="hold.py"]')
    await expect(run).toBeVisible({ timeout: 30_000 })
    await run.click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'ready', { timeout: 60_000 })
    expect(starts(log)).toBe(0)
    writeFileSync(hold, 'hold')
    await primary(page).click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'input', { timeout: 120_000 })

    // 关面板只换展示面：同一问由原对话框接着问，脚本不被取消
    await panel(page).locator('[data-prep-close]').click()
    await expect(panel(page)).toHaveCount(0)
    const dialog = page.locator('[data-dialog="script-input"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('[data-script-input-prompt]')).toHaveText('order: ')
    await dialog.locator('[data-script-input-answer]').fill('1')
    await dialog.locator('[data-script-input-submit]').click()
    await expect(dialog).toHaveCount(0)

    // 脚本此刻停在闸门上（后台还在跑）：从脚本行的状态回到面板
    await page.locator('[data-script-row="hold.py"] [data-script-prep-status]').click()
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'running', { timeout: 30_000 })
    const sid = await panel(page).getAttribute('data-prep-session')
    expect(sid).toBeTruthy()

    // 取报告的 HTTP 断开：只改连接事实，不标失败、不重提交；后台照跑到终局
    await page.route('**/api/engine/preparation-sessions/**', (route) => route.abort())
    await expect(panel(page)).toHaveAttribute('data-prep-connection', 'lost', { timeout: 30_000 })
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'offline')
    rmSync(hold)
    await expect
      .poll(async () => (await (await page.request.get(`${a.baseURL}/api/engine/preparation-sessions/${sid}`)).json()).phase, {
        timeout: 60_000,
      })
      .toBe('completed')
    expect(await panel(page).getAttribute('data-prep-state')).toBe('offline') // 失联期间界面没有把它说成失败
    await page.unroute('**/api/engine/preparation-sessions/**')
    await expect(panel(page)).toHaveAttribute('data-prep-connection', 'ok', { timeout: 30_000 })
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'completed')
    expect(starts(log)).toBe(1) // 失联 / 重连没有重提交任何执行

    // 应用重启：同一端口、**同一份数据 / 配置目录**起一个新后端（同一台机器上的重启：内存里的会话都没了，本机的
    // 数据目录还在）→ 旧会话 404 → 只重建检查，不重跑。fixtures 的 stop 会删整个临时根，先把两个目录存下来。
    // （以前这里每次重启都换一个全新的数据目录——那等于「换了一台机器」：T12 起 input 回答的上下文摘要只在本机数据
    // 目录的侧表里，换机器只当建议重新问，这是有意的安全方向，而不是「重启丢了上下文」）
    const port = a.port
    const keptData = path.join(root, 'kept-data')
    const keptConfig = path.join(root, 'kept-config')
    cpSync(a.dataDir, keptData, { recursive: true })
    const srcConfig = path.join(path.dirname(a.dataDir), 'config')
    if (existsSync(srcConfig)) cpSync(srcConfig, keptConfig, { recursive: true })
    else mkdirSync(keptConfig, { recursive: true })
    await a.stop()
    second = await startApp({
      figures: project,
      port,
      env: { TAVOTTO_DATA_DIR: keptData, TAVOTTO_CONFIG_DIR: keptConfig },
    })
    await expect(panel(page)).toHaveAttribute('data-prep-state', 'restarted', { timeout: 60_000 })
    await expect(panel(page).locator('[data-prep-primary]')).toHaveAttribute('data-prep-primary', 'run')
    await page.waitForTimeout(2000)
    expect(starts(log)).toBe(1)

    // 明确停止：本会话新建的会话在停住时当场关掉（不等脚本自己跑完）
    writeFileSync(hold, 'hold')
    await primary(page).click()
    // 同一台机器重启：记住的回答连同本机侧表里的上下文都还在，按上下文原样复用（ADR 0099 §九 / §十）——不再问。
    // （以前这里写成「复用或再问一次都行」，再按某一刻读到的 running|input 分支：input 晚于 running 到来时就走错分支，
    // 停在问题上去点「停止」——这条用例在 T12 之后的「本机稳定红」就是这个竞态 + 每次重启换数据目录）
    const state = panel(page)
    await expect.poll(() => starts(log), { timeout: 120_000 }).toBe(2) // 脚本真的开跑了（停在闸门上）
    await expect(state).toHaveAttribute('data-prep-state', 'running', { timeout: 60_000 })
    await page.waitForTimeout(1500) // 若回答没被复用，问题会在脚本开跑后立刻到来：给它时间出现，再断言它没有
    await expect(state).toHaveAttribute('data-prep-state', 'running')
    await expect(page.locator('[data-script-input-answer]')).toHaveCount(0)
    await primary(page).click() // 停止
    await expect(state).toHaveAttribute('data-prep-state', 'cancelled', { timeout: 30_000 })
    expect(existsSync(hold)).toBe(true) // 闸门还在：是取消当场关掉了会话，不是脚本自己跑完
    expect(runs(log).filter((r) => r.event === 'done').length).toBe(1)
  } finally {
    if (existsSync(hold)) rmSync(hold)
    await second?.stop()
    rmSync(root, { recursive: true, force: true })
  }
})
