/**
 * 延迟清输入框（方案落定时，`clearIfUnchanged(seq, 目标)`）靠编辑序号判「提交之后输入框没被动过」：
 * 序号跨目标切换必须单调递增、不复用——A 发起 → 切 B → 切回 A，序号不能回到提交时的值（否则会清掉没提交的新稿）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { __resetWriteStampForTests } from '@/lib/aiDraft'
import { setCurrentProjectId } from '@/lib/session'
import { createDraftBackend } from '@/test/aiDraftBackend'
import type { PanelObject } from '@/types/document'
import { useAiDraft } from './useAiDraft'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string) => ({ id, type: 'panel', fileId: `${id}.pdf`, script: `${id}.py` }) as unknown as PanelObject
let api: ReturnType<typeof useAiDraft>
function Probe({ p }: { p: PanelObject }) {
  api = useAiDraft(p)
  return null
}
let root: Root
let host: HTMLDivElement
const show = (p: PanelObject) => act(async () => root.render(<Probe p={p} />))
const settle = async () => {
  for (let i = 0; i < 20; i++) await act(async () => Promise.resolve())
}

beforeEach(() => {
  __resetWriteStampForTests()
  const be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  setCurrentProjectId('pa')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('A 写稿提交 → 切 B → 切回 A 写新稿：序号不会回到提交时的值，延迟清不动新稿', async () => {
  await show(panel('A'))
  await settle()
  await act(async () => api.setPrompt('A 的指令'))
  const submitted = api.draftSeq()
  const target = { project: 'pa', fileId: 'A.pdf' }
  await show(panel('B'))
  await settle()
  await show(panel('A'))
  await settle()
  // 切回 A 之后写新稿（写到与提交序号同样多次）
  for (let i = 0; i < submitted; i++) await act(async () => api.setPrompt(`A 的新稿 ${i}`))
  const typed = api.prompt
  await act(async () => api.clearIfUnchanged(submitted, target))
  expect(api.prompt).toBe(typed)
  expect(api.draftSeq()).toBeGreaterThan(submitted)
})

it('目标不一致时不清；目标一致且没动过才清', async () => {
  await show(panel('A'))
  await settle()
  await act(async () => api.setPrompt('x'))
  const s = api.draftSeq()
  await act(async () => api.clearIfUnchanged(s, { project: 'pa', fileId: 'other.pdf' }))
  expect(api.prompt).toBe('x')
  await act(async () => api.clearIfUnchanged(s, { project: 'pa', fileId: 'A.pdf' }))
  expect(api.prompt).toBe('')
})
