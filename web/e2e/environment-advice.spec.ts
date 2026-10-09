import { chmodSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 环境「建议 → 检查 → 采用」（T05，ADR 0114）——只有真浏览器 + 真后端才能回答的几件：
 *
 *   * 项目里有自己的 `.venv`、用户还没决定：打开页面、打开设置，**项目的解释器一次都没被起过**
 *     （哨兵在项目之外）；设置里是一句话 + 一个主按钮，读的是后端的 `project.recommendation`；
 *   * 明确的「检查」只起被点名的候选；检查 ≠ 采用（项目设置里仍没有决定）；
 *   * 点「使用它」时后端现场再体检——假解释器通不过，如实报错、不记任何决定。
 *
 * 全程不联网；解释器是会写哨兵的假 `.venv`（POSIX 脚本），所以只在 POSIX 上跑。
 */
function tmp(prefix: string): string {
  return mkdtempSync(path.join(os.tmpdir(), prefix))
}

function writeProject(dir: string, sentinelDir: string): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(path.join(dir, 'plot.py'), 'import matplotlib\n', 'utf-8')
  const py = path.join(dir, '.venv', 'bin', 'python')
  mkdirSync(path.dirname(py), { recursive: true })
  writeFileSync(path.join(dir, '.venv', 'pyvenv.cfg'), 'home = /nowhere\n', 'utf-8')
  writeFileSync(py, `#!/bin/sh\ntouch "${sentinelDir}/venv_python.$$"\nexit 0\n`, 'utf-8')
  chmodSync(py, 0o755)
}

const fired = (dir: string) => readdirSync(dir).map((n) => n.split('.')[0])

test.skip(
  process.platform === 'win32',
  '哨兵解释器是 POSIX shell 脚本；本条在 CI 的 posix-e2e 腿上执行（issue #30）',
)

test('项目自带的环境：先是一句话的建议，检查才起它，采用要用户点', async ({ app, page }) => {
  const root = tmp('tavotto-envadvice-')
  const sentinels = path.join(root, 'sentinel')
  mkdirSync(sentinels)
  const project = path.join(root, '项目 空格')
  writeProject(project, sentinels)
  try {
    const a = await app({ figures: project })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)

    // 打开设置的「项目」页：渲染环境那一块读后端的建议
    await page.locator('[data-rail="settings"]').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible({ timeout: 30_000 })
    await dialog.getByRole('navigation').locator('[data-section="project"]').click()
    const advice = dialog.locator('[data-env-advice]')
    await expect(advice).toBeVisible({ timeout: 30_000 })
    await expect(advice).toContainText('.venv/bin/python')

    // 后端的同一份事实：需要用户先决定、推荐的是项目声明的那个、没有任何候选被检查过
    const env = await (await page.request.get(`${a.baseURL}/api/engine/environment`)).json()
    const rec = env.project.recommendation
    expect(rec.decision.needs_decision).toBe(true)
    expect(env.project.consent).toBe('none')
    const venv = rec.candidates.find((c: { python_relative: string | null }) => c.python_relative === '.venv/bin/python')
    expect(venv.status).toBe('unchecked')
    expect(rec.recommended_id).toBe(venv.id)

    // 看建议、开设置、扫描：项目的解释器一次都没被起过（给收尾留一点时间再看）
    await page.waitForTimeout(1500)
    expect(fired(sentinels)).toEqual([])

    // 明确的检查：只起被点名的那个；检查不替用户决定
    const checked = await (
      await page.request.post(`${a.baseURL}/api/engine/environment/check`, {
        data: { candidates: [venv.id] },
      })
    ).json()
    expect(checked.checked).toEqual([venv.id])
    expect(fired(sentinels)).toEqual(['venv_python'])
    const after = checked.recommendation.candidates.find((c: { id: string }) => c.id === venv.id)
    expect(after.checked).toBe(true)
    expect(after.status).not.toBe('healthy') // 假解释器通不过体检，如实写，不冒充
    const still = await (await page.request.get(`${a.baseURL}/api/engine/environment`)).json()
    expect(still.project.consent).toBe('none')

    // 点「使用它」：后端现场再体检；假解释器不合格 → 报错，不记任何决定
    await advice.getByRole('button').first().click()
    await expect(advice.locator('.text-danger')).toBeVisible({ timeout: 30_000 })
    const final = await (await page.request.get(`${a.baseURL}/api/engine/environment`)).json()
    expect(final.project.consent).toBe('none')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
