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
import { useDocumentStore } from '@/store/documentStore'

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

  it('缺的那一项还留在素材清单里（导出时才发现）：不拿它配它自己，配到别处同名的那张', async () => {
    useAssetStore.setState({
      byId: {
        'old/case6.png': { id: 'old/case6.png', name: 'case6', folder: 'old', kind: 'png', native_w_mm: 80, native_h_mm: 60, mtime: 1 },
        'new/case6.png': { id: 'new/case6.png', name: 'case6', folder: 'new', kind: 'png', native_w_mm: 80, native_h_mm: 60, mtime: 1 },
      },
    } as never)
    act(() => root.render(<RelinkDialog />))
    await act(async () =>
      useClipboardStore.getState().setPending({ mode: 'relink', missing: [{ fileId: 'old/case6.png', name: 'case6', count: 1 }] }),
    )
    const row = document.querySelector('[data-relink-row="old/case6.png"]')!
    expect(row.querySelector('[data-relink-by-name]')).not.toBeNull()
    expect(row.textContent).toContain('case6（new）')
    expect(row.textContent).not.toContain('（old）')
  })

  describe.each([
    [true, '同一张图换了地址（导出时重新找）：图内修改原样保留', [{ op: 'text', id: 't1' }]],
    [false, '项目包导入的素材替换：换成新素材自己的基线（原行为）', []],
  ])('relocate=%s', (relocate, _title, expected) => {
    it(String(_title), async () => {
      const edits = [{ op: 'text', id: 't1' }]
      useAssetStore.setState({
        byId: { 'new/case6.png': { id: 'new/case6.png', name: 'case6', folder: 'new', kind: 'png', native_w_mm: 80, native_h_mm: 60, mtime: 1 } },
      } as never)
      const doc = useDocumentStore.getState().doc
      useDocumentStore.setState({
        doc: { ...doc, objects: [{ id: 'p0', type: 'panel', fileId: 'old/case6.png', name: 'case6', overrides: edits }] as never },
      })
      act(() => root.render(<RelinkDialog />))
      await act(async () =>
        useClipboardStore.getState().setPending({
          mode: 'relink',
          missing: [{ fileId: 'old/case6.png', name: 'case6', count: 1 }],
          ...(relocate ? { relocate: true } : {}),
        }),
      )
      await act(async () => document.querySelector<HTMLButtonElement>('[data-relink-confirm]')!.click())
      const panel = useDocumentStore.getState().doc.objects[0] as unknown as { fileId: string; overrides: unknown[] }
      expect(panel.fileId).toBe('new/case6.png')
      expect(panel.overrides).toEqual(expected)
    })
  })
})
