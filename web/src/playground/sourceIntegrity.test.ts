/**
 * 「源文件未被修改」必须是**两个真哈希**比出来的，不是两个变量比自己。
 *
 * 这个文件盖主线程那一半：Web Crypto 算的 sha256 与判定规则。另一半
 * （Worker 里 Python 从虚拟 FS 读回来再算）在 `tests/test_browser_session.py`
 * 里用真 CPython 跑，两侧的对拍在 `web/e2e/playground.spec.ts`。
 *
 * 下面的向量是 `hashlib.sha256(t.encode()).hexdigest()` 的输出——**跨实现的
 * 已知答案**，正是这条链路要成立的东西：主线程与 Python 对同一段文本必须
 * 得出同一个数，否则「比对」比的是两套编码口径而不是内容。
 */
import { describe, expect, it } from 'vitest'
import { canHashLocally, compareHashes, sha256Hex, shortHash } from './sourceIntegrity'

const SHA = {
  '': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  abc: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  // 非 ASCII：证明两侧算的是同一串 UTF-8 字节，不是各自的字符串表示
  'π': '2617fcb92baa83a96341de050f07a3186657090881eae6b833f66a035600f35a',
} as const

describe('sha256Hex', () => {
  it('对着 Python hashlib 的已知答案算得出来', async () => {
    expect(canHashLocally()).toBe(true)
    for (const [text, want] of Object.entries(SHA)) {
      expect(await sha256Hex(text)).toBe(want)
    }
  })

  it('一个字节之差就换一个数', async () => {
    const a = await sha256Hex('import matplotlib.pyplot as plt\n')
    const b = await sha256Hex('import matplotlib.pyplot as plu\n')
    expect(a).toHaveLength(64)
    expect(a).not.toBe(b)
  })
})

describe('compareHashes', () => {
  it('两边相等 = unchanged，并记下核对完成的时刻', () => {
    const r = compareHashes(SHA.abc, SHA.abc, 1234)
    expect(r.verdict).toBe('unchanged')
    expect(r.verifiedAt).toBe(1234)
  })

  it('不相等 = changed（不变式失效，绝不含糊过去）', () => {
    expect(compareHashes(SHA.abc, SHA[''], 1).verdict).toBe('changed')
  })

  it('主线程算不出来（非安全上下文没有 crypto.subtle）= 查不了，不是「没改」', () => {
    const r = compareHashes('', SHA.abc, 1)
    expect(r.verdict).toBe('unavailable')
    expect(r.reason).toBe('no_subtle_crypto')
    expect(r.verifiedAt).toBeUndefined()
  })

  it('Worker 那边没给出哈希 = 查不了，同样不许说「未改动」', () => {
    const r = compareHashes(SHA.abc, '', 1)
    expect(r.verdict).toBe('unavailable')
    expect(r.reason).toBe('worker_error')
  })
})

describe('shortHash', () => {
  it('长哈希省略成可读的一段，短串原样', () => {
    expect(shortHash(SHA.abc)).toBe('ba7816b…15ad')
    expect(shortHash('')).toBe('')
    expect(shortHash('abcd')).toBe('abcd')
  })
})
