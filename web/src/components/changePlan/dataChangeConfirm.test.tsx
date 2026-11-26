/**
 * 脚本方案「数据会变，先问一句」（ADR 0206 修订 2026-11-27）：卡片先问、回答前没有「查看 / 应用」、「先不改」后卡片落成一句灰字、
 * 「照样改」之后应用才把确认带给后端；没有 confirm 的脚本方案照旧直接应用（不带任何确认字段）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const applyChangePlanScript = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: vi.fn(),
  applyChangePlanScript: (...a: unknown[]) => applyChangePlanScript(...a),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanTranscript } from './ChangePlanTranscript'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const DIFF = '--- fig.py (修改前)\n+++ fig.py (修改后)\n@@ -1,3 +1,3 @@\n a\n-t = x\n+t = x * 60\n c\n'
const CONFIRM = { kind: 'script_data_changed' as const, items: [{ stem: 'fig', gid: 'l0', reason: 'data_changed' as const }], total: 1 }
const planWith = (confirm?: typeof CONFIRM): ChangePlan => ({
  id: 'p1',
  version: 1,
  kind: 'script',
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp', script_sha256: 'aa' },
  base_patches: BASE,
  candidate_patches: BASE,
  applied: null,
  groups: [{ key: 'script', kind: 'script', label: '横轴改成秒', comment_id: 'a1', atomic: true, changes: [], boxes: { after: [] }, script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: 'bb', diff: DIFF, added: 1, removed: 1 }, ...(confirm ? { confirm } : {}) }],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
})
const entry = (plan: ChangePlan): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key: 'a',
    from: 'assistant',
    instruction: '横轴单位改成秒，数据也换算',
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    planId: 'p1',
    plan,
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    request: { instruction: '横轴单位改成秒，数据也换算', mode: 'script' },
    comments: [],
    commentCount: 0,
  }
}
const put = (e: PlanEntry) => act(() => useChangePlanStore.setState({ entries: { [e.key]: e }, order: [e.key], dialogKey: null }))

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const click = (sel: string) => act(() => q(sel)!.click())

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  applyChangePlanScript.mockReset().mockResolvedValue({ backup: 'fig/1', after_sha256: 'bb' })
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_data_confirm')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

it('数据会变：卡上先问一句 + 两颗按钮，回答前没有「查看 / 应用」，弹窗与应用都拒绝', async () => {
  await put(entry(planWith(CONFIRM)))
  await render(<ChangePlanTranscript panelId="p1" />)
  const card = q('[data-change-plan-card="ready"]')!
  expect(card.textContent).toContain('这次改动会改变图里画的数据，确定要这样改吗？')
  expect(q('[data-change-plan-confirm-data="yes"]')!.textContent).toBe('照样改')
  expect(q('[data-change-plan-confirm-data="no"]')!.textContent).toBe('先不改')
  expect(q('[data-change-plan-view]')).toBeNull()
  expect(q('[data-change-plan-apply]')).toBeNull()
  await act(async () => {
    useChangePlanStore.getState().openDialog('a')
    expect(useChangePlanStore.getState().dialogKey).toBeNull()
    expect(useChangePlanStore.getState().apply('a')).toBe('nothing')
    expect(await useChangePlanStore.getState().applyScript('a')).toBe('failed')
  })
  expect(applyChangePlanScript).not.toHaveBeenCalled()
})

it('「先不改」：这张脚本卡整张不应用，落成一句灰字，脚本没动', async () => {
  await put(entry(planWith(CONFIRM)))
  await render(<ChangePlanTranscript panelId="p1" />)
  click('[data-change-plan-confirm-data="no"]')
  const card = q('[data-change-plan-card="failed"]')!
  expect(card.getAttribute('data-change-plan-code')).toBe('change_plan_script_data_declined')
  expect(card.textContent).toContain('数据没改，这次脚本也没有动。')
  expect(q('[data-change-plan-confirm-data]')).toBeNull()
  expect(q('[data-change-plan-run-script]')).toBeNull()
  expect(applyChangePlanScript).not.toHaveBeenCalled()
})

it('「照样改」：出现「查看 / 应用」，应用时把确认带给后端', async () => {
  await put(entry(planWith(CONFIRM)))
  await render(<ChangePlanTranscript panelId="p1" />)
  click('[data-change-plan-confirm-data="yes"]')
  expect(q('[data-change-plan-confirm-data]')).toBeNull()
  expect(q('[data-change-plan-view]')).not.toBeNull()
  await act(async () => {
    q('[data-change-plan-apply]')!.click()
  })
  expect(applyChangePlanScript).toHaveBeenCalledExactlyOnceWith('p1', { confirmDataChange: true })
})

it('没有 confirm 的脚本方案：不问，直接应用，不带确认字段', async () => {
  await put(entry(planWith()))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-confirm-data]')).toBeNull()
  await act(async () => {
    q('[data-change-plan-apply]')!.click()
  })
  expect(applyChangePlanScript).toHaveBeenCalledExactlyOnceWith('p1')
})
