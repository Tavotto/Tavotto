/**
 * 助手输入框的附件（ADR 0175）：入口（回形针 / 粘贴 / 拖入，任意文件）、输入框上方的预览行、发送带暂存 id、对话里的两个气泡、
 * 模型看不了图时的一句话。判据的主语：**不附文件时发给后端的请求里没有 `attachments`**（逐字节不变）；附文件时只带暂存 id，字节不进 `aiRun` 的请求。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import '@/hooks/useServerEvents'
import { aiRun, ApiError, discardAiAttachment, fetchAiRequest, fetchAiSession, uploadAiAttachment } from '@/lib/api'
import { __resetWriteStampForTests } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useAiAttachStore } from '@/store/aiAttachStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'
import { ATTACH_TILTS } from './AttachCards'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRun: vi.fn(),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
  uploadAiAttachment: vi.fn(),
  discardAiAttachment: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const PNG = Uint8Array.from(
  atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='),
  (c) => c.charCodeAt(0),
)
const png = (name = 'shot.png') => new File([PNG], name, { type: 'image/png' })
const csv = (name = 'data.csv') => new File(['a,b\n1,2\n'], name, { type: 'text/csv' })

let root: Root | null = null
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const qa = (sel: string) => Array.from(host.querySelectorAll<HTMLElement>(sel))
const input = () => q<HTMLTextAreaElement>('[data-ai-input]')!
const settle = async () => {
  for (let i = 0; i < 30; i++) await act(async () => new Promise((r) => setTimeout(r, 0)))
}
const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
  await settle()
}
const typeText = async (text: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const click = async (el: Element | null) => {
  expect(el, '要点的按钮不在').toBeTruthy()
  await act(async () => (el as HTMLElement).click())
}
/** 在输入框上派发一次粘贴：`files` 是剪贴板里的文件 */
const paste = async (files: File[]) => {
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', { value: { files } })
  await act(async () => {
    input().dispatchEvent(ev)
  })
  await settle()
  return ev
}
const drop = async (files: File[]) => {
  const ev = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'dataTransfer', { value: { files, types: ['Files'] } })
  await act(async () => {
    q('[data-ai-composer]')!.dispatchEvent(ev)
  })
  await settle()
  return ev
}

const panelOf = (fileId: string, id: string): PanelObject =>
  ({
    id, type: 'panel', x: 0, y: 0, w: 100, h: 75, fileId, fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: fileId, script: `${id}.py`, overrides: [],
  }) as unknown as PanelObject

let be: ReturnType<typeof createDraftBackend>
let nextId = 0

