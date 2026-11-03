import { afterEach, describe, expect, it, vi } from 'vitest'
import { postEnginePrewarm } from './api'
import { pickPrewarmTarget } from './prewarm'
import type { CanvasObject } from '@/types/document'

const panel = (fileId: string, script?: string) => ({ type: 'panel', id: fileId, fileId, script }) as unknown as CanvasObject
const text = { type: 'text', id: 't' } as unknown as CanvasObject

afterEach(() => vi.unstubAllGlobals())

describe('pickPrewarmTarget', () => {
  it('picks only the first panel that has a script', () => {
    expect(pickPrewarmTarget([text, panel('a.pdf'), panel('b.pdf', 'b.py'), panel('c.pdf', 'c.py')], {})).toBe('b.pdf')
  })
  it('picks nothing when no panel is script-backed', () => {
    expect(pickPrewarmTarget([text, panel('a.pdf')], {})).toBeNull()
  })
  it('does not interfere when the first scripted figure is already rendering', () => {
    expect(pickPrewarmTarget([panel('b.pdf', 'b.py'), panel('c.pdf', 'c.py')], { 'b.pdf': {} })).toBeNull()
  })
})

describe('postEnginePrewarm', () => {
  it('swallows every failure: prewarm is a side channel', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    await expect(postEnginePrewarm('b.pdf')).resolves.toBeUndefined()
  })
  it('posts only the id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    await postEnginePrewarm('b.pdf')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ id: 'b.pdf' })
  })
})
