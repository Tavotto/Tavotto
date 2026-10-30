import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AiSession } from '@/store/aiStore'
import { useAiStore } from '@/store/aiStore'
import { fetchAiSession, resumeAiEditDependencies, type ConvertDependencyCard } from '@/lib/api'
import { EditDependencyNotice } from './EditDependencyNotice'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  resumeAiEditDependencies: vi.fn(),
  fetchAiSession: vi.fn(),
  discardAiEditDependencies: vi.fn(),
}))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const card: ConvertDependencyCard = {
  kind: 'ask_once', packages: ['openpyxl'], outside_list: [], env: { kind: 'builtin', name: '' },
  action: 'install_managed', impact_digest: 'shown-digest', target: 'tavotto_managed',
  creates_environment: true, downloads_python: false, modifies_user_environment: false,
}
const session: AiSession = {
  id: 'old', project: 'pj', agent: 'builtin', agentLabel: 'AI', prompt: 'edit', script: 'fig.py',
  panelId: null, fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '', entries: [],
  status: 'done', changed: false, diff: '', startedAt: 1, finalized: true,
  task: { task_type: 'figure_edit', outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: card }] },
}
let node: HTMLDivElement
let root: Root
beforeEach(() => {
  node = document.createElement('div'); document.body.append(node); root = createRoot(node)
  useAiStore.setState({ sessions: [session] })
  vi.mocked(resumeAiEditDependencies).mockReset()
  vi.mocked(fetchAiSession).mockReset()
})
afterEach(async () => { await act(async () => root.unmount()); node.remove() })

it('shows the same authorization action and resumes with the displayed impact', async () => {
  vi.mocked(resumeAiEditDependencies).mockResolvedValue({ session: 'resumed' })
  vi.mocked(fetchAiSession).mockResolvedValue({ final: true, status: 'done', changed: true, diff: 'diff', transcript: [], task: { task_type: 'figure_edit', outcome: 'partial' } })
  await act(async () => root.render(<EditDependencyNotice session={session} card={card} />))
  expect(node.textContent).toContain('openpyxl')
  const button = node.querySelector<HTMLButtonElement>('[data-convert-deps-install]')!
  expect(button).toBeTruthy()
  await act(async () => { button.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
  expect(resumeAiEditDependencies).toHaveBeenCalledWith('old', 'pj', 'install', 'shown-digest')
  expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 'resumed', changed: true, diff: 'diff', finalized: true })
})

it('late cancellation offers continue without a second install authorization', async () => {
  const late = { packages: ['openpyxl'], notice: 'dependency_installed_late' } as ConvertDependencyCard
  vi.mocked(resumeAiEditDependencies).mockResolvedValue({ session: 'resumed' })
  vi.mocked(fetchAiSession).mockResolvedValue({ final: true, status: 'done', changed: true, diff: 'diff', transcript: [], task: { task_type: 'figure_edit', outcome: 'partial' } })
  await act(async () => root.render(<EditDependencyNotice session={session} card={late} />))
  expect(node.textContent).toMatch(/依赖已经装好|dependencies are already installed/i)
  const button = [...node.querySelectorAll('button')].find((el) => /继续|Continue/.test(el.textContent ?? ''))!
  await act(async () => { button.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
  expect(resumeAiEditDependencies).toHaveBeenCalledWith('old', 'pj', 'replan', undefined)
})

it('a failed request leaves the authorization card available for retry', async () => {
  vi.mocked(resumeAiEditDependencies).mockRejectedValue(new Error('offline'))
  await act(async () => root.render(<EditDependencyNotice session={session} card={card} />))
  await act(async () => { node.querySelector<HTMLButtonElement>('[data-convert-deps-install]')!.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
  expect(node.querySelector('[data-convert-deps-install]')).toBeTruthy()
  expect(useAiStore.getState().sessions[0].id).toBe('old')
})
