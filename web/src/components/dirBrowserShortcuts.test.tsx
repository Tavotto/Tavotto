/**
 * 目录选择器的常用起点按界面语言显示（#668 评审）。
 *
 * 后端 `_browse_shortcuts` 的 `name` 是中文写死的：英文界面上会冒出「主目录 / 桌面」，
 * 而且那一格曾经叫「文档」——界面名词里没有这个词（ADR 0001 2026-09-26 修订）。现在后端
 * 多给一个稳定的 `id`，界面按它查自己的语言包；老后端没有 `id`、或 `id` 不认识时回退 `name`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DirBrowser } from '@/components/DirBrowser'
import { setLocale } from '@/i18n'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const SHORTCUTS = [
  { id: 'home', name: '主目录', path: '/u' },
  { id: 'desktop', name: '桌面', path: '/u/Desktop' },
  { id: 'documents', name: '文稿', path: '/u/Documents' },
  { id: 'downloads', name: '下载', path: '/u/Downloads' },
  // 老后端（没有 id）与将来的新 id：原样显示后端给的名字
  { name: '旧后端的名字', path: '/u/old' },
  { id: 'future', name: '以后才有的', path: '/u/future' },
]

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  globalThis.fetch = vi.fn(async () =>
    new Response(
      JSON.stringify({ path: '/u', parent: '/', dirs: [], roots: [], shortcuts: SHORTCUTS, is_roots: false }),
      { status: 200 },
    ),
  ) as unknown as typeof fetch
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  act(() => root.unmount())
  host.remove()
  await setLocale('zh-CN')
})

async function mountAndReadChips(): Promise<string[]> {
  await act(async () => {
    root.render(<DirBrowser mode="open" onClose={() => {}} onPick={() => {}} />)
  })
  // 常用起点的 chip：title 是路径
  return SHORTCUTS.map(
    (s) => document.querySelector<HTMLButtonElement>(`button[title="${s.path}"]`)?.textContent?.trim() ?? '∅',
  )
}

describe('常用起点的显示名', () => {
  it('英文界面：四个已知起点按 id 翻成英文，不再露出后端的中文', async () => {
    await setLocale('en-US')
    expect(await mountAndReadChips()).toEqual([
      'Home',
      'Desktop',
      'Documents',
      'Downloads',
      '旧后端的名字',
      '以后才有的',
    ])
  })

  it('中文界面：Documents 叫「文稿」（访达里的名字），没有「文档」', async () => {
    await setLocale('zh-CN')
    const chips = await mountAndReadChips()
    expect(chips.slice(0, 4)).toEqual(['主目录', '桌面', '文稿', '下载'])
    expect(chips.join(' ')).not.toContain('文档')
  })
})

describe('目录列表的键盘与焦点（2026-10-07 设计审计 §10.2）', () => {
  const DIRS = [
    { name: 'a', path: '/u/a' },
    { name: 'b', path: '/u/b' },
    { name: 'c', path: '/u/c' },
  ]
  const respond = () =>
    vi.fn(async (input: RequestInfo | URL) => {
      const at = new URL(String(input), 'http://x').searchParams.get('path') ?? '/u'
      return new Response(
        JSON.stringify({
          path: at,
          parent: at === '/' ? null : '/',
          dirs: at === '/u' ? DIRS : [],
          roots: [],
          shortcuts: [],
          is_roots: false,
        }),
        { status: 200 },
      )
    })

  it('新建：打开时焦点直接在名字框（initialFocusRef）', async () => {
    globalThis.fetch = respond() as unknown as typeof fetch
    await act(async () => {
      root.render(<DirBrowser mode="create" onClose={() => {}} onPick={() => {}} />)
    })
    expect(document.activeElement).toBe(document.querySelector('[data-dir-browser-name]'))
  })

  it('↑↓ 在行间走（Tab 只停一行），⌫ 回上一级', async () => {
    const fetchMock = respond()
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await act(async () => {
      root.render(<DirBrowser mode="open" initialPath="/u" onClose={() => {}} onPick={() => {}} />)
    })
    const rows = () => [...document.querySelectorAll<HTMLButtonElement>('[data-dir-row]')]
    expect(rows().map((r) => r.tabIndex)).toEqual([0, -1, -1])
    act(() => rows()[0].focus())
    const key = async (k: string) =>
      act(async () => {
        document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))
      })
    await key('ArrowDown')
    expect(document.activeElement).toBe(rows()[1])
    expect(rows().map((r) => r.tabIndex)).toEqual([-1, 0, -1])
    await key('ArrowUp')
    expect(document.activeElement).toBe(rows()[0])
    fetchMock.mockClear()
    await key('Backspace')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(new URL(String(fetchMock.mock.calls[0][0]), 'http://x').searchParams.get('path')).toBe('/')
  })

  it('⌫ 回上一级：等响应落地、新清单渲染后焦点落在新清单第一行（Codex #831 P2）', async () => {
    const PARENT_DIRS = [
      { name: 'u', path: '/u' },
      { name: 'v', path: '/v' },
    ]
    let release!: () => void
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const at = new URL(String(input), 'http://x').searchParams.get('path') ?? '/u'
      // 上一级的响应慢于一帧：手动放行
      if (at === '/') await new Promise<void>((r) => (release = r))
      return new Response(
        JSON.stringify({
          path: at,
          parent: at === '/' ? null : '/',
          dirs: at === '/u' ? DIRS : PARENT_DIRS,
          roots: [],
          shortcuts: [],
          is_roots: false,
        }),
        { status: 200 },
      )
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await act(async () => {
      root.render(<DirBrowser mode="open" initialPath="/u" onClose={() => {}} onPick={() => {}} />)
    })
    const rows = () => [...document.querySelectorAll<HTMLButtonElement>('[data-dir-row]')]
    act(() => rows()[1].focus())
    await act(async () => {
      document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }))
    })
    // 等过不止一帧，响应还没回来
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(rows().map((r) => r.dataset.dirRow)).toEqual(['a', 'b', 'c'])
    await act(async () => {
      release()
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(rows().map((r) => r.dataset.dirRow)).toEqual(['u', 'v'])
    expect(document.activeElement).toBe(document.querySelector('[data-dir-row="u"]'))
    expect(rows().map((r) => r.tabIndex)).toEqual([0, -1])
  })
})
