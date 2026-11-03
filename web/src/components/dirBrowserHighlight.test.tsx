/**
 * DirBrowser 的 `highlight` / `fallbackPath`（导出对话框「位置 › 更改…」用）：
 * 从当前目录的上一级打开、当前目录那一行高亮并成为漫游焦点；起点打不开时回落。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DirBrowser } from '@/components/DirBrowser'
import { initI18n } from '@/i18n'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
const asked: string[] = []

const listing = (path: string) => ({
  path,
  parent: '/p',
  dirs: [
    { name: 'a', path: `${path}/a` },
    { name: 'export', path: `${path}/export` },
    { name: 'z', path: `${path}/z` },
  ],
  roots: [],
  shortcuts: [],
  is_roots: false,
})

beforeEach(() => {
  initI18n('zh-CN')
  asked.length = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

const mount = (props: Partial<Parameters<typeof DirBrowser>[0]>) =>
  act(async () => {
    root.render(<DirBrowser mode="open" onClose={() => {}} onPick={() => {}} {...props} />)
  })
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-dir-row]')]

describe('highlight', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const path = new URL(String(url), 'http://x').searchParams.get('path') ?? ''
      asked.push(path)
      return new Response(JSON.stringify(listing(path)), { status: 200 })
    }) as unknown as typeof fetch
  })

  it('浏览到上一级时，当前目录那一行高亮、是漫游焦点行；别的行不高亮', async () => {
    await mount({ initialPath: '/p/proj', highlight: '/p/proj/export' })
    const cur = rows().filter((r) => r.hasAttribute('data-current'))
    expect(cur.map((r) => r.dataset.dirRow)).toEqual(['export'])
    expect(cur[0].getAttribute('aria-current')).toBe('true')
    expect(cur[0].tabIndex).toBe(0)
    expect(rows().filter((r) => r.tabIndex === 0)).toHaveLength(1)
  })

  it('服务端回的是解析过的路径（同名、同一上级名）也认', async () => {
    await mount({ initialPath: '/p/proj', highlight: '/var/proj/export' })
    expect(rows().filter((r) => r.hasAttribute('data-current')).map((r) => r.dataset.dirRow)).toEqual(['export'])
  })

  it('不给 highlight：没有任何高亮（默认行为不变）', async () => {
    await mount({ initialPath: '/p/proj' })
    expect(rows().some((r) => r.hasAttribute('data-current'))).toBe(false)
  })

  it('点高亮的那一行进去，「确认」选的就是原目录', async () => {
    const picked: string[] = []
    await mount({ initialPath: '/p/proj', highlight: '/p/proj/export', onPick: (p) => picked.push(p) })
    await act(async () => rows().find((r) => r.dataset.dirRow === 'export')!.click())
    await act(async () => document.querySelector<HTMLElement>('[data-dir-browser-confirm]')!.click())
    expect(picked).toEqual(['/p/proj/export'])
  })
})

describe('fallbackPath', () => {
  it('起点打不开 → 回落到 fallbackPath，不留下那条错误', async () => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      const path = new URL(String(url), 'http://x').searchParams.get('path') ?? ''
      asked.push(path)
      if (path === '/p/proj') return new Response(JSON.stringify({ error: 'nope', code: 'x', params: {} }), { status: 400 })
      return new Response(JSON.stringify(listing(path)), { status: 200 })
    }) as unknown as typeof fetch
    await mount({ initialPath: '/p/proj', fallbackPath: '/p/proj/export' })
    expect(asked).toEqual(['/p/proj', '/p/proj/export'])
    expect(document.querySelector<HTMLInputElement>('[data-dir-browser-path]')!.value).toBe('/p/proj/export')
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })

  it('不给 fallbackPath：起点打不开就只显示错误（行为不变）', async () => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      asked.push(new URL(String(url), 'http://x').searchParams.get('path') ?? '')
      return new Response(JSON.stringify({ error: 'nope', code: 'x', params: {} }), { status: 400 })
    }) as unknown as typeof fetch
    await mount({ initialPath: '/p/proj' })
    expect(asked).toEqual(['/p/proj'])
    expect(document.querySelector('[role="alert"]')).not.toBeNull()
  })
})
