/**
 * MCP 画布的状态表达（2026-10-07 设计审计 §10.4）：
 *   - 预检结论是一枚 StatusPill（语气跟着结论走），整枚可点 = 重跑预检；
 *   - 问题列表：阻断与警告不再共用一个图标（问题面板那张严重度表），颜色是锚点；
 *     行是 listRowClass（hover 底 + 焦点环），只有 gid 还在当前 manifest 里的行可点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { seedExactRender } from '@/test/renderFixtures'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { IssueList, PreflightPill } from './McpApp'
import type { PreflightIssuePayload } from './session'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  useRenderStore.setState(useRenderStore.getInitialState(), true)
  useUiStore.getState().setSelectedGids([])
})

const render = (node: React.ReactNode) => act(() => root.render(node))

describe('PreflightPill', () => {
  const pill = () => container.querySelector<HTMLElement>('[data-status-pill]')!

  it.each([
    [{ error: 1, warn: 2 }, false, 'danger'],
    [{ warn: 1 }, false, 'warn'],
    [{ not_verifiable: 1 }, false, 'neutral'],
    [{}, false, 'ok'],
    [{ error: 3 }, true, 'neutral'],
  ] as const)('counts %o stale=%s → %s', (counts, stale, tone) => {
    render(<PreflightPill counts={counts} stale={stale} loading={false} onClick={() => {}} />)
    expect(pill().dataset.statusPill).toBe(tone)
  })

  it('整枚是一颗按钮，点它 = 重跑预检；文字仍是那句计数（e2e 按名字找它）', () => {
    const onClick = vi.fn()
    render(<PreflightPill counts={{ error: 0, warn: 1 }} stale={false} loading={false} onClick={onClick} />)
    const btn = container.querySelector<HTMLButtonElement>('button[data-preflight-pill]')!
    expect(btn.contains(pill())).toBe(true)
    expect(btn.textContent).toMatch(/0 阻断 · 1 警告/)
    act(() => btn.click())
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})

describe('IssueList', () => {
  const panel = {
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    fileId: 'fig.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    script: 'fig.py',
    overrides: [],
  } as unknown as PanelObject
  const manifest = {
    elements: [{ gid: 'g-title', role: 'title', bbox: [0, 0, 1, 1] }],
  } as unknown as Manifest
  const issue = (id: string, severity: PreflightIssuePayload['severity'], gids: string[]): PreflightIssuePayload => ({
    id,
    severity,
    text: `${id} text`,
    object_ids: [],
    gids,
  })

  it('阻断八角、警告三角（两级不再共用一个图标），颜色是锚点；只有 gid 还在的行可点并选中它', () => {
    seedExactRender(panel, manifest)
    render(
      <IssueList
        issues={[issue('e1', 'error', ['g-title']), issue('w1', 'warn', ['g-gone'])]}
        stale={false}
        panel={panel}
      />,
    )
    const rows = [...container.querySelectorAll<HTMLButtonElement>('button[data-mcp-issue]')]
    expect(rows.map((r) => r.dataset.mcpIssue)).toEqual(['error', 'warn'])
    const glyph = (r: HTMLElement) => r.querySelector('svg')!.innerHTML
    expect(glyph(rows[0])).not.toBe(glyph(rows[1]))
    expect(rows[0].querySelector('svg')!.getAttribute('class')).toContain('text-danger')
    expect(rows[1].querySelector('svg')!.getAttribute('class')).toContain('text-warn')
    // 行是 listRowClass：hover 底 + 焦点环
    expect(rows[0].className).toContain('hover:bg-surface-hover')
    expect(rows[0].className).toContain('focus-visible:focus-ring')
    // 等级也用文字说一遍（读屏）
    expect(rows[0].textContent).toContain('阻断')
    expect(rows[1].disabled).toBe(true)
    act(() => rows[0].click())
    expect(useUiStore.getState().selectedGids).toEqual(['g-title'])
  })
})
