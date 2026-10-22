/**
 * Windows 卸载后重装 → 新手态（NSIS 卸载记号，ADR 0136 修订 2026-10-21）：
 * 后端回 reinstalled=true → 前端把 onboarding 迁回新手态（not_started、welcomeSeenAt 清空、hintSeen 保留）
 * 并弹一次欢迎页，再 ack；没有记号（应用内更新 / 非 Windows）完全不动；只处理一次。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fetchMarker = vi.fn()
const ackMarker = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchReinstallMarker: () => fetchMarker(),
  ackReinstallMarker: () => ackMarker(),
}))

let desktop = true
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: () => desktop,
}))

import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { resetTutorialAutoOpenForTest, useAutoOpen } from './autoOpen'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Probe() {
  useAutoOpen()
  return null
}

let host: HTMLDivElement
let root: Root
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const mount = async () => {
  await act(async () => root.render(<Probe />))
  await flush()
}
const ob = () => useOnboardingStore.getState()

beforeEach(() => {
  localStorage.clear()
  configureOnboardingPersistence(null)
  ob().resetOnboarding()
  useProjectStore.setState({ phase: 'none', recent: [], pinned: [], project: null, listsLoaded: true } as never)
  resetTutorialAutoOpenForTest()
  desktop = true
  fetchMarker.mockReset()
  ackMarker.mockReset()
  ackMarker.mockResolvedValue({ consumed: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

const finishedUser = () =>
  useOnboardingStore.setState({
    status: 'completed',
    completedSteps: ['demo_import'],
    welcomeSeenAt: 111,
    hintSeen: { layout_page_bounds: 5 },
  } as never)

describe('卸载后重装的记号', () => {
  it('有记号：老手被迁回新手态（hintSeen 保留）、弹欢迎页、ack 一次', async () => {
    finishedUser()
    fetchMarker.mockResolvedValue({ reinstalled: true })
    await mount()
    expect(ob().status).toBe('not_started')
    expect(ob().welcomeSeenAt).toBeTypeOf('number') // 清空后欢迎页弹出又记了新的一次
    expect(ob().welcomeSeenAt).not.toBe(111)
    expect(ob().completedSteps).toEqual([])
    expect(ob().hintSeen).toEqual({ layout_page_bounds: 5 })
    expect(ob().surface).toBe('welcome')
    expect(ackMarker).toHaveBeenCalledTimes(1)
  })

  it('浏览器模式（非桌面客户端）：不取、不 ack、不重置——记号留给真正重装的桌面客户端（Codex #1272）', async () => {
    desktop = false
    finishedUser()
    fetchMarker.mockResolvedValue({ reinstalled: true })
    await mount()
    expect(fetchMarker).not.toHaveBeenCalled()
    expect(ackMarker).not.toHaveBeenCalled()
    expect(ob().status).toBe('completed')
    expect(ob().welcomeSeenAt).toBe(111)
  })

  it('没有记号（应用内更新 / 非 Windows）：状态一字不动、不 ack', async () => {
    finishedUser()
    fetchMarker.mockResolvedValue({ reinstalled: false })
    await mount()
    expect(ob().status).toBe('completed')
    expect(ob().welcomeSeenAt).toBe(111)
    expect(ob().surface).toBe('none')
    expect(ackMarker).not.toHaveBeenCalled()
  })

  it('取不到记号状态（浏览器 / 旧后端）：当没有，不抛不动', async () => {
    finishedUser()
    fetchMarker.mockRejectedValue(new Error('HTTP 404'))
    await mount()
    expect(ob().status).toBe('completed')
    expect(ackMarker).not.toHaveBeenCalled()
  })

  it('只重置一次：同一会话再挂载（App 两个分支各挂一份）不再询问、不再重置', async () => {
    finishedUser()
    fetchMarker.mockResolvedValue({ reinstalled: true })
    await mount()
    ob().skip()
    const after = ob().status
    act(() => root.unmount())
    root = createRoot(host)
    await mount()
    expect(fetchMarker).toHaveBeenCalledTimes(1)
    expect(ob().status).toBe(after)
    expect(ackMarker).toHaveBeenCalledTimes(1)
  })

  it('ack 失败不回滚：状态已是新手态，记号留给下次启动', async () => {
    finishedUser()
    fetchMarker.mockResolvedValue({ reinstalled: true })
    ackMarker.mockRejectedValue(new Error('boom'))
    await mount()
    expect(ob().status).toBe('not_started')
  })
})