beforeEach(async () => {
  __resetWriteStampForTests()
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
  setCurrentProjectId('pa')
  nextId = 0
  vi.mocked(aiRun).mockReset().mockResolvedValue({ session: 's-1', script: 'p1.py' })
  vi.mocked(uploadAiAttachment)
    .mockReset()
    .mockImplementation(async (blob: Blob, name: string) => ({
      id: `${(++nextId).toString(16).padStart(32, '0')}`,
      kind: blob.type.startsWith('image/') ? 'image' : 'file',
      mime: blob.type || null,
      bytes: blob.size,
      width: null,
      height: null,
      name,
    }))
  vi.mocked(discardAiAttachment).mockReset().mockResolvedValue({ ok: true })
  vi.mocked(fetchAiRequest).mockReset().mockRejectedValue(new TypeError('down'))
  vi.mocked(fetchAiSession).mockReset().mockRejectedValue(new ApiError('nf', 404, {}))
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiAttachStore.setState({ byTarget: {} })
  useAiStore.setState({
    sessions: [], submissions: {}, scope: 'figure',
    caps: capsOf([agentCaps()]), agent: 'codex', models: {}, efforts: {},
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai_ref')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1')] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
})
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('入口与预览行', () => {
  it('空态：工具行里有回形针、没有预览行；没附文件时不多出任何东西', async () => {
    await mount()
    expect(q('[data-ai-attach-btn]')).toBeTruthy()
    expect(q('[data-ai-attach-row]')).toBeNull()
    expect(qa('[data-ai-attach-chip]')).toHaveLength(0)
    // 回形针住在原有的工具行里（与模型胶囊同一个父节点），不是新加的一行
    expect(q('[data-ai-attach-btn]')!.parentElement).toBe(q('[data-ai-pill="model"]')!.parentElement)
  })

  it('模型胶囊用提供方 logo、不再写「提供方 ·」', async () => {
    await mount()
    const pill = q('[data-ai-pill="model"]')!
    expect(pill.querySelector('[data-brand-logo]')).toBeTruthy()
    expect(pill.textContent).not.toContain('·')
  })

  it('粘贴截图：预览行出现一张卡、传给后端暂存；粘贴文字不拦', async () => {
    await mount()
    const textPaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(textPaste, 'clipboardData', { value: { files: [] } })
    await act(async () => input().dispatchEvent(textPaste))
    expect(textPaste.defaultPrevented).toBe(false)
    expect(qa('[data-ai-attach-chip]')).toHaveLength(0)

    const ev = await paste([png()])
    expect(ev.defaultPrevented).toBe(true)
    expect(qa('[data-ai-attach-chip="ready"]')).toHaveLength(1)
    expect(q('[data-ai-attach-row]')).toBeTruthy()
    expect(uploadAiAttachment).toHaveBeenCalledTimes(1)
    expect(vi.mocked(uploadAiAttachment).mock.calls[0][1]).toBe('shot.png')
  })

  it('复制自表格 / 网页的「文字 + 渲染图」按文字粘贴，不拦', async () => {
    await mount()
    const ev = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(ev, 'clipboardData', { value: { files: [png()], getData: () => 'a\tb' } })
    await act(async () => input().dispatchEvent(ev))
    await settle()
    expect(ev.defaultPrevented).toBe(false)
    expect(qa('[data-ai-attach-chip]')).toHaveLength(0)
  })

  it('CSV 等非图片文件也能附：显示成文件卡（扩展名角标 + 文件名），不出缩略图', async () => {
    await mount()
    await paste([csv('结果.csv')])
    const card = q('[data-ai-attach-chip]')!
    expect(card.querySelector('img')).toBeNull()
    expect(card.textContent).toContain('csv')
    expect(card.textContent).toContain('结果.csv')
  })

  it('预览卡的倾斜角是确定的序列（同一个序号永远同一个角度）', async () => {
    await mount()
    await paste([png('1.png'), csv('2.csv'), png('3.png')])
    const tilts = qa('[data-ai-attach-chip]').map((c) => c.style.getPropertyValue('--tilt'))
    expect(tilts).toEqual(ATTACH_TILTS.slice(0, 3).map((t) => `${t}deg`))
  })

  it('卡片按选中的顺序排（文件类型、处理快慢不影响顺序）', async () => {
    await mount()
    await paste([csv('a.csv'), png('b.png'), csv('c.csv')])
    expect(qa('[data-ai-attach-chip]').map((c) => c.title)).toEqual(['a.csv', 'b.png', 'c.csv'])
  })

  it('点 × 去掉一个：卡片消失，后端暂存也删', async () => {
    await mount()
    await paste([png('a.png'), csv('b.csv')])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(2)
    await click(qa('[data-ai-attach-remove]')[0])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(1)
    expect(discardAiAttachment).toHaveBeenCalledTimes(1)
  })

  it('拖进输入框也能附', async () => {
    await mount()
    await drop([png(), csv()])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(2)
  })

  it('拖放 / 粘贴不冒泡给外层（画布级的拖放处理接不走）', async () => {
    await mount()
    const outer = vi.fn()
    document.body.addEventListener('drop', outer)
    document.body.addEventListener('paste', outer)
    await drop([png()])
    await paste([png()])
    document.body.removeEventListener('drop', outer)
    document.body.removeEventListener('paste', outer)
    expect(outer).not.toHaveBeenCalled()
  })

  it('最多 6 个：多的不收，并说一句', async () => {
    await mount()
    await paste([png('1.png'), csv('2.csv'), png('3.png'), csv('4.csv')])
    await paste([png('5.png'), csv('6.csv'), png('7.png'), csv('8.csv')])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(6)
    expect(uploadAiAttachment).toHaveBeenCalledTimes(6)
    expect(q<HTMLButtonElement>('[data-ai-attach-btn]')!.disabled).toBe(true)
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('单个文件超过上限：不附、说一句，输入框照常能发', async () => {
    await mount()
    const big = new File([new Uint8Array(25 * 1024 * 1024 + 1)], 'huge.bin')
    await paste([big])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(0)
    expect(uploadAiAttachment).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toBeTruthy()
    await typeText('把线条加粗')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })

  it('上传失败：那一个悄悄去掉并说一句，不卡发送', async () => {
    vi.mocked(uploadAiAttachment).mockRejectedValueOnce(new TypeError('down'))
    await mount()
    await paste([png()])
    expect(qa('[data-ai-attach-chip]')).toHaveLength(0)
    expect(useUiStore.getState().status).toBeTruthy()
    await typeText('把线条加粗')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })
})

describe('发送', () => {
  it('不附文件：请求里没有 attachments 这个键（与原来逐字节相同），对话里没有附件气泡', async () => {
    await mount()
    await typeText('把线条加粗')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    const req = vi.mocked(aiRun).mock.calls[0][0] as unknown as Record<string, unknown>
    expect('attachments' in req).toBe(false)
    expect(q('[data-ai-user-attachments]')).toBeNull()
  })

  it('附了 2 图 + 1 CSV：请求只带暂存 id；发送后预览行清空；用户消息拆成文字 + 附件两个气泡，点一下展开、再点收回', async () => {
    await mount()
    await paste([png('a.png'), png('b.png'), csv('c.csv')])
    await typeText('按参考图的配色改，数据见 csv')
    await click(q('[data-ai-send="send"]'))
    await settle()
    const req = vi.mocked(aiRun).mock.calls[0][0] as { attachments?: string[] }
    expect(req.attachments).toEqual([1, 2, 3].map((n) => n.toString(16).padStart(32, '0')))
    expect(JSON.stringify(req)).not.toContain('iVBOR')
    expect(q('[data-ai-attach-row]')).toBeNull()
    expect(useAiStore.getState().sessions[0].attachmentCount).toBe(3)
    const bubble = q<HTMLElement>('[data-ai-user-attachments="stack"]')!
    expect(bubble).toBeTruthy()
    expect(bubble.getAttribute('aria-expanded')).toBe('false')
    expect(bubble.textContent).toContain('c.csv')
    // 文字气泡与附件气泡是两个节点
    expect(q('[data-ai-user] p')!.closest('[data-ai-user-attachments]')).toBeNull()
    await click(bubble)
    expect(bubble.getAttribute('aria-expanded')).toBe('true')
    await click(bubble)
    expect(bubble.getAttribute('aria-expanded')).toBe('false')
  })

  it('接回 / 历史里的会话没有明细：附件气泡只写个数', async () => {
    await mount()
    useAiStore.setState({
      sessions: [
        {
          id: 's-old', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: '旧的指令', script: 'p1.py', panelId: 'p1', fileId: 'Fig1.pdf',
          gid: null, scope: 'figure', target: '整张图', entries: [], status: 'done', changed: false, diff: '', startedAt: 1, finishedAt: 2,
          finalized: true, attachmentCount: 3,
        },
      ],
    })
    await settle()
    const b = q('[data-ai-user-attachments="count"]')!
    expect(b.textContent).toContain('3')
  })

  it('只有文件、没写字：不能发（指令仍是必填）', async () => {
    await mount()
    await paste([csv()])
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('后端拒绝发送：文件还留在预览行里，可以改了再发', async () => {
    vi.mocked(aiRun).mockRejectedValueOnce(new ApiError('no', 409, { code: 'ai_script_busy' }))
    await mount()
    await paste([png()])
    await typeText('改一下')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(qa('[data-ai-attach-chip]')).toHaveLength(1)
  })
})

describe('模型看不了图', () => {
  const noVisionCaps = () =>
    capsOf([agentCaps({ models: ['deepseek-chat', 'gpt-5'], default_model: 'deepseek-chat', no_vision: ['deepseek-chat'] })])

  it('附了图 + 当前模型确定看不了：居中的一句话 + 换过去的按钮；没附图不出现；只附 CSV 也不出现', async () => {
    useAiStore.setState({ caps: noVisionCaps() })
    await mount()
    expect(q('[data-ai-draft-note="no-vision"]')).toBeNull()
    await paste([csv()])
    expect(q('[data-ai-draft-note="no-vision"]'), '只附数据文件：不关模型看不看得了图的事').toBeNull()
    await paste([png()])
    expect(q('[data-ai-draft-note="no-vision"]')).toBeTruthy()
    expect(q('[data-ai-draft-note="no-vision"]')!.className).toContain('text-center')
    await click(q('[data-ai-attach-switch]'))
    expect(useAiStore.getState().models.codex).toBe('gpt-5')
    expect(q('[data-ai-draft-note="no-vision"]')).toBeNull()
  })

  it('判不出的模型照常发送：不出提示', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps({ models: ['some-new-model'], default_model: 'some-new-model' })]) })
    await mount()
    await paste([png()])
    expect(q('[data-ai-draft-note="no-vision"]')).toBeNull()
  })

  it('没有别的能看图的模型可换：只说一句，不出按钮', async () => {
    useAiStore.setState({
      caps: capsOf([agentCaps({ models: ['deepseek-chat'], default_model: 'deepseek-chat', no_vision: ['deepseek-chat'] })]),
    })
    await mount()
    await paste([png()])
    expect(q('[data-ai-draft-note="no-vision"]')).toBeTruthy()
    expect(q('[data-ai-attach-switch]')).toBeNull()
  })
})
