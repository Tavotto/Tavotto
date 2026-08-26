/**
 * 版本缩略图缓存的**身份与回收**（#137 评审 P1）。
 *
 * 缓存键少带一个维度的后果不是「慢一点」，而是**把别的图当成这一版的样子
 * 显示出来**，而且一次请求都不发——正是 issue #131 要修掉的那类「无提示地
 * 冒充版本视觉状态」。`fileId` 是项目内相对路径，两个项目里同名同 overrides
 * 的图完全可能是两张不同的图。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearVariantPngCache, useVariantPng } from './useVariantPng'
import { setCurrentProjectId } from '@/lib/session'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const previewPng = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (id: string, patches: unknown[], bucket: number, signal?: AbortSignal) =>
    previewPng(id, patches, bucket, signal),
}))

const OV = [{ gid: 't1', prop: 'pos_frac', value: [0.1, 0.2] }]

let created = 0
let revoked: string[] = []
let container: HTMLDivElement
let root: Root
/** 最近一次渲染读到的 hook 结果 */
let last: ReturnType<typeof useVariantPng>

function Probe({
  fileId,
  overrides,
  bucket = 200,
  rev = 0,
}: {
  fileId: string
  overrides: unknown[]
  bucket?: number
  rev?: number
}) {
  last = useVariantPng(fileId, overrides, bucket, true, rev)
  return null
}

beforeEach(() => {
  previewPng.mockReset()
  previewPng.mockResolvedValue(new Blob(['png']))
  clearVariantPngCache()
  created = 0
  revoked = []
  setCurrentProjectId('projA')
  globalThis.URL.createObjectURL = vi.fn(() => `blob:url-${++created}`)
  globalThis.URL.revokeObjectURL = vi.fn((u: string) => {
    revoked.push(u)
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

/** 挂一次 Probe 并等取数落定 */
async function fetchOnce(
  fileId: string,
  overrides: unknown[],
  bucket = 200,
  rev = 0,
): Promise<string | null> {
  await act(async () => {
    root.render(<Probe fileId={fileId} overrides={overrides} bucket={bucket} rev={rev} />)
  })
  // 取数是一个 microtask 链（fetch → blob → setState），冲干净再断言；
  // 不是「等 N 毫秒应该就好了」——这里等的是队列排空，不是墙钟
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
  return last.url
}

describe('缓存键：项目 + 素材版本 + 变体，少一个都会串图', () => {
  it('同一项目同一变体命中缓存，只发一次请求', async () => {
    const a = await fetchOnce('fig1.pdf', OV)
    const b = await fetchOnce('fig1.pdf', OV)
    expect(a).toBe(b)
    expect(a).not.toBeNull()
    expect(previewPng).toHaveBeenCalledTimes(1)
  })

  it('换项目之后同名同 overrides 不得命中旧项目的图', async () => {
    const a = await fetchOnce('fig1.pdf', OV)
    setCurrentProjectId('projB')
    const b = await fetchOnce('fig1.pdf', OV)
    expect(b).not.toBe(a)
    expect(previewPng).toHaveBeenCalledTimes(2)
  })

  it('磁盘素材被改过（rev 变了）也不得命中旧缩略图', async () => {
    const a = await fetchOnce('fig1.pdf', OV, 200, 1000)
    const b = await fetchOnce('fig1.pdf', OV, 200, 2000)
    expect(b).not.toBe(a)
    expect(previewPng).toHaveBeenCalledTimes(2)
  })

  it('没有 overrides 的面板压根不发请求（与磁盘图一模一样）', async () => {
    expect(await fetchOnce('fig1.pdf', [])).toBeNull()
    expect(previewPng).not.toHaveBeenCalled()
  })
})

describe('回收：blob 不许泄漏', () => {
  it('超出上限的老条目当场 revoke', async () => {
    for (let i = 0; i < 30; i++) {
      await fetchOnce('fig1.pdf', [{ gid: 't', prop: 'p', value: i }])
    }
    // 上限 24：最早那几个必须已经还回去了
    expect(revoked.length).toBeGreaterThanOrEqual(6)
    expect(revoked).toContain('blob:url-1')
  })

  it('clearVariantPngCache 整表释放', async () => {
    await fetchOnce('fig1.pdf', OV)
    await fetchOnce('fig2.pdf', OV)
    revoked = []
    clearVariantPngCache()
    expect(revoked).toHaveLength(2)
  })
})

describe('失败：退回磁盘图，但必须标成近似', () => {
  it('请求失败 → url 为空、approximate 置位', async () => {
    previewPng.mockRejectedValue(new Error('engine down'))
    await fetchOnce('fig1.pdf', OV)
    expect(last.url).toBeNull()
    expect(last.approximate).toBe(true)
  })
})
