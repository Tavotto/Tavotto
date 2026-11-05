/**
 * 设置 › 更新（桌面）里的「后台下载新版本」开关，以及后台已下好时的主动作。
 * 主语：开关认 id `setting-update-auto-download`（role=switch 的 aria-checked）；偏好经 PATCH 落后端配置。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { UpdateSettings } from '@/components/settings/UpdateSettings'
import { useUpdateStore } from '@/store/updateStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
const toggle = () => document.querySelector<HTMLButtonElement>('#setting-update-auto-download')!
const noop = async () => {}

function seed(patch: Record<string, unknown> = {}) {
  useUpdateStore.setState({
    ...useUpdateStore.getInitialState(),
    status: {
      desktop: true,
      current: '0.20.0',
      auto_check: false,
      auto_download: true,
      repo_url: 'r',
      releases_url: 'u',
    } as never,
    desktopChecked: true,
    check: noop,
    checkDesktop: noop,
    ...patch,
  })
}

async function render() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<UpdateSettings />))
}

beforeEach(() => {
  document.body.innerHTML = ''
})
afterEach(async () => {
  await act(async () => root?.unmount())
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('后台下载开关', () => {
  it('默认开；名字是可见的那行字；点一下把 false 写到后端配置', async () => {
    const fetchMock = vi.fn((_u: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ auto_check: false, auto_download: JSON.parse(String(init?.body)).auto_download }),
      } as Response),
    )
    vi.stubGlobal('fetch', fetchMock)
    seed()
    await render()
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    const label = document.getElementById('setting-update-auto-download-label')!
    expect(label.textContent).toBe('后台下载新版本')
    expect(toggle().getAttribute('aria-labelledby')).toBe(label.id)
    await act(async () => toggle().click())
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]!.body))).toEqual({ auto_download: false })
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })

  it('关着时开关显示关', async () => {
    seed({ status: { desktop: true, current: '0.20.0', auto_check: false, auto_download: false, repo_url: 'r', releases_url: 'u' } })
    await render()
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })
})

describe('后台已下好', () => {
  it('主动作是「现在重启」（用已下好的包），不是「下载并安装」', async () => {
    seed({
      desktopUpdate: { version: '9.9.9' },
      desktopReady: { version: '9.9.9', source: 'mirror' },
    })
    await render()
    const label = [...document.querySelectorAll('button[data-update-relaunch]')].map((b) => b.textContent)
    expect(label).toEqual(['现在重启'])
  })

  it('没下好时仍是「下载并安装」（后台失败后的手动入口）', async () => {
    seed({ desktopUpdate: { version: '9.9.9' }, desktopReady: null })
    await render()
    expect(document.querySelector('button[data-update-relaunch]')).toBeNull()
    expect([...document.querySelectorAll('button')].some((b) => b.textContent === '下载并安装')).toBe(true)
  })
})
