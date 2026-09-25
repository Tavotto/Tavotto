/**
 * `engineSpecfix` 的成功体校验（ADR 0080，Codex #549 第七轮 P1）：下游消费的每个字段
 * 都要验过形状——缺一个、类型不对，就是「结果不确定」（抛），不是「修好了 / 没修成」。
 * 调用方的 `issueFixActions` 把抛出的一律当不确定处理：文档不改，按此刻的列表重放 worker。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineError, engineSpecfix } from './api'

const GOOD = {
  ok: true,
  exit: 'done',
  patches: [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 8.5 }],
  changes: [],
  skipped: [{ rule: 'font-family-substituted', gid: 'axes_0.xlabel', reason: 'font_unavailable' }],
  unresolved: [],
  blocking: [],
  adjustments: [],
  worker_retired: false,
}

const realFetch = globalThis.fetch
let body: unknown = GOOD

beforeEach(() => {
  body = GOOD
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch
})

afterEach(() => {
  globalThis.fetch = realFetch
})

const call = () => engineSpecfix('Fig1.pdf', [], 1, {}, undefined)

async function badShape(): Promise<void> {
  const err = await call().then(
    () => null,
    (e: unknown) => e,
  )
  expect(err).toBeInstanceOf(EngineError)
  expect((err as EngineError).code).toBe('bad_shape')
}

describe('engineSpecfix 的成功体校验', () => {
  it('形状完整：原样交出', async () => {
    await expect(call()).resolves.toMatchObject({ ok: true, worker_retired: false })
  })

  it('没有 worker_retired（老后端）：照样收', async () => {
    const { worker_retired: _, ...old } = GOOD
    body = old
    await expect(call()).resolves.toMatchObject({ ok: true })
  })

  it('缺 skipped：不确定，抛', async () => {
    const { skipped: _, ...rest } = GOOD
    body = rest
    await badShape()
  })

  it('skipped 里有一项不是 {rule, gid, reason} 字符串：抛', async () => {
    body = { ...GOOD, skipped: [{ rule: 'x', gid: 3, reason: 'no_fit' }] }
    await badShape()
  })

  it('patches 里有一项缺 prop：抛', async () => {
    body = { ...GOOD, patches: [{ gid: 'axes_0.xticks', value: 8.5 }] }
    await badShape()
  })

  it('patches 里有一项不是对象：抛', async () => {
    body = { ...GOOD, patches: [null] }
    await badShape()
  })

  it('exit 不是字符串：抛', async () => {
    body = { ...GOOD, exit: 3 }
    await badShape()
  })

  it('worker_retired 不是布尔：抛', async () => {
    body = { ...GOOD, worker_retired: 'yes' }
    await badShape()
  })
})
