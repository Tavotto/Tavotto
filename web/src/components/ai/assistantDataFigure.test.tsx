/**
 * 助手面板里的「从数据出图」入口（TAF Phase 5c，开关后面）。
 *
 * - `caps.taf_enabled` 为假（默认 / 老后端）：入口整个不存在，面板顶栏只剩历史钮；
 * - 为真：顶栏出现「从数据出图」，点开在输入区上方展开小表单（不是弹窗）：打开时拉一次 CSV 列表，
 *   空列表一句话；选文件 + 写指令后「生成」才可用；提交调用 store 的 `startDataFigure` 并收起表单。
 *
 * 下拉是 Radix Select（jsdom 里点不开），这里换成原生 select 替身；选择器一律认 `data-*`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiDataFiles } from '@/lib/api'
import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { setCurrentProjectId } from '@/lib/session'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFiles: vi.fn(),
  fetchAiHistory: vi.fn(async () => ({ sessions: [], total: 0 })),
}))
vi.mock('@/components/ui/Select', () => ({
  Select: ({ value, onChange, options, ariaLabel }: {
    value: string
    onChange: (v: string) => void
    options: { value: string; label: string }[]
    ariaLabel?: string
  }) => (
    <select aria-label={ariaLabel} data-mock-select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">-</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}))

const draftBackend = createDraftBackend()
globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
  draftBackend.handle(String(url), init) ?? new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string, v?: Record<string, unknown>) => t(k, { ns: 'ai', ...(v ?? {}) })
const files = vi.mocked(aiDataFiles)
const startDataFigure = vi.fn(async () => {})

let root: Root
let host: HTMLDivElement

async function mount(taf: boolean | undefined) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_data_figure')
  useSelectionStore.setState({ ids: [] } as never)
  useUiStore.setState({ elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({
    sessions: [],
    caps: capsOf([agentCaps()], taf === undefined ? {} : { taf_enabled: taf }),
    agent: 'codex',
    startDataFigure,
  } as never)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
  for (let i = 0; i < 10; i++) await act(async () => Promise.resolve())
}

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const setValue = async (el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, v: string) => {
  const proto =
    el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  await act(async () => {
    setter.call(el, v)
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
const flush = async () => {
  for (let i = 0; i < 10; i++) await act(async () => Promise.resolve())
}

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('pa')
  document.body.innerHTML = ''
  startDataFigure.mockClear()
  files.mockReset()
  files.mockResolvedValue({
    files: [
      { path: 'data/a.csv', size: 10 },
      { path: 'data/b.csv', size: 20 },
    ],
    truncated: false,
  })
})

afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('入口只在 TAF 开关开着时出现', () => {
  it.each([false, undefined])('taf_enabled = %s：没有按钮，也不会去拉列表', async (flag) => {
    await mount(flag)
    expect(q('[data-ai-data-figure-open]')).toBeNull()
    expect(q('[data-ai-data-figure-form]')).toBeNull()
    expect(files).not.toHaveBeenCalled()
  })

  it('taf_enabled = true：有按钮，表单默认收起，不拉列表', async () => {
    await mount(true)
    const btn = q('[data-ai-data-figure-open]')!
    expect(btn.textContent).toContain(ai('dataFigure.open'))
    expect(q('[data-ai-data-figure-form]')).toBeNull()
    expect(files).not.toHaveBeenCalled()
  })
})

describe('小表单', () => {
  it('点开：拉列表；选文件 + 写指令后生成可用；提交调用 store 并收起', async () => {
    await mount(true)
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    expect(files).toHaveBeenCalledTimes(1)
    const submit = () => q<HTMLButtonElement>('[data-ai-data-figure-submit]')!
    expect(submit().disabled).toBe(true)
    await setValue(q<HTMLSelectElement>('[data-ai-data-figure-file] select')!, 'data/b.csv')
    expect(submit().disabled).toBe(true) // 还没写指令
    await setValue(q<HTMLTextAreaElement>('[data-ai-data-figure-instruction]')!, '  画 y 随 x 的变化  ')
    expect(submit().disabled).toBe(false)
    await act(async () => submit().click())
    expect(startDataFigure).toHaveBeenCalledWith('data/b.csv', '画 y 随 x 的变化')
    await vi.waitFor(() => expect(q('[data-ai-data-figure-form]')).toBeNull())
  })

  it('空列表：一句话，没有生成钮', async () => {
    files.mockResolvedValue({ files: [], truncated: false })
    await mount(true)
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    expect(q('[data-ai-data-figure-note="empty"]')!.textContent).toBe(ai('dataFigure.empty'))
    expect(q('[data-ai-data-figure-submit]')).toBeNull()
  })

  it('取消：收起且不调用 store', async () => {
    await mount(true)
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    await act(async () => q('[data-ai-data-figure-cancel]')!.click())
    await vi.waitFor(() => expect(q('[data-ai-data-figure-form]')).toBeNull())
    expect(startDataFigure).not.toHaveBeenCalled()
  })

  it('再点一次入口也收起', async () => {
    await mount(true)
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    expect(q('[data-ai-data-figure-form]')).not.toBeNull()
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await vi.waitFor(() => expect(q('[data-ai-data-figure-form]')).toBeNull())
  })

  it('列表读取失败：一句话，不假装是空项目', async () => {
    files.mockRejectedValue(new Error('x'))
    await mount(true)
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    expect(q('[data-ai-data-figure-note="failed"]')!.textContent).toBe(ai('dataFigure.loadFailed'))
  })
})

describe('上一次的结果还没确认', () => {
  it('有 unconfirmed 的数据出图会话：表单提示，且「生成」禁用（防重复提交）', async () => {
    await mount(true)
    await act(async () =>
      useAiStore.setState({
        sessions: [
          {
            id: 'df-1', agent: 'tavotto', agentLabel: 'Tavotto', prompt: '画', script: '', project: 'pa',
            panelId: null, fileId: null, gid: null, scope: 'figure', target: 'data/a.csv', entries: [],
            status: 'failed', changed: false, diff: '', startedAt: 1, unconfirmed: true, requestId: 'r1',
            task: { task_type: 'data_to_figure', outcome: null },
          },
        ],
      } as never),
    )
    await act(async () => q('[data-ai-data-figure-open]')!.click())
    await flush()
    await setValue(q<HTMLSelectElement>('[data-ai-data-figure-file] select')!, 'data/b.csv')
    await setValue(q<HTMLTextAreaElement>('[data-ai-data-figure-instruction]')!, '画点什么')
    expect(q('[data-ai-data-figure-note="unconfirmed"]')!.textContent).toBe(ai('dataFigure.unconfirmedHint'))
    expect(q<HTMLButtonElement>('[data-ai-data-figure-submit]')!.disabled).toBe(true)
  })
})

describe('结果是对话里的一轮', () => {
  it('没选图时，数据出图的会话也显示在对话流里（不是空状态）', async () => {
    await mount(true)
    await act(async () =>
      useAiStore.setState({
        sessions: [
          {
            id: 'df-1', agent: 'tavotto', agentLabel: 'Tavotto', prompt: '画 y 随 x 的变化', script: '', project: 'pa',
            panelId: null, fileId: null, gid: null, scope: 'figure', target: 'data/a.csv', entries: [],
            status: 'running', changed: false, diff: '', startedAt: 1,
            task: { task_type: 'data_to_figure', outcome: null },
          },
        ],
      } as never),
    )
    expect(q('[data-ai-transcript]')).not.toBeNull()
    expect(q('[data-ai-user]')!.textContent).toContain('画 y 随 x 的变化')
    expect(q('[data-ai-send="send"]')).not.toBeNull() // 它不是脚本任务：发送钮仍是发送，不变成中止
  })
})
