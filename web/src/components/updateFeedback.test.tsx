import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', () => ({
  isDesktop: () => false, checkDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(), relaunchDesktop: vi.fn(),
}))
vi.mock('@/lib/telemetry', () => ({ captureTelemetry: vi.fn() }))

import { i18n, t } from '@/i18n'
import type { UpdateStatus } from '@/lib/api'
import { relaunchDesktop } from '@/lib/desktop'
import { readDismissedUpdate } from '@/lib/updateNotice'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { UpdateNoticeDialog } from './UpdateNoticeDialog'
import { UpdateSettings } from './settings/UpdateSettings'

const status = (autoCheck = true): UpdateStatus => ({
  current: '0.14.0', auto_check: autoCheck,
  repo_url: 'https://example.test', releases_url: 'https://example.test/releases',
})
// Captured from real Flask dispatch after the authenticated session was invalidated.
const expiredSession = { code: 'session_auth_required', error: '会话未建立或已失效' }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
let root: Root | undefined
let host: HTMLDivElement | undefined
const query = <T extends Element = HTMLElement>(selector: string) => {
  const found = document.querySelector<T>(selector)
  expect(found, selector).not.toBeNull()
  return found!
}
const toggle = () => query<HTMLButtonElement>('[data-update-auto] button')
const notice = () => query('[data-dialog="update-notice"]')
const restart = () => query<HTMLButtonElement>('[data-update-relaunch]')
const settingError = () => query('[data-update-auto-error]')
const restartError = () => query('[data-update-relaunch-error]')
async function mount(node: ReactNode) {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => { root!.render(node) })
}
async function unmount() {
  if (root) await act(async () => root!.unmount())
  root = undefined; host?.remove(); host = undefined
}
function installed() {
  useUpdateStore.setState({
    status: { ...status(), desktop: true }, desktopChecked: true,
    desktopPhase: 'installed', desktopUpdate: { version: '0.15.0' },
  })
}
beforeEach(async () => {
  localStorage.clear()
  useUpdateStore.setState({ ...useUpdateStore.getInitialState(), status: status() }, true)
  useTelemetryStore.setState({ askOpen: false }); useNativeSessionStore.setState({ pendingQueue: [] })
  vi.mocked(relaunchDesktop).mockReset()
  await i18n.changeLanguage('en-US')
})
afterEach(async () => { await unmount(); vi.unstubAllGlobals(); await i18n.changeLanguage('zh-CN') })

