/**
 * 设置里的自定义 CLI 路径（issue #89）：已存路径要回显，失焦只在真的
 * 改过时才 PATCH。以前 codexPath 从 '' 起步、onBlur 无条件提交——打开
 * 设置再移走一次焦点，空字符串就把用户存好的 codex_path 删掉了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
  patchAiSettings: vi.fn(),
}))

import {
  fetchAiCapabilities,
  patchAiSettings,
  type AiCapabilities,
  type AiProviderCaps,
} from '@/lib/api'
import { SettingsDialog } from '@/components/SettingsDialog'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const fetchMock = vi.mocked(fetchAiCapabilities)
const patchMock = vi.mocked(patchAiSettings)

const provider = (over: Partial<AiProviderCaps> = {}): AiProviderCaps => ({
  installed: true,
  path: '/usr/bin/cli',
  argv: ['/usr/bin/cli'],
  version: 'test 1.0.0',
  models: [],
  default_model: null,
  efforts: [],
  default_effort: null,
  endpoint: null,
  ...over,
})

const caps = (over: Partial<AiCapabilities> = {}): AiCapabilities => ({
  providers: { codex: provider(), claude: provider() },
  settings: { codex_path: '/saved/codex', claude_path: null },
  endpoints: [],
  presets: [],
  active: { codex: null, claude: null },
  ...over,
})

const st = (key: string) => t(`settings.${key}`, { ns: 'dialogs' })

let host: HTMLDivElement
let root: Root

async function open(initial = caps()) {
  fetchMock.mockResolvedValue(initial)
  patchMock.mockResolvedValue(initial)
  useAiStore.setState({ caps: initial })
  useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<SettingsDialog />)
  })
  await act(async () => {})
}

/** 两个路径输入框共用一个占位符，按文档顺序 codex 在前 */
const pathInputs = () =>
  [...document.querySelectorAll('input')].filter(
    (i) => i.placeholder === st('ai.pathPlaceholder'),
  ) as HTMLInputElement[]

const setValue = (input: HTMLInputElement, v: string) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )!.set!
  setter.call(input, v)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  fetchMock.mockReset()
  patchMock.mockReset()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

describe('AI 设置的自定义 CLI 路径', () => {
  it('回显已存的路径', async () => {
    await open()
    const [codex, claude] = pathInputs()
    expect(codex.value).toBe('/saved/codex')
    expect(claude.value).toBe('')
  })

  it('没改动的失焦一个请求都不发（曾经会把已存路径删掉）', async () => {
    await open()
    const [codex] = pathInputs()
    await act(async () => {
      codex.focus()
      codex.blur()
    })
    expect(patchMock).not.toHaveBeenCalled()
    expect(codex.value).toBe('/saved/codex')
  })

  it('真的改过才提交，并随探测结果刷新', async () => {
    await open()
    const [codex] = pathInputs()
    const updated = caps({
      settings: { codex_path: '/new/codex', claude_path: null },
    })
    patchMock.mockResolvedValue(updated)
    fetchMock.mockResolvedValue(updated)
    await act(async () => {
      codex.focus()
      setValue(codex, '/new/codex')
    })
    await act(async () => {
      codex.blur()
    })
    expect(patchMock).toHaveBeenCalledExactlyOnceWith({ codex_path: '/new/codex' })
    expect(fetchMock).toHaveBeenCalledWith(true) // 提交后重探测
    expect(pathInputs()[0].value).toBe('/new/codex')
  })

  it('提交在途时的新编辑不被完成回调顶掉', async () => {
    await open()
    const [codex] = pathInputs()
    let resolvePatch!: (v: AiCapabilities) => void
    patchMock.mockImplementation(
      () => new Promise<AiCapabilities>((r) => (resolvePatch = r)),
    )
    await act(async () => {
      codex.focus()
      setValue(codex, '/new/codex')
    })
    await act(async () => {
      codex.blur() // PATCH + 重探测在途（可达数秒）
    })
    await act(async () => {
      codex.focus()
      setValue(codex, '/newer/codex') // 在途期间继续编辑
    })
    const updated = caps({
      settings: { codex_path: '/new/codex', claude_path: null },
    })
    fetchMock.mockResolvedValue(updated)
    await act(async () => {
      resolvePatch(updated)
    })
    expect(pathInputs()[0].value).toBe('/newer/codex') // 新草稿仍在
  })

  it('清空后失焦 = 显式删除，照样提交', async () => {
    await open()
    const [codex] = pathInputs()
    const cleared = caps({ settings: { codex_path: null, claude_path: null } })
    patchMock.mockResolvedValue(cleared)
    fetchMock.mockResolvedValue(cleared)
    await act(async () => {
      codex.focus()
      setValue(codex, '')
    })
    await act(async () => {
      codex.blur()
    })
    expect(patchMock).toHaveBeenCalledExactlyOnceWith({ codex_path: '' })
    expect(pathInputs()[0].value).toBe('')
  })
})
