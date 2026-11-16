/**
 * 智能参考线（ADR 0185 M7）：图内数据点真的进了拖动的候选线（`candidatesFor` 读权威 manifest，这里代答一份）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/store/renderStore', async (orig) => ({
  ...(await orig<typeof import('@/store/renderStore')>()),
  exactPanelManifest: () => ({
    elements: [
      { gid: 'l', role: 'line', label: '', bbox: [0, 0, 1, 1], editable: [], draggable: false,
        geometry: { kind: 'polyline', paths: [{ points: [[0.1, 0.2], [0.33, 0.47]], closed: false }], fill: false, stroke: true } },
    ],
  }),
}))

const { candidatesFor } = await import('@/canvas/interactions')

beforeEach(async () => {
  useUiStore.setState({ snapEnabled: true, snapToObjects: true, snapToGrid: false, snapToGuides: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_sgd_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push({ id: 'fig', type: 'panel', fileId: 'f.svg', x: 0, y: 0, w: 100, h: 50, nativeW: 100, nativeH: 50, overrides: [] } as unknown as PanelObject)
  })
})

describe('图内数据点进候选线', () => {
  it('曲线顶点的 x、y 在候选里；拖的是这张图自己时不算', () => {
    const c = candidatesFor(new Set())!
    expect(c.xs.some((x) => Math.abs(x - 33) < 1e-6)).toBe(true)
    expect(c.ys.some((y) => Math.abs(y - 23.5) < 1e-6)).toBe(true)
    const own = candidatesFor(new Set(['fig']))!
    expect(own.xs.some((x) => Math.abs(x - 33) < 1e-6)).toBe(false)
  })
})