describe('browser update preference feedback', () => {
  it('catches a real 401-shaped failure, retains the confirmed setting, and translates recovery after switching languages or reopening', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(expiredSession, 401)))
    await mount(<UpdateSettings />)
    await act(async () => toggle().click())
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    expect(toggle().disabled).toBe(false)
    expect(settingError().textContent).toContain('last confirmed')
    expect(settingError().textContent).toContain(t('backend.session_auth_required', { ns: 'errors' }))
    expect(settingError().textContent).toContain('Relaunch')
    expect(query<HTMLButtonElement>('[data-update-auto-retry]').disabled).toBe(false)
    const english = settingError().textContent
    await act(async () => { await i18n.changeLanguage('zh-CN') })
    expect(settingError().textContent).toContain(t('backend.session_auth_required', { ns: 'errors' }))
    expect(settingError().textContent).not.toBe(english)
    await unmount(); await mount(<UpdateSettings />)
    expect(settingError().textContent).toContain(t('backend.session_auth_required', { ns: 'errors' }))
  })
  it('locks writes, catches connection errors, and retries the original choice exactly once', async () => {
    const first = deferred<Response>(); const second = deferred<Response>()
    const fetchMock = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    vi.stubGlobal('fetch', fetchMock)
    await mount(<UpdateSettings />)
    await act(async () => { toggle().click(); toggle().click() })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(toggle().disabled).toBe(true)
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    expect(query('[data-update-auto]').textContent).toContain('Saving')
    await act(async () => { await useUpdateStore.getState().setAutoCheck(true) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await act(async () => { first.reject(new TypeError('Failed to fetch')) })
    expect(settingError().textContent).toContain('Failed to fetch')
    expect(toggle().disabled).toBe(false)
    await act(async () => {
      const retry = query<HTMLButtonElement>('[data-update-auto-retry]'); retry.click(); retry.click()
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ auto_check: false })
    expect(document.querySelector('[data-update-auto-error]')).toBeNull()
    expect(toggle().disabled).toBe(true)
    await act(async () => { second.resolve(response({ auto_check: false })) })
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    expect(toggle().disabled).toBe(false)
    expect(document.querySelector('[data-update-auto-error]')).toBeNull()
  })
  it('uses the acknowledged server value rather than assuming the requested value was saved', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ auto_check: true })))
    await useUpdateStore.getState().setAutoCheck(false)
    expect(useUpdateStore.getState().status?.auto_check).toBe(true)
  })
  it.each(['before', 'during'] as const)('a check started %s a write cannot restore a stale preference', async (timing) => {
    const read = deferred<Response>(); const write = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn((url) => String(url).includes('/settings') ? write.promise : read.promise))
    let checking: Promise<void>; let saving: Promise<void>
    if (timing === 'before') {
      checking = useUpdateStore.getState().check(true); saving = useUpdateStore.getState().setAutoCheck(false)
    } else {
      saving = useUpdateStore.getState().setAutoCheck(false); checking = useUpdateStore.getState().check(true)
    }
    write.resolve(response({ auto_check: false })); await saving
    read.resolve(response({ ...status(), latest: '0.16.0', update_available: true })); await checking
    expect(useUpdateStore.getState().status?.auto_check).toBe(false)
    expect(useUpdateStore.getState().status?.latest).toBe('0.16.0')
    vi.stubGlobal('fetch', vi.fn(async () => response(status(true))))
    await useUpdateStore.getState().check(true)
    expect(useUpdateStore.getState().status?.auto_check).toBe(true)
  })
  it('finishes a pending save after Settings is closed and reopened without permitting another write', async () => {
    useUpdateStore.setState({ status: status(false) })
    const pending = deferred<Response>()
    const fetchMock = vi.fn(() => pending.promise)
    vi.stubGlobal('fetch', fetchMock)
    await mount(<UpdateSettings />)
    await act(async () => toggle().click())
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    await unmount()
    await mount(<UpdateSettings />)
    expect(toggle().disabled).toBe(true)
    await act(async () => toggle().click())
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await act(async () => { pending.resolve(response({ auto_check: true })) })
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    expect(toggle().disabled).toBe(false)
  })

  it('a check settling during a write cannot change the confirmed setting, even when the write fails', async () => {
    const read = deferred<Response>()
    const write = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn((url) => String(url).includes('/settings') ? write.promise : read.promise))
    const saving = useUpdateStore.getState().setAutoCheck(false)
    const checking = useUpdateStore.getState().check(true)
    read.resolve(response({ ...status(false), latest: '0.16.0' }))
    await checking
    expect(useUpdateStore.getState().status?.auto_check).toBe(true)
    expect(useUpdateStore.getState().status?.latest).toBe('0.16.0')
    write.resolve(response(expiredSession, 401))
    await saving
    expect(useUpdateStore.getState().status?.auto_check).toBe(true)
    expect(useUpdateStore.getState().autoCheckFailure?.value).toBe(false)
  })

  it('disables unknown preferences while initial status loads', async () => {
    const read = deferred<Response>(); const fetchMock = vi.fn(() => read.promise)
    vi.stubGlobal('fetch', fetchMock); useUpdateStore.setState({ status: null })
    await mount(<UpdateSettings />)
    expect(toggle().disabled).toBe(true)
    await act(async () => { await useUpdateStore.getState().setAutoCheck(false) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await act(async () => { read.resolve(response(status(false))) })
    expect(toggle().disabled).toBe(false)
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })
})
describe('installed desktop restart feedback', () => {
  it.each(['en-US', 'zh-CN'])('shows IPC rejection and quit/reopen guidance in %s while keeping the update installed', async (locale) => {
    await i18n.changeLanguage(locale); installed()
    vi.mocked(relaunchDesktop).mockRejectedValue(new Error('IPC restart denied'))
    await mount(<UpdateNoticeDialog />); await act(async () => restart().click())
    expect(restartError().textContent).toContain(t('update.relaunchFailed', { ns: 'errors' }))
    expect(restartError().textContent).toContain('IPC restart denied')
    expect(restartError().querySelector('[role="alert"]')).not.toBeNull()
    expect(useUpdateStore.getState().desktopPhase).toBe('installed')
    expect(notice().textContent).toContain('0.15.0'); expect(restart().disabled).toBe(false)
    const previous = restartError().textContent
    await act(async () => { await i18n.changeLanguage(locale === 'en-US' ? 'zh-CN' : 'en-US') })
    expect(restartError().textContent).not.toBe(previous)
    expect(restartError().textContent).toContain(t('update.relaunchFailed', { ns: 'errors' }))
  })
  it('guards repeated restart calls, locks dismissal in flight, and clears the error when retrying', async () => {
    installed(); const first = deferred<void>(); const second = deferred<void>()
    vi.mocked(relaunchDesktop).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    await mount(<UpdateNoticeDialog />)
    await act(async () => { restart().click(); restart().click() })
    expect(relaunchDesktop).toHaveBeenCalledTimes(1); expect(restart().disabled).toBe(true)
    expect(notice().getAttribute('aria-busy')).toBe('true')
    expect(query<HTMLButtonElement>('[data-update-dismiss]').disabled).toBe(true)
    await act(async () => {
      await useUpdateStore.getState().relaunch()
      query<HTMLButtonElement>('[data-update-dismiss]').click()
      notice().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(relaunchDesktop).toHaveBeenCalledTimes(1); expect(readDismissedUpdate()).toBeNull()
    expect(notice()).toBeTruthy()
    await act(async () => { first.reject('native IPC rejected') })
    expect(restartError().textContent).toContain('native IPC rejected'); expect(restart().disabled).toBe(false)
    await act(async () => restart().click())
    expect(document.querySelector('[data-update-relaunch-error]')).toBeNull(); expect(restart().disabled).toBe(true)
    await act(async () => { second.resolve() })
    expect(relaunchDesktop).toHaveBeenCalledTimes(2)
    expect(useUpdateStore.getState().desktopPhase).toBe('installed')
    expect(document.querySelector('[data-update-relaunch-error]')).toBeNull(); expect(restart().disabled).toBe(false)
  })
  it('also disables the Settings restart control while the shared action is pending', async () => {
    installed()
    const pending = deferred<void>()
    vi.mocked(relaunchDesktop).mockReturnValue(pending.promise)
    await mount(<UpdateSettings />)
    await act(async () => { restart().click(); restart().click() })
    expect(restart().disabled).toBe(true)
    expect(relaunchDesktop).toHaveBeenCalledTimes(1)
    await unmount()
    await mount(<UpdateNoticeDialog />)
    expect(restart().disabled).toBe(true)
    await act(async () => { pending.reject(new Error('IPC refused')) })
    expect(restart().disabled).toBe(false)
    expect(restartError().textContent).toContain('IPC refused')
  })

  it('dismisses a failed restart and retries from Settings with its recovery information intact', async () => {
    installed(); vi.mocked(relaunchDesktop).mockRejectedValueOnce(null).mockResolvedValueOnce(undefined)
    await mount(<UpdateNoticeDialog />); await act(async () => restart().click())
    expect(restartError().textContent).toContain('Quit and reopen')
    await act(async () => query<HTMLButtonElement>('[data-update-dismiss]').click())
    expect(document.querySelector('[data-dialog="update-notice"]')).toBeNull()
    expect(readDismissedUpdate()).toBe('0.15.0')
    await unmount(); await mount(<UpdateSettings />)
    expect(restartError().textContent).toContain('Quit and reopen')
    await act(async () => restart().click())
    expect(relaunchDesktop).toHaveBeenCalledTimes(2)
    expect(document.querySelector('[data-update-relaunch-error]')).toBeNull()
    expect(useUpdateStore.getState().desktopPhase).toBe('installed')
  })
})
