/**
 * 助手输入框的参考图（ADR 0175）：入口（图片钮 / 粘贴 / 拖入）、一排缩略图、发送带暂存 id、模型看不了图时的一句话。
 * 判据的主语：**不附图时发给后端的请求里没有 `ref_images`**（逐字节不变）；附图时只带暂存 id，图片字节不进 `aiRun` 的请求。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import '@/hooks/useServerEvents'
import { aiRun, ApiError, discardAiRefImage, fetchAiRequest, fetchAiSession, uploadAiRefImage } from '@/lib/api'
import { __resetWriteStampForTests } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useAiRefStore } from '@/store/aiRefStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRun: vi.fn(),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
  uploadAiRefImage: vi.fn(),
  discardAiRefImage: vi.fn(),
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
  vi.mocked(uploadAiRefImage)
    .mockReset()
    .mockImplementation(async () => ({ id: `${(++nextId).toString(16).padStart(32, '0')}`, mime: 'image/png', bytes: PNG.length, width: 1, height: 1 }))
  vi.mocked(discardAiRefImage).mockReset().mockResolvedValue({ ok: true })
  vi.mocked(fetchAiRequest).mockReset().mockRejectedValue(new TypeError('down'))
  vi.mocked(fetchAiSession).mockReset().mockRejectedValue(new ApiError('nf', 404, {}))
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiRefStore.setState({ byTarget: {} })
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

describe('入口与一排缩略图', () => {
  it('空态：工具行里有图片钮、没有缩略图，输入框的高度类没有变（不撑大卡片）', async () => {
    await mount()
    expect(q('[data-ai-ref-attach]')).toBeTruthy()
    expect(qa('[data-ai-ref-chip]')).toHaveLength(0)
    // 图片钮与缩略图都住在原有的工具行里，不是新加的一行
    expect(q('[data-ai-ref-attach]')!.parentElement).toBe(q('[data-ai-pill="model"]')!.parentElement)
  })

  it('粘贴截图：出现一枚缩略图、传给后端暂存；粘贴文字不拦', async () => {
    await mount()
    const textPaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(textPaste, 'clipboardData', { value: { files: [] } })
    await act(async () => input().dispatchEvent(textPaste))
    expect(textPaste.defaultPrevented).toBe(false)
    expect(qa('[data-ai-ref-chip]')).toHaveLength(0)

    const ev = await paste([png()])
    expect(ev.defaultPrevented).toBe(true)
    expect(qa('[data-ai-ref-chip="ready"]')).toHaveLength(1)
    expect(uploadAiRefImage).toHaveBeenCalledTimes(1)
    expect((vi.mocked(uploadAiRefImage).mock.calls[0][0] as Blob).type).toBe('image/png')
  })

  it('点 × 去掉一张：缩略图消失，后端暂存也删', async () => {
    await mount()
    await paste([png('a.png'), png('b.png')])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(2)
    await click(qa('[data-ai-ref-remove]')[0])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(1)
    expect(discardAiRefImage).toHaveBeenCalledTimes(1)
  })

  it('拖进输入框也能附；不是图片的文件悄悄忽略', async () => {
    await mount()
    await drop([png(), new File(['x'], 'notes.txt', { type: 'text/plain' })])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(1)
  })

  it('最多 4 张：多的不收，并说一句', async () => {
    await mount()
    await paste([png('1.png'), png('2.png'), png('3.png')])
    await paste([png('4.png'), png('5.png'), png('6.png')])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(4)
    expect(uploadAiRefImage).toHaveBeenCalledTimes(4)
    expect(q<HTMLButtonElement>('[data-ai-ref-attach]')!.disabled).toBe(true)
    expect(useUiStore.getState().status).toBeTruthy()
  })

  it('认不出的格式（浏览器解不了的 HEIC 等）：不加缩略图、说一句，输入框照常能发', async () => {
    await mount()
    await paste([new File([new Uint8Array(32)], 'x.heic', { type: 'image/heic' })])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(0)
    expect(uploadAiRefImage).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toBeTruthy()
    await typeText('把线条加粗')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })

  it('上传失败：那一张悄悄去掉并说一句，不卡发送', async () => {
    vi.mocked(uploadAiRefImage).mockRejectedValueOnce(new TypeError('down'))
    await mount()
    await paste([png()])
    expect(qa('[data-ai-ref-chip]')).toHaveLength(0)
    expect(useUiStore.getState().status).toBeTruthy()
    await typeText('把线条加粗')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })
})

describe('发送', () => {
  it('不附图：请求里没有 ref_images 这个键（与原来逐字节相同）', async () => {
    await mount()
    await typeText('把线条加粗')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    const req = vi.mocked(aiRun).mock.calls[0][0] as unknown as Record<string, unknown>
    expect('ref_images' in req).toBe(false)
    expect(q('[data-ai-user-refs]')).toBeNull()
  })

  it('附了 2 张：请求只带暂存 id（不带图片字节），发送后缩略图排清空，对话里的用户消息带缩略图', async () => {
    await mount()
    await paste([png('a.png'), png('b.png')])
    const ids = vi.mocked(uploadAiRefImage).mock.results.length
    expect(ids).toBe(2)
    await typeText('按参考图的配色改')
    await click(q('[data-ai-send="send"]'))
    await settle()
    const req = vi.mocked(aiRun).mock.calls[0][0] as { ref_images?: string[] }
    expect(req.ref_images).toEqual(['00000000000000000000000000000001', '00000000000000000000000000000002'])
    expect(JSON.stringify(req)).not.toContain('iVBOR')
    expect(qa('[data-ai-ref-chip]')).toHaveLength(0)
    expect(useAiStore.getState().sessions[0].refCount).toBe(2)
    expect(qa('[data-ai-user-refs]').length).toBeGreaterThan(0)
  })

  it('只有图、没写字：不能发（指令仍是必填）', async () => {
    await mount()
    await paste([png()])
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('后端拒绝发送：图还留在输入框里，可以改了再发', async () => {
    vi.mocked(aiRun).mockRejectedValueOnce(new ApiError('no', 409, { code: 'ai_script_busy' }))
    await mount()
    await paste([png()])
    await typeText('改一下')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(qa('[data-ai-ref-chip]')).toHaveLength(1)
  })
})

describe('模型看不了图', () => {
  const noVisionCaps = () =>
    capsOf([agentCaps({ models: ['deepseek-chat', 'gpt-5'], default_model: 'deepseek-chat', no_vision: ['deepseek-chat'] })])

  it('附了图 + 当前模型确定看不了：一句话 + 换过去的按钮；没附图不出现', async () => {
    useAiStore.setState({ caps: noVisionCaps() })
    await mount()
    expect(q('[data-ai-draft-note="no-vision"]')).toBeNull()
    await paste([png()])
    expect(q('[data-ai-draft-note="no-vision"]')).toBeTruthy()
    await click(q('[data-ai-ref-switch]'))
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
    expect(q('[data-ai-ref-switch]')).toBeNull()
  })
})
