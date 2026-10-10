/**
 * 缺失素材重链接（2026-10-07 设计审计 §10.2）：按名字自动配上的那几行要**说出口**（「按名字匹配」），
 * 没配上的保持缺失；选中的替代素材画缩略图；路径留尾巴（TailPath）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { RelinkDialog } from '@/components/RelinkDialog'
import { useClipboardStore } from '@/lib/clipboard'
import { useAssetStore } from '@/store/assetStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useAssetStore.setState({
    byId: {
      'figs/Fig1.pdf': {
        id: 'figs/Fig1.pdf',
        name: 'Fig1',
        folder: 'figs',
        kind: 'pdf',
        native_w_mm: 80,
        native_h_mm: 60,
        mtime: 1,
      },
    },
  } as never)
  useClipboardStore.setState({ pending: null })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useClipboardStore.setState({ pending: null })
})

describe('RelinkDialog', () => {
  it('按名字配上的说「按名字匹配」并画替代素材的缩略图；配不上的保持缺失、没有这句', async () => {
    act(() => root.render(<RelinkDialog />))
    expect(document.querySelector('[data-dialog="relink"]')).toBeNull()
    await act(async () =>
      useClipboardStore.getState().setPending({
        mode: 'relink',
        missing: [
          { fileId: 'old/machine/Fig1.pdf', name: 'Fig1', count: 1 },
          { fileId: 'old/machine/Gone.pdf', name: 'Gone', count: 2 },
        ],
      }),
    )
    const row = (id: string) => document.querySelector(`[data-relink-row="${id}"]`)!
    expect(row('old/machine/Fig1.pdf').querySelector('[data-relink-by-name]')).not.toBeNull()
    expect(row('old/machine/Fig1.pdf').querySelector('[data-relink-thumb]')).not.toBeNull()
    expect(row('old/machine/Gone.pdf').querySelector('[data-relink-by-name]')).toBeNull()
    expect(row('old/machine/Gone.pdf').querySelector('[data-relink-thumb]')).toBeNull()
    // 路径留尾巴：从左边裁（rtl 容器里钉住从左到右）
    expect(row('old/machine/Gone.pdf').querySelector('[dir="rtl"]')!.textContent).toContain('old/machine/Gone.pdf')
  })
})
