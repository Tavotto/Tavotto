import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DirBrowser } from './DirBrowser'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const HOME = '/home/old'
const TARGET = 'E:/new-parent'
const NAME = '研究课题'
type Mode = 'open' | 'create'
interface BrowseRequest {
  path: string | null
  done: boolean
  release: (result?: { ok?: boolean; nearest?: string }) => void
}
let root: Root
let host: HTMLDivElement
const requests: BrowseRequest[] = []
const picked: { path: string; create: boolean }[] = []
const pathInput = () => document.querySelector<HTMLInputElement>('[data-dir-browser-path]')!
const confirm = () => document.querySelector<HTMLButtonElement>('[data-dir-browser-confirm]')!

beforeEach(() => {
  requests.length = 0
  picked.length = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => new Promise<Response>(resolve => {
    const path = new URL(String(input), 'http://test').searchParams.get('path')
    const request: BrowseRequest = { path, done: false, release: ({ ok = true, nearest } = {}) => {
      if (request.done) return
      request.done = true
      const resolved = path ?? HOME
      resolve(ok ? Response.json({ path: resolved, parent: '/', dirs: [{ name: 'child', path: `${resolved}/child` }],
        roots: [], shortcuts: [], is_roots: false }) : Response.json({ code: 'x', error: 'unavailable',
        ...(nearest ? { nearest } : {}) }, { status: 400 }))
    } }
    requests.push(request)
  })))
})

afterEach(async () => {
  act(() => root.unmount())
  await act(async () => { for (const request of requests) request.release() })
  host.remove()
  vi.unstubAllGlobals()
})

const input = (selector: string, value: string) => act(() => {
  const field = document.querySelector<HTMLInputElement>(selector)!
  field.focus()
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const navigate = (path: string) => {
  input('[data-dir-browser-path]', path)
  // Enter in the address field submits this original form handler.
  act(() => pathInput().form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
const land = (request: BrowseRequest, result?: Parameters<BrowseRequest['release']>[0]) =>
  act(async () => request.release(result))
const mount = async (mode: Mode, fallbackPath?: string) => {
  await act(async () => root.render(<DirBrowser mode={mode} fallbackPath={fallbackPath} onClose={() => {}}
    onPick={(path, create) => picked.push({ path, create })} />))
  if (mode === 'create') input('[data-dir-browser-name]', NAME)
}
const expectedPick = (mode: Mode, path = TARGET) => [{ path: mode === 'create' ? `${path}/${NAME}` : path, create: mode === 'create' }]

describe('目录导航与确认属于最新用户意图', () => {
  it.each(['create', 'open'] as const)('%s：新导航pending时初始home落地不能授旧目录确认', async mode => {
    await mount(mode)
    navigate(TARGET)
    expect(requests.map(row => row.path)).toEqual([null, TARGET])
    await land(requests[0])
    expect(requests[1].done).toBe(false)
    expect(pathInput().value).toBe(TARGET)
    expect(confirm().disabled).toBe(true)
    act(() => confirm().click())
    expect(picked).toEqual([])
    await land(requests[1])
    expect(confirm().disabled).toBe(false)
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick(mode))
  })

  it.each(['create', 'open'] as const)('%s：新导航已落地后旧home迟到不能覆盖地址/目标/名称或抢焦点', async mode => {
    await mount(mode)
    navigate(TARGET)
    await land(requests[1])
    expect(pathInput().value).toBe(TARGET)
    await land(requests[0])
    expect(pathInput().value).toBe(TARGET)
    expect(document.activeElement).toBe(pathInput())
    if (mode === 'create') expect(document.querySelector<HTMLInputElement>('[data-dir-browser-name]')!.value).toBe(NAME)
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick(mode))
  })

  it.each(['create', 'open'] as const)('%s：已settled目录的确认资格在下一导航开始立即撤销，实际响应后恢复', async mode => {
    await mount(mode)
    await land(requests[0])
    expect(confirm().disabled).toBe(false)
    navigate(TARGET)
    expect(pathInput().value).toBe(TARGET)
    expect(confirm().disabled).toBe(true)
    act(() => confirm().click())
    expect(picked).toEqual([])
    await land(requests[1])
    expect(confirm().disabled).toBe(false)
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick(mode))
  })

  it('初始失败迟到不再触发fallback，保后来已完成导航的确认资格', async () => {
    await mount('create', '/fallback')
    navigate(TARGET)
    await land(requests[1])
    await land(requests[0], { ok: false })
    expect(requests.map(row => row.path)).toEqual([null, TARGET])
    expect(pathInput().value).toBe(TARGET)
    expect(document.querySelector('[data-dir-browser-nearest]')).toBeNull()
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick('create'))
  })

  it.each(['create', 'open'] as const)('%s：未提交地址草稿或失焦不能借旧目录确认，Enter完成后自然可选', async mode => {
    await mount(mode)
    await land(requests[0])
    input('[data-dir-browser-path]', TARGET)
    expect(requests).toHaveLength(1)
    expect(confirm().disabled).toBe(true)
    act(() => pathInput().blur())
    act(() => confirm().click())
    expect(picked).toEqual([])
    navigate(TARGET)
    await land(requests[1])
    expect(confirm().disabled).toBe(false)
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick(mode))
  })

  it('当前请求失败保nearest入口，明确导航完成后才恢复确认', async () => {
    await mount('open')
    await land(requests[0])
    navigate('/missing')
    await land(requests[1], { ok: false, nearest: '/nearest' })
    expect(confirm().disabled).toBe(true)
    const nearest = document.querySelector<HTMLButtonElement>('[data-dir-browser-nearest]')!
    expect(nearest).not.toBeNull()
    act(() => nearest.click())
    expect(requests[2].path).toBe('/nearest')
    expect(confirm().disabled).toBe(true)
    await land(requests[2])
    expect(document.querySelector('[data-dir-browser-nearest]')).toBeNull()
    expect(confirm().disabled).toBe(false)
    act(() => confirm().click())
    expect(picked).toEqual(expectedPick('open', '/nearest'))
  })
})
