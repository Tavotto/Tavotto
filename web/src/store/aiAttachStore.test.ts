import { beforeEach, describe, expect, it, vi } from 'vitest'
import { discardAiAttachment, uploadAiAttachment } from '@/lib/api'
import { prepareAttachment } from '@/lib/aiAttachments'
import { useAiAttachStore } from './aiAttachStore'

vi.mock('@/lib/api', () => ({ uploadAiAttachment: vi.fn(), discardAiAttachment: vi.fn() }))
vi.mock('@/lib/aiAttachments', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/aiAttachments')>()),
  prepareAttachment: vi.fn(),
}))

const T = 'pa\u0000Fig1.pdf'
const file = (name: string) => new File(['x'], name, { type: 'text/csv' })
const prepared = (f: File) => ({ ok: true as const, item: { kind: 'file' as const, name: f.name, ext: 'csv', size: f.size, thumb: '', blob: f } })

let n = 0
beforeEach(() => {
  n = 0
  useAiAttachStore.setState({ byTarget: {} })
  vi.mocked(discardAiAttachment).mockReset().mockResolvedValue({ ok: true })
  vi.mocked(prepareAttachment).mockReset().mockImplementation(async (f: File) => prepared(f) as never)
  vi.mocked(uploadAiAttachment)
    .mockReset()
    .mockImplementation(async (_b: Blob, name: string) => ({ id: `id${++n}`, kind: 'file', mime: null, bytes: 1, width: null, height: null, name }) as never)
})

describe('settle', () => {
  it('还在读文件 / 解码（还没上卡）的也等：发送带上整批选中的文件', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    vi.mocked(prepareAttachment).mockImplementation(async (f: File) => {
      await gate
      return prepared(f) as never
    })
    void useAiAttachStore.getState().add(T, 'pa', [file('a.csv'), file('b.csv')])
    expect(useAiAttachStore.getState().byTarget[T] ?? []).toHaveLength(0)
    const out = useAiAttachStore.getState().settle(T)
    release()
    expect((await out).map((x) => x.name)).toEqual(['a.csv', 'b.csv'])
  })
})

describe('release', () => {
  it('只拿掉这次提交带走的，提交途中新加的留着、后端暂存不删', async () => {
    await useAiAttachStore.getState().add(T, 'pa', [file('a.csv')])
    const taken = await useAiAttachStore.getState().settle(T)
    await useAiAttachStore.getState().add(T, 'pa', [file('b.csv')])
    useAiAttachStore.getState().release(T, taken.map((x) => x.key))
    expect(useAiAttachStore.getState().byTarget[T].map((x) => x.name)).toEqual(['b.csv'])
    useAiAttachStore.getState().release(T, useAiAttachStore.getState().byTarget[T].map((x) => x.key))
    expect(T in useAiAttachStore.getState().byTarget).toBe(false)
    expect(discardAiAttachment).not.toHaveBeenCalled()
  })
})
