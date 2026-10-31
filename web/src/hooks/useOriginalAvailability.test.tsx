/**
 * runtime 图的原图口径读 `runtimeAssetStore.byId[id].registered`（`ensure()` 在对话框挂载**之后**才回来）：
 * hook 的 memo 必须订阅它，否则可用性不刷新，单图档仍静默按画布导（#1348 Codex P1）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { PanelObject } from '@/types/document'
import { useOriginalAvailability } from './useOriginalSpec'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ID = 'runtime:fig.py#F'
const panel = {
  id: 'r1',
  type: 'panel',
  fileId: ID,
  fileKind: 'runtime',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
} as unknown as PanelObject

let seen: ReturnType<typeof useOriginalAvailability>
function Host() {
  seen = useOriginalAvailability(ID, { anyFigures: true, panel })
  return null
}

let root: Root
let el: HTMLDivElement
beforeEach(() => {
  useAssetStore.setState({ byId: {} } as never)
  useRuntimeAssetStore.setState({
    assets: [],
    byId: { [ID]: { registered: false, checked: true, status: 'needs_rerun', cached: false, profile: 'safe' } },
  } as never)
  el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
})
afterEach(async () => {
  await act(async () => root.unmount())
  el.remove()
})

describe('useOriginalAvailability 订阅 runtime 注册状态', () => {
  it('挂载后 ensure() 才回 registered：可用性从 source_stale 翻成可用', async () => {
    await act(async () => root.render(<Host />))
    expect(seen.ok).toBe(false)
    expect(seen.reason).toBe('source_stale')
    await act(async () => {
      useRuntimeAssetStore.setState({
        byId: { [ID]: { registered: true, checked: true, status: 'fresh', cached: true, profile: 'safe' } },
      } as never)
    })
    expect(seen.ok).toBe(true)
  })
})
