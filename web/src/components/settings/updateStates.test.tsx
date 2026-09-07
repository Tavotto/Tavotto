/**
 * 更新页的状态覆盖（UI/UX 审计 T48）。
 *
 * 审计走查那天只看得到一种状态：0.13.0 + 「已是最新版本。」。有更新、离线、
 * 下载中、失败、完成这五态**在真机上凑不齐**，所以这里用受控 fixture 摆出来
 * ——fixture 的取值集合不是编的，它就是 `store/updateStore.ts` 的状态字段
 * （`UpdateStatus` 的可选字段 + `DesktopPhase` 的四档 + 两条通道各自的错误位）。
 *
 * 钉住的两件事：
 *   1. **「最新」是一次检查的回答，不是发布状态的实时核验**——从没查过时不许
 *      说「是最新」，查过了也只能说到那一刻；
 *   2. **进行中显示真实的数，拿不到就走不确定态**；失败看得出是失败，且重试
 *      的入口还在。
 *
 * 两条通道互斥（桌面归 Tauri、浏览器归 `/api/update/*`），所以下面按通道分组。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import { UpdateSettings } from '@/components/settings/UpdateSettings'
import { useUpdateStore } from '@/store/updateStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const st = (key: string, values?: Record<string, unknown>) =>
  t(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 2026-09-06 14:53 本地时间——断言里出现的时间串由 formatDateTime 现算 */
const CHECKED_AT = new Date(2026, 8, 6, 14, 53).getTime()

let root: Root
let host: HTMLDivElement

async function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<UpdateSettings />)
  })
  await act(async () => {})
}

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byLabel = (label: string) => buttons().find((b) => b.textContent?.trim() === label)
/**
 * 「最新」那一行的结论。**判据认结构性标记，不认那两句话的散文**：
 * 「不含另一句」在时间参数不同的时候恒真，而恒真的断言看起来和通过一模一样。
 */
const verdict = () =>
  document.querySelector('[data-update-verdict]')?.getAttribute('data-update-verdict') ?? null

/**
 * 基线：一个已经拿到状态、什么都没发生的浏览器通道。
 *
 * **两条 effect 必须被摆平**：`status` 为空时组件会自己 `check(false)`，
 * `desktopChecked` 为假时会自己 `checkDesktop()`——两者都会真的去 fetch，
 * 让用例变成在测网络。这里把动作换成 no-op，再由每条用例摆自己的状态。
 */
function seed(patch: Record<string, unknown> = {}) {
  useUpdateStore.setState({
    status: {
      current: '0.13.0',
      auto_check: true,
      repo_url: 'https://github.com/Tavotto/Tavotto',
      releases_url: 'https://github.com/Tavotto/Tavotto/releases',
    } as never,
    checking: false,
    applying: false,
    restartRequired: false,
    applyLog: null,
    applyFailed: false,
    checkError: null,
    desktopPhase: 'idle',
    desktopUpdate: null,
    desktopProgress: null,
    desktopError: null,
    desktopChecked: false,
    desktopCheckedAtMs: null,
    check: async () => {},
    checkDesktop: async () => {},
    setAutoCheck: async () => {},
    ...patch,
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('这条用例不该联网'))),
  )
  seed()
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

/* ------------------------ 「最新」这句话的作用域 ------------------------- */

describe('「最新」只说到上一次检查那一刻', () => {
  it('从没检查过时说「不知道」，不说「是最新」', async () => {
    seed({ status: { current: '0.13.0', auto_check: true } as never })
    await render()
    expect(verdict()).toBe('unknown')
    expect(text()).toContain(st('update.latestUnknown'))
    expect(text()).toContain(st('update.lastChecked', { time: st('update.neverChecked') }))
  })

  it('查过了才说「那一刻没有新版本」，并带上那一刻', async () => {
    seed({
      status: { current: '0.13.0', auto_check: true, checked_at_ms: CHECKED_AT } as never,
    })
    await render()
    expect(verdict()).toBe('checked')
    expect(text()).toContain(
      st('update.noUpdateAtLastCheck', { time: formatDateTime(CHECKED_AT) }),
    )
  })

  it('桌面通道同样按时间戳说话：查过之前不给结论', async () => {
    seed({ status: { current: '0.13.0', desktop: true, auto_check: true } as never })
    await render()
    expect(text()).toContain(st('update.lastChecked', { time: st('update.neverChecked') }))
    expect(verdict()).toBeNull()
  })

  it('桌面通道查过没有新版：结论带上那一刻', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
    })
    await render()
    const time = formatDateTime(CHECKED_AT)
    expect(verdict()).toBe('checked')
    expect(text()).toContain(st('update.noUpdateAtLastCheck', { time }))
    expect(text()).toContain(st('update.lastChecked', { time }))
  })
})

/* ------------------------------- 有更新 --------------------------------- */

describe('有更新', () => {
  it('浏览器通道：版本、变更、单一主行动都在，且不再说「最新」', async () => {
    seed({
      status: {
        current: '0.13.0',
        auto_check: true,
        checked_at_ms: CHECKED_AT,
        update_available: true,
        latest: '0.14.0',
        notes: '修了三个导出缺陷',
        can_self_update: true,
        html_url: 'https://example.invalid/releases/v0.14.0',
      } as never,
    })
    await render()
    expect(text()).toContain('0.14.0')
    expect(text()).toContain('修了三个导出缺陷')
    expect(byLabel(st('update.downloadAndUpgrade'))).toBeTruthy()
    expect(verdict()).toBeNull()
  })

  it('桌面通道：有更新时不出现「那一刻没有新版本」', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopUpdate: { version: '0.14.0', notes: '修了三个导出缺陷' } as never,
    })
    await render()
    expect(text()).toContain('0.14.0')
    expect(byLabel(st('update.downloadAndInstall'))).toBeTruthy()
    expect(verdict()).toBeNull()
  })

  it('源码检出装不了：给命令而不是一个点不动的按钮', async () => {
    seed({
      status: {
        current: '0.13.0',
        auto_check: true,
        checked_at_ms: CHECKED_AT,
        update_available: true,
        latest: '0.14.0',
        can_self_update: false,
        upgrade_command: 'git pull',
      } as never,
    })
    await render()
    expect(text()).toContain('git pull')
    expect(byLabel(st('update.downloadAndUpgrade'))).toBeUndefined()
  })
})

