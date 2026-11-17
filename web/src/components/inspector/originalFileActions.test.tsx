import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FigureOrigin, PanelObject } from '@/types/document'
import { OriginalFileActions } from './OriginalFileActions'

// Only the visibility rule is under test here; the buttons have their own suites.
vi.mock('./UpdateSourceButton', () => ({
  UpdateSourceButton: () => <span data-testid="writeback" />,
  WriteBackRecordsButton: () => <span data-testid="records" />,
}))

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const origin: FigureOrigin = {
  version: 1,
  snapshot: {
    version: 1, sourceId: 'History.pdf', patchHash: 'P', receiptId: 'A', snapshotId: 'archive-A',
    executionOwner: { script: 'plot.py', run_config: null },
  },
  patches: [],
}
const panel = (extra: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf', x: 0, y: 0, w: 80, h: 60,
  nativeW: 80, nativeH: 60, figureFrame: 1, script: null, overrides: [], ...extra,
})

let root: Root
let host: HTMLDivElement
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

async function render(value: PanelObject): Promise<string> {
  await act(async () => { root.render(<OriginalFileActions panel={value} />) })
  return host.innerHTML
}

describe('original-file actions', () => {
  it('appears for a scriptless panel that has a verified historical source', async () => {
    const html = await render(panel({ figureOrigin: origin }))
    expect(html).toContain('data-testid="writeback"')
    expect(html).toContain('data-testid="records"')
  })

  it('stays hidden when there is neither a script nor a historical source', async () => {
    expect(await render(panel())).toBe('')
  })

  it('keeps the original script-backed entry unchanged', async () => {
    expect(await render(panel({ script: 'plot.py' }))).toContain('data-testid="writeback"')
  })
})
