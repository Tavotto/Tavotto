/**
 * 「找不到这个脚本的文件」错误卡的主按钮位（ADR 0211 第一期 6）：恰好一份字节相同 → 一颗「接到 …」；
 * 没有指纹 / 对不上 / 多份 → 「选脚本接回」名单（字节相同的 + 就绪度候选），另有「其他脚本」可手动指认；
 * 不是这种情况（问不到 / 脚本其实在）→ 不出现。主语认 `data-script-relink*`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/scriptRelink', async (orig) => ({
  ...(await orig<typeof import('@/store/scriptRelink')>()),
  relinkScript: vi.fn(async () => 'linked'),
}))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchRelinkCandidates: vi.fn(),
}))

import { fetchRelinkCandidates, type RelinkCandidates } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { relinkScript } from '@/store/scriptRelink'
import type { PanelObject } from '@/types/document'
import { ScriptRelinkAction } from './ScriptRelinkAction'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
const panel = { id: 'p1', type: 'panel', fileId: 'Fig1.pdf', script: 'plot.py' } as PanelObject

async function render(found: RelinkCandidates | null, candidates: string[] = []) {
  vi.mocked(fetchRelinkCandidates).mockResolvedValue(found)
  useAssetStore.setState({
    byId: { 'Fig1.pdf': { id: 'Fig1.pdf', capability: { status: 'source_missing', script: 'plot.py', candidates } } } as never,
  })
  await act(async () => root.render(<ScriptRelinkAction panel={panel} script="plot.py" />))
  await act(async () => {
    await Promise.resolve()
  })
}

const found = (over: Partial<RelinkCandidates>): RelinkCandidates => ({
  script: 'plot.py', missing: true, fingerprint: true, matches: [], scripts: ['scripts/plot.py', 'b.py', 'c.py'], ...over,
})

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('ScriptRelinkAction', () => {
  it('恰好一份字节相同：一颗「接回 scripts/」（悬停是完整路径），点了就接', async () => {
    await render(found({ matches: ['scripts/plot.py'] }))
    const btn = host.querySelector<HTMLButtonElement>('[data-script-relink]')!
    expect(btn.textContent).toBe('接回 scripts/')
    expect(btn.className).not.toMatch(/truncate/)
    expect(btn.title).toBe('scripts/plot.py')
    await act(async () => btn.click())
    expect(relinkScript).toHaveBeenCalledWith(panel, 'plot.py', 'scripts/plot.py')
  })

  it('老登记没有指纹：不给一键，给名单（候选 + 其他脚本手动指认），不按文件名猜', async () => {
    await render(found({ fingerprint: false }), ['scripts/plot.py'])
    expect(host.querySelector('[data-script-relink]')).toBeNull()
    const trigger = host.querySelector<HTMLButtonElement>('[data-script-relink-choose]')!
    expect(trigger.textContent).toBe('选脚本接回')
    await act(async () => {
      trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    })
    const options = [...document.querySelectorAll<HTMLElement>('[data-script-relink-option]')].map((e) => e.dataset.scriptRelinkOption)
    expect(options).toEqual(['scripts/plot.py'])
    expect(document.querySelector('[data-script-relink-others]')).not.toBeNull()
  })

  it('不止一份字节相同：都列出来让用户选', async () => {
    await render(found({ matches: ['scripts/plot.py', 'copy.py'], scripts: ['scripts/plot.py', 'copy.py'] }))
    expect(host.querySelector('[data-script-relink]')).toBeNull()
    expect(host.querySelector('[data-script-relink-choose]')).not.toBeNull()
  })

  it('问不到 / 脚本其实在：不出现（卡上照旧只有「重试渲染」）', async () => {
    await render(null)
    expect(host.innerHTML).toBe('')
    await render(found({ missing: false }))
    expect(host.innerHTML).toBe('')
  })

  it('改了名：「接回 new_name.py」；挪回了项目根：「接回 项目根目录」', async () => {
    await render(found({ matches: ['new_name.py'] }))
    expect(host.querySelector('[data-script-relink]')!.textContent).toBe('接回 new_name.py')
    await act(async () => root.render(<ScriptRelinkAction panel={panel} script="scripts/plot.py" />))
    vi.mocked(fetchRelinkCandidates).mockResolvedValue(found({ script: 'scripts/plot.py', matches: ['plot.py'] }))
    await act(async () => root.render(<ScriptRelinkAction panel={panel} script="scripts/plot.py" key="b" />))
    await act(async () => {
      await Promise.resolve()
    })
    expect(host.querySelector('[data-script-relink]')!.textContent).toBe('接回 项目根目录')
  })
})
