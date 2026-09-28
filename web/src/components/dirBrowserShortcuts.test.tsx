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
