/**
 * 同意态的三档语义与「首启只问一次」。
 *
 * 最要紧的一条：**`unset` 与 `disabled` 必须分得开**。合成一个布尔的话，
 * 「用户说过不」和「还没问过」长得一模一样，于是每次启动都再问一遍。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  fetchTelemetrySettings: vi.fn(),
  patchTelemetryConsent: vi.fn(),
}))

import { fetchTelemetrySettings, patchTelemetryConsent } from '@/lib/api'
import { telemetryEnabled, setTelemetryEnabled } from '@/lib/telemetry'
import { useTelemetryStore } from '@/store/telemetryStore'

const fetchMock = vi.mocked(fetchTelemetrySettings)
const patchMock = vi.mocked(patchTelemetryConsent)

const settings = (over: Partial<Awaited<ReturnType<typeof fetchTelemetrySettings>>> = {}) => ({
  consent: 'unset' as const,
  enabled: false,
  hard_disabled: false,
  consent_version: 1,
  saved_consent_version: 0,
  ...over,
})

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
  setTelemetryEnabled(false)
  useTelemetryStore.setState({ settings: null, askOpen: false })
})

describe('load', () => {
  it('unset → 该问一次，且此时还没打开发送', async () => {
    fetchMock.mockResolvedValue(settings())
    await useTelemetryStore.getState().load()
    expect(useTelemetryStore.getState().askOpen).toBe(true)
    expect(telemetryEnabled()).toBe(false)
  })

  it('enabled → 不问，直接打开发送', async () => {
    fetchMock.mockResolvedValue(settings({ consent: 'enabled', enabled: true }))
    await useTelemetryStore.getState().load()
    expect(useTelemetryStore.getState().askOpen).toBe(false)
    expect(telemetryEnabled()).toBe(true)
  })

  it('disabled → 不再问（说过不就是不）', async () => {
    fetchMock.mockResolvedValue(settings({ consent: 'disabled' }))
    await useTelemetryStore.getState().load()
    expect(useTelemetryStore.getState().askOpen).toBe(false)
    expect(telemetryEnabled()).toBe(false)
  })

  it('硬开关关着时不问——点了也没用的框只会让人以为是自己关的', async () => {
    fetchMock.mockResolvedValue(settings({ hard_disabled: true }))
    await useTelemetryStore.getState().load()
    expect(useTelemetryStore.getState().askOpen).toBe(false)
    expect(telemetryEnabled()).toBe(false)
  })

  it('取不到设置就当没开，也不问', async () => {
    fetchMock.mockRejectedValue(new Error('offline'))
    await useTelemetryStore.getState().load()
    expect(useTelemetryStore.getState().askOpen).toBe(false)
    expect(telemetryEnabled()).toBe(false)
    expect(useTelemetryStore.getState().settings).toBeNull()
  })
})

describe('choose', () => {
  it('同意 → 写 enabled 并立刻开始发送', async () => {
    patchMock.mockResolvedValue(settings({ consent: 'enabled', enabled: true }))
    await useTelemetryStore.getState().choose('enabled', 'first_run')
    expect(patchMock).toHaveBeenCalledWith('enabled', 'first_run')
    expect(telemetryEnabled()).toBe(true)
    expect(useTelemetryStore.getState().askOpen).toBe(false)
  })

  it('「暂不」写的是 disabled，不是留在 unset', async () => {
    patchMock.mockResolvedValue(settings({ consent: 'disabled' }))
    await useTelemetryStore.getState().choose('disabled', 'first_run')
    expect(patchMock).toHaveBeenCalledWith('disabled', 'first_run')
    expect(telemetryEnabled()).toBe(false)
  })

  it('设置里关掉立刻停止发送', async () => {
    setTelemetryEnabled(true)
    patchMock.mockResolvedValue(settings({ consent: 'disabled' }))
    await useTelemetryStore.getState().choose('disabled', 'settings')
    expect(telemetryEnabled()).toBe(false)
  })

  it('写入失败也不会让发送悄悄开着', async () => {
    setTelemetryEnabled(true)
    patchMock.mockRejectedValue(new Error('offline'))
    await useTelemetryStore.getState().choose('enabled', 'settings')
    expect(telemetryEnabled()).toBe(false)
    expect(useTelemetryStore.getState().askOpen).toBe(false)
  })
})
