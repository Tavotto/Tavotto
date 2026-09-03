/**
 * 关窗询问闸的前端一半（issue #223）。
 *
 * 这里量的是**答复**：壳问一句「能关吗」，六个保存状态各该答什么，用户点了
 * 三选一里的哪个之后窗口该不该关。壳那一半（拦 / 看门狗 / 代号）在
 * `src-tauri/src/main.rs` 的 `CloseGate` 单测里，两侧的词汇表由
 * `tests/test_desktop_close_guard.py` 比对。
 *
 * **jsdom 里 `isDesktop()` 恒假**，所以 `resolveDesktopCloseRequest` 必须换成
 * 假的——不换的话它会一路 return false，而这些用例断言的正是「答了什么」，
 * 换句话说不换就全是恒真的。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject } from '@/types/document'
import type { ProjectDocument, TextObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { answerCloseRequest, beginCloseRequest } from './closeGuard'

/** 前端答给壳的每一句都记在这里 */
const said: string[] = []

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./desktop')>()),
  resolveDesktopCloseRequest: (d: string) => {
    said.push(d)
    return Promise.resolve(true)
  },
}))

const text = (id: string): TextObject => ({
  id, type: 'text', text: 'x', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const seeded = (): ProjectDocument => {
  const pd = emptyProject()
  pd.canvases[0].objects = [text('t0')]
  pd.updatedAt = 1
  return pd
}

const s = () => useDocumentStore.getState()
const tick = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}

/* 假磁盘：只需要「写得进 / 写不进」两档 */
let putOk = true
const puts: string[] = []
const fakeFetch = (async (url: unknown, init?: RequestInit) => {
  const raw = String(url)
  const m = raw.match(/\/api\/autosave\/([^/?]+)/)
  if (!m) return new Response('{}', { status: 404 })
  if (init?.method === 'PUT') {
    puts.push(decodeURIComponent(m[1]))
    return putOk
      ? new Response('{"ok":true,"saved_at":1,"revision":"r1"}', { status: 200 })
      : new Response('{"error":"disk full"}', { status: 500 })
  }
  if (init?.method === 'DELETE') return new Response('{"ok":true}', { status: 200 })
  return new Response('{}', { status: 404 })
}) as typeof fetch

beforeEach(async () => {
  globalThis.fetch = fakeFetch
  said.length = 0
  puts.length = 0
  putOk = true
  localStorage.clear()
  await s().switchDocument(seeded(), 'd_close')
  await tick()
  puts.length = 0
})

afterEach(async () => {
  await tick()
  localStorage.clear()
})

describe('壳问「能关吗」', () => {
  it.each(['clean', 'saved'] as const)('%s：不弹框，直接答「关」', async (state) => {
    useDocumentStore.setState({ saveState: state })
    expect(await beginCloseRequest()).toBe(false)
    expect(said).toEqual(['close'])
  })

  // ADR 0024 的关闭保护正是这四档；与 `beforeunload` 共用 `hasUnsavedWork`，
  // 两处各写一份判据的结果会是「刷新会拦、关窗不拦」。
  it.each(['dirty', 'saving', 'save_error', 'conflict'] as const)(
    '%s：先接手（hold），再让调用方弹三选一',
    async (state) => {
      useDocumentStore.setState({ saveState: state })
      expect(await beginCloseRequest()).toBe(true)
      expect(said).toEqual(['hold'])
    },
  )
})

describe('三选一的答复', () => {
  it('取消：窗口留着，壳收到 cancel', async () => {
    useDocumentStore.setState({ saveState: 'dirty' })
    expect(await answerCloseRequest('cancel')).toBe('stay')
    expect(said).toEqual(['cancel'])
    expect(puts).toEqual([])
  })

  it('不保存：直接关，一次磁盘都不碰', async () => {
    useDocumentStore.setState({ saveState: 'dirty' })
    expect(await answerCloseRequest('discard')).toBe('closing')
    expect(said).toEqual(['close'])
    expect(puts).toEqual([])
  })

  it('保存：真写进磁盘之后才关', async () => {
    useDocumentStore.setState({ saveState: 'dirty' })
    expect(await answerCloseRequest('save')).toBe('closing')
    expect(puts).toEqual(['d_close'])
    expect(said).toEqual(['close'])
  })

  /**
   * 这条是整扇闸最要紧的一位：**存不成就不能关**。
   * 存不成还照关，用户按下的那个「保存」就成了一句谎话——他以为存好了，
   * 磁盘上留着的是旧的那份，而窗口已经没了。
   */
  it('保存失败（写盘 500）：窗口留着，壳收到 cancel', async () => {
    putOk = false
    useDocumentStore.setState({ saveState: 'dirty' })
    expect(await answerCloseRequest('save')).toBe('stay')
    expect(puts).toEqual(['d_close'])
    expect(s().saveState).toBe('save_error')
    expect(said).toEqual(['cancel'])
  })

  /**
   * 冲突未决时 `flushAutosave` **根本不往磁盘上写**（磁盘那份已经不是我以为
   * 的那份）。所以「保存」在这一档是个不可能成功的动作，绝不能因为它「没报错」
   * 就把窗口关掉。
   */
  it('冲突未决时点保存：不写磁盘，也不关窗', async () => {
    useDocumentStore.setState({ saveState: 'conflict' })
    expect(await answerCloseRequest('save')).toBe('stay')
    expect(puts).toEqual([])
    expect(said).toEqual(['cancel'])
  })
})
