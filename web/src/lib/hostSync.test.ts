import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  runHostIntegration: vi.fn().mockResolvedValue('{}'),
}))

import { runHostIntegration } from '@/lib/desktop'
import { resetHostSyncForTest, startHostSync } from './hostSync'

const run = vi.mocked(runHostIntegration)
const desktop = (on: boolean) => {
  if (on)
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  else
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
}

describe('startHostSync', () => {
  beforeEach(() => {
    run.mockReset()
    run.mockResolvedValue('{}')
    resetHostSyncForTest()
    desktop(true)
  })

  it('sends plain sync when background auto-download is off or unknown', () => {
    startHostSync(false)
    expect(run).toHaveBeenCalledTimes(1)
    expect(run).toHaveBeenCalledWith('sync')
  })

  it('sends sync_plugins only when auto-download is on', () => {
    startHostSync(true)
    expect(run).toHaveBeenCalledWith('sync_plugins')
  })

  it('does not send in the browser', () => {
    desktop(false)
    startHostSync(true)
    expect(run).not.toHaveBeenCalled()
  })

  it('sends once per app run and swallows failures', async () => {
    run.mockRejectedValue(new Error('boom'))
    startHostSync(true)
    startHostSync(false)
    await Promise.resolve()
    expect(run).toHaveBeenCalledTimes(1)
  })
})
