/**
 * 准备会话创建请求的请求体（`createPreparationSession`）：前端**永远不发 `execution`**（V2 PR4，回退是后端同一会话里的第二个
 * attempt）；`reuse_last_success`（#982）只在目标明确带它且没有 argv 时出现。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPreparationSession } from './api'

const calls: { url: string; init?: RequestInit }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
        headers: new Headers(),
      } as Response)
    }),
  )
})
afterEach(() => vi.unstubAllGlobals())

const bodyOf = (i: number) => JSON.parse(String(calls[i].init?.body ?? 'null'))

describe('createPreparationSession 请求体', () => {
  it('普通目标：只有 script', async () => {
    await createPreparationSession({ script: 'a.py' }, null)
    expect(bodyOf(0)).toEqual({ script: 'a.py' })
  })

  it('明确沿用：带 reuse_last_success，仍没有 execution', async () => {
    await createPreparationSession({ script: 'a.py', reuse_last_success: true }, null)
    expect(bodyOf(0)).toEqual({ script: 'a.py', reuse_last_success: true })
    expect(JSON.stringify(bodyOf(0))).not.toContain('execution')
  })
})