/* -------------------------------- 离线 ---------------------------------- */

describe('离线', () => {
  it('浏览器通道：手动检查连不上后端时说出来，且还能再点一次', async () => {
    seed({ checkError: '连不上更新服务' })
    await render()
    expect(text()).toContain('连不上更新服务')
    const btn = byLabel(st('update.checkNow'))!
    expect(btn.disabled).toBe(false)
  })

  it('桌面通道：说不出话的按钮不算反馈——错误 + 手动下载 + 可重试', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopError: '更新服务不可达',
    })
    await render()
    expect(text()).toContain('更新服务不可达')
    expect(text()).toContain(st('update.manualDownload'))
    expect(byLabel(st('update.checkNow'))!.disabled).toBe(false)
    // 检查失败时不许拿上一次的时间戳去说「那一刻是最新的」
    expect(verdict()).toBeNull()
  })
})

/* ------------------------------ 下载中 ---------------------------------- */

describe('下载中只显示壳真的给出的数', () => {
  const progressbar = () => document.querySelector('[role="progressbar"]') as HTMLElement | null

  it('有 Content-Length：进度条与文字都用那个真实百分比', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopUpdate: { version: '0.14.0' } as never,
      desktopPhase: 'downloading',
      desktopProgress: 0.42,
    })
    await render()
    expect(progressbar()!.getAttribute('aria-valuenow')).toBe('42')
    expect(text()).toContain(st('update.downloadingPct', { pct: 42 }))
  })

  it('拿不到 Content-Length：走不确定态，不编一个百分比', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopUpdate: { version: '0.14.0' } as never,
      desktopPhase: 'downloading',
      desktopProgress: null,
    })
    await render()
    expect(progressbar()!.hasAttribute('aria-valuenow')).toBe(false)
    expect(text()).toContain(st('update.downloading'))
    expect(text()).not.toMatch(/\d+%/)
  })
})

/* -------------------------------- 失败 ---------------------------------- */

describe('失败看得出是失败，重试路径还在', () => {
  it('浏览器通道：pip 装失败不再画成一片中性日志', async () => {
    seed({
      status: {
        current: '0.13.0',
        auto_check: true,
        checked_at_ms: CHECKED_AT,
        update_available: true,
        latest: '0.14.0',
        can_self_update: true,
      } as never,
      applyFailed: true,
      applyLog: 'ERROR: Could not install packages due to an OSError',
    })
    await render()
    const alert = document.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain(st('update.applyFailedRetry'))
    // pip 真正说的那句话不许被吃掉
    expect(text()).toContain('ERROR: Could not install packages due to an OSError')
    // 重试的入口还在，而且点得动
    expect(byLabel(st('update.downloadAndUpgrade'))!.disabled).toBe(false)
  })

  it('成功装完的日志不套错误壳', async () => {
    seed({
      status: {
        current: '0.13.0',
        auto_check: true,
        checked_at_ms: CHECKED_AT,
        update_available: true,
        latest: '0.14.0',
        can_self_update: true,
      } as never,
      applyFailed: false,
      applyLog: 'Successfully installed tavotto-0.14.0',
    })
    await render()
    expect(text()).toContain('Successfully installed tavotto-0.14.0')
    expect(text()).not.toContain(st('update.applyFailedRetry'))
  })

  it('桌面通道：装到一半失败后，安装按钮回来了', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopUpdate: { version: '0.14.0' } as never,
      desktopPhase: 'idle',
      desktopError: '签名校验没通过',
    })
    await render()
    expect(text()).toContain('签名校验没通过')
    expect(byLabel(st('update.downloadAndInstall'))!.disabled).toBe(false)
  })
})

/* -------------------------------- 完成 ---------------------------------- */

describe('完成', () => {
  it('桌面通道：装完给重启，且不再给「下载并安装」', async () => {
    seed({
      status: { current: '0.13.0', desktop: true, auto_check: true } as never,
      desktopChecked: true,
      desktopCheckedAtMs: CHECKED_AT,
      desktopUpdate: { version: '0.14.0' } as never,
      desktopPhase: 'installed',
    })
    await render()
    expect(byLabel(st('update.relaunch'))).toBeTruthy()
    expect(text()).toContain(st('update.installedHint'))
    expect(byLabel(st('update.downloadAndInstall'))).toBeUndefined()
  })

  it('浏览器通道：装完只能提示重启，不假装已经换了版本', async () => {
    seed({
      status: {
        current: '0.13.0',
        auto_check: true,
        checked_at_ms: CHECKED_AT,
        update_available: true,
        latest: '0.14.0',
        can_self_update: true,
      } as never,
      restartRequired: true,
      applyLog: 'Successfully installed tavotto-0.14.0',
    })
    await render()
    expect(text()).toContain(st('update.restartStrong'))
    // 重启之前那个按钮必须消失：再点一次只会再装一遍已经装好的东西
    expect(byLabel(st('update.downloadAndUpgrade'))).toBeUndefined()
  })
})
