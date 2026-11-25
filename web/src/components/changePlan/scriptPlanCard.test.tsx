/**
 * 脚本修改方案卡与弹窗（ADR 0206 PR 6）：卡上一句话（+N −M）、「直接应用」走脚本应用、已应用有「撤销」（按备份还原，不看文档历史）、
 * 弹窗里「对比图 / 脚本改动」两个页签（脚本改动用现成的 `DiffView`）、越界失败卡列文件名。图片在 jsdom 里不加载，只看结构与锚点。
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
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: vi.fn(),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanDialog } from './ChangePlanDialog'
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
const DIFF = '--- fig.py (修改前)\n+++ fig.py (修改后)\n@@ -1,3 +1,3 @@\n a\n-ax.set_title("old")\n+ax.set_title("new")\n c\n'
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  kind: 'script',
  source: 'assistant',
  base: { target: 'f_p1', fingerprint: 'fp', script_sha256: 'aa' },
  base_patches: BASE,
  candidate_patches: BASE,
  applied: null,
  groups: [{ key: 'script', kind: 'script', label: '改标题', comment_id: 'a1', atomic: true, changes: [], boxes: { after: [] }, script: { rel: 'fig.py', base_sha256: 'aa', new_sha256: 'bb', diff: DIFF, added: 1, removed: 1 } }],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
}
const entry = (key: string, over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
    from: 'assistant',
    instruction: '把标题改成 new',
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
    plan: PLAN,
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    request: { instruction: '把标题改成 new', mode: 'script' },
    comments: [],
    commentCount: 0,
    ...over,
  }
}
const put = (e: PlanEntry, dialogKey: string | null = null) =>
  act(() => useChangePlanStore.setState({ entries: { [e.key]: e }, order: [e.key], dialogKey }))

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const applyScript = vi.fn()
const undoScript = vi.fn()

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  applyScript.mockReset().mockResolvedValue('applied')
  undoScript.mockReset().mockResolvedValue(true)
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null, applyScript: applyScript as never, undoScript: undoScript as never })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_script_cards')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

it('待确认的脚本方案卡：一句话说改了几行，「直接应用」走脚本应用', async () => {
  await put(entry('a'))
  await render(<ChangePlanTranscript panelId="p1" />)
  const card = q('[data-change-plan-card="ready"]')!
  expect(card.textContent).toContain('+1 −1')
  expect(q('[data-change-plan-run-script]')).toBeNull()
  await act(async () => q('[data-change-plan-apply]')!.click())
  expect(applyScript).toHaveBeenCalledWith('a')
})

it('已应用：有「撤销」（按备份还原，不看文档历史）；点了走 undoScript', async () => {
  await put(entry('a', { status: 'applied', plan: { ...PLAN, applied: { backup: 'fig/1', after_sha256: 'bb' } } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-card="applied"]')!.textContent).toContain('脚本已修改')
  await act(async () => q('[data-change-plan-undo]')!.click())
  expect(undoScript).toHaveBeenCalledWith('a')
})

it('一句灰字：脚本没写进去 / 脚本在应用之后又改过', async () => {
  await put(entry('a', { notice: 'apply_failed' }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-notice]')!.textContent).toContain('脚本没有改动')
  await put(entry('a', { status: 'applied', notice: 'undo_failed', plan: { ...PLAN, applied: { backup: 'x', after_sha256: 'bb' } } }))
  expect(q('[data-change-plan-notice]')!.textContent).toContain('没有替你撤销')
})

it('弹窗：对比图 / 脚本改动两个页签；脚本改动里是 diff；没有逐组勾选；主按钮「应用」走脚本应用', async () => {
  await put(entry('a'), 'a')
  await render(<ChangePlanDialog />)
  expect(q('[data-change-plan-dialog]')).toBeTruthy()
  expect(q('[data-change-plan-script-meta]')!.textContent).toContain('fig.py')
  expect(q('[data-change-plan-list]')).toBeNull()
  expect(q('[data-change-plan-script-diff]')).toBeNull()
  const tab = [...document.body.querySelectorAll<HTMLElement>('[role="radio"], [role="tab"], button')].find((b) => b.textContent === '脚本改动')!
  await act(async () => tab.click())
  expect(q('[data-change-plan-script-diff] [data-ai-diff]')).toBeTruthy()
  expect(q('[data-change-plan-script-diff]')!.textContent).toContain('ax.set_title("new")')
  await act(async () => q('[data-change-plan-confirm]')!.click())
  expect(applyScript).toHaveBeenCalledWith('a')
})

it('越界的失败卡：一句话 + 文件名（其余「等 N 个」）+「重试」', async () => {
  await put(
    entry('a', {
      status: 'failed',
      plan: undefined,
      failure: { code: 'change_plan_script_overreach', params: { files: ['a.txt', 'b.txt', 'c.txt', 'd.txt', 'e.txt'], total: 8, target_changed: false } },
    }),
  )
  await render(<ChangePlanTranscript panelId="p1" />)
  const card = q('[data-change-plan-card="failed"]')!
  expect(card.textContent).toContain('你的脚本没有被改动')
  expect(q('[data-change-plan-overreach-files]')!.textContent).toContain('等 8 个')
  expect(q('[data-change-plan-failed-action="retry"]')).toBeTruthy()
})

it('部分能改之后的脚本方案卡：同一条指令只画一次用户气泡；前一张收成一行灰字时由脚本卡画', async () => {
  const first = entry('a', { plan: { ...PLAN, kind: undefined, groups: [{ ...PLAN.groups[0], kind: undefined, script: undefined }] } })
  const second = entry('b', { followsKey: 'a' })
  act(() => useChangePlanStore.setState({ entries: { a: first, b: second }, order: ['a', 'b'], dialogKey: null }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(document.body.querySelectorAll('[data-change-plan-user]')).toHaveLength(1)
  expect(document.body.querySelectorAll('[data-change-plan-card]')).toHaveLength(2)
  act(() => useChangePlanStore.setState({ entries: { a: { ...first, status: 'handoff' }, b: second }, order: ['a', 'b'] }))
  expect(document.body.querySelectorAll('[data-change-plan-user]')).toHaveLength(1)
  expect(q('[data-change-plan-handoff]')).toBeTruthy()
})

it('脚本被改过的冲突卡：一句话说清没自动还原，主按钮「恢复到开始前的版本」+ 次级「保留现在的版本」，没有「改脚本来做」', async () => {
  const restore = vi.fn()
  act(() => useChangePlanStore.setState({ restoreScript: restore as never }))
  await put(entry('a', { status: 'failed', plan: undefined, failure: { code: 'change_plan_script_overreach', params: { target_changed: true, backup: 'fig/1', script: 'fig.py', current_sha256: 'cc', files: [], total: 0 } } }))
  await render(<ChangePlanTranscript panelId="p1" />)
  expect(q('[data-change-plan-card="failed"]')!.textContent).toContain('没有自动还原')
  expect(q('[data-change-plan-run-script]')).toBeNull()
  expect(q('[data-change-plan-dismiss]')!.textContent).toBe('保留现在的版本')
  await act(async () => q('[data-change-plan-failed-action="restore"]')!.click())
  expect(restore).toHaveBeenCalledWith('a')
  expect(q('[data-change-plan-failed-action="restore"]')!.textContent).toBe('恢复到开始前的版本')
})
