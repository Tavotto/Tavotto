import { describe, expect, it } from 'vitest'
import {
  canPassThrough,
  fitWithin,
  extOf,
  pickFiles,
  prepareAttachment,
  ATTACH_MAX,
  ATTACH_MAX_FILE_BYTES,
  ATTACH_MAX_IMAGE_BYTES,
  ATTACH_MAX_TOTAL_BYTES,
  ATTACH_MAX_PIXELS,
  ATTACH_MAX_SIDE,
  ATTACH_TARGET_BYTES,
  sniffImageMime,
} from './aiAttachments'

const bytes = (...xs: number[]) => Uint8Array.from(xs)

describe('参考图的格式与尺寸兜底', () => {
  it('按魔数认四种格式，不信扩展名', () => {
    expect(sniffImageMime(bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a))).toBe('image/png')
    expect(sniffImageMime(bytes(0xff, 0xd8, 0xff, 0xe0))).toBe('image/jpeg')
    expect(sniffImageMime(new TextEncoder().encode('GIF89a'))).toBe('image/gif')
    expect(sniffImageMime(new TextEncoder().encode('RIFF\0\0\0\0WEBPVP8 '))).toBe('image/webp')
    expect(sniffImageMime(new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"/>'))).toBeNull()
    expect(sniffImageMime(new TextEncoder().encode('ftypheic'))).toBeNull()
    expect(sniffImageMime(bytes())).toBeNull()
  })

  it('上限与后端同一个数：6 个、图 4 MiB、文件 25 MiB、合计 50 MiB；前端缩得更狠（目标 < 上限）', () => {
    expect(ATTACH_MAX).toBe(6)
    expect(ATTACH_MAX_IMAGE_BYTES).toBe(4 * 1024 * 1024)
    expect(ATTACH_MAX_FILE_BYTES).toBe(25 * 1024 * 1024)
    expect(ATTACH_MAX_TOTAL_BYTES).toBe(50 * 1024 * 1024)
    expect(ATTACH_TARGET_BYTES).toBeLessThan(ATTACH_MAX_IMAGE_BYTES)
  })

  it('等比缩到最长边以内，只缩不放大', () => {
    expect(fitWithin(8000, 4000)).toEqual({ w: ATTACH_MAX_SIDE, h: 1024 })
    expect(fitWithin(100, 50)).toEqual({ w: 100, h: 50 })
    expect(fitWithin(1, 100000)).toEqual({ w: 1, h: ATTACH_MAX_SIDE })
  })

  it('什么能原样交给后端：认得的格式 + 字节没超 + 像素不多', () => {
    expect(canPassThrough('image/png', 1000, 800, 600)).toBe(true)
    expect(canPassThrough('image/png', ATTACH_TARGET_BYTES + 1, 800, 600)).toBe(false)
    expect(canPassThrough('image/png', 1000, 5000, 5000)).toBe(false)
    expect(canPassThrough('image/png', 1000, 4097, 10)).toBe(false)
    expect(canPassThrough(null, 1000, 10, 10)).toBe(false)
    expect(canPassThrough('image/jpeg', 1000, null, null)).toBe(true) // 量不出像素（解码器缺席）：字节没超就放行，后端还有兜底
    expect(ATTACH_MAX_PIXELS).toBeLessThan(40_000_000)
  })

  it('挑文件：任何文件都收，最多取 room 个，数出多的', () => {
    const f = (name: string, type: string) => new File(['x'], name, { type })
    const r = pickFiles([f('a.png', 'image/png'), f('n.csv', 'text/csv'), f('b.heic', ''), f('c.jpg', 'image/jpeg')], 3)
    expect(r.take.map((x) => x.name)).toEqual(['a.png', 'n.csv', 'b.heic'])
    expect(r.overflow).toBe(1)
    expect(pickFiles([f('a.png', 'image/png')], 0).take).toEqual([])
  })

  it('扩展名角标：小写、不带点、没有就空；超长的不当扩展名', () => {
    expect(extOf('Data.CSV')).toBe('csv')
    expect(extOf('a.tar.gz')).toBe('gz')
    expect(extOf('README')).toBe('')
    expect(extOf('x.verylongext')).toBe('')
  })

  it('超限的图被缩小重编码：解码 → 缩到最长边 2048 → 字节在目标内', async () => {
    const drawn: { w: number; h: number }[] = []
    const realCreate = document.createElement.bind(document)
    const sizes: number[] = []
    // 假画布：toBlob 按宽度返回递减的字节数（模拟 JPEG 越缩越小），只为验证流程与出口约束
    document.createElement = ((tag: string) => {
      if (tag !== 'canvas') return realCreate(tag)
      const c = { width: 0, height: 0 } as { width: number; height: number; getContext: () => unknown; toBlob: (cb: (b: Blob | null) => void, type: string) => void; toDataURL: () => string }
      c.getContext = () => ({ fillStyle: '', fillRect() {}, drawImage: () => drawn.push({ w: c.width, h: c.height }) })
      c.toBlob = (cb, type) => {
        const n = c.width * c.height < 3_000_000 ? 1000 : ATTACH_TARGET_BYTES + 1
        sizes.push(n)
        cb(new Blob([new Uint8Array(n)], { type }))
      }
      c.toDataURL = () => 'data:image/jpeg;base64,AAAA'
      return c as unknown as HTMLElement
    }) as typeof document.createElement
    const oldBmp = globalThis.createImageBitmap
    globalThis.createImageBitmap = (async () => ({ width: 6000, height: 4000, close() {} })) as unknown as typeof createImageBitmap
    try {
      const big = new File([new Uint8Array(5 * 1024 * 1024)], 'huge.png', { type: 'image/png' })
      // 头 16 字节要是 PNG 魔数
      const head = new Uint8Array(5 * 1024 * 1024)
      head.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
      const res = await prepareAttachment(new File([head], big.name, { type: 'image/png' }))
      expect(res.ok).toBe(true)
      if (!res.ok) return
      expect(res.item.blob.size).toBeLessThanOrEqual(ATTACH_TARGET_BYTES)
      expect(Math.max(...drawn.slice(0, 1).map((d) => Math.max(d.w, d.h)))).toBeLessThanOrEqual(ATTACH_MAX_SIDE)
      expect(res.item.thumb.startsWith('data:image/jpeg')).toBe(true)
    } finally {
      document.createElement = realCreate as typeof document.createElement
      globalThis.createImageBitmap = oldBmp
    }
  })

  it('浏览器解不了的图片（如 HEIC）：当普通文件附上，不抛不拒', async () => {
    const res = await prepareAttachment(new File([new Uint8Array(64)], 'x.heic', { type: 'image/heic' }))
    expect(res.ok && res.item.kind).toBe('file')
  })

  it('CSV / XLSX 这类文件原样附上：kind=file、没有缩略图、带扩展名', async () => {
    const res = await prepareAttachment(new File(['a,b\n1,2\n'], '数据.csv', { type: 'text/csv' }))
    expect(res.ok && res.item).toMatchObject({ kind: 'file', ext: 'csv', thumb: '', name: '数据.csv' })
  })

  it('单个文件超过 25 MiB：回 tooLarge（不抛）', async () => {
    const big = new File([new Uint8Array(ATTACH_MAX_FILE_BYTES + 1)], 'huge.bin')
    expect(await prepareAttachment(big)).toEqual({ ok: false, reason: 'tooLarge' })
  })

  it('认得的小图原样通过（不重编码，动图 GIF 也保留）', async () => {
    const gif = new File([new TextEncoder().encode('GIF89a\x01\x00\x01\x00'), new Uint8Array(32)], 'a.gif', { type: 'image/gif' })
    const res = await prepareAttachment(gif)
    expect(res.ok && res.item.kind).toBe('image')
    expect(res.ok && res.item.blob.size).toBe(gif.size)
  })
})
