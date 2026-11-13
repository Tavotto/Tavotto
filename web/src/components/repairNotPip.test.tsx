import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchEngineEnvironment: vi.fn().mockResolvedValue({}),
}))

import type { DependencyRepairOffer } from '@/lib/api'
import { DependencyRepairCard } from '@/components/DependencyRepairCard'
import { i18n } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { __resetDepRepairParkingForTests, useDepRepairStore } from '@/store/depRepairStore'
import { useUiStore } from '@/store/uiStore'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

// Codex #1276 P2：后端报 not_pip_installable（ROOT）又没有可用的已有解释器时，卡片专门说一句、主按钮去选环境，不给「指定包名」
const OFFER: DependencyRepairOffer = {
  import_name: 'ROOT',
  script: 'fig.py',
  requirement: null,
  targets: [],
  rounds_remaining: 3,
  python_supported: { min: '3.10', max: '3.14' },
  code: 'dependency_unresolved',
  not_pip_installable: { label: 'ROOT', hint: 'own_env' },
}

// 「指定包名」输入（收起的「高级」里还有一个选解释器路径的输入，那个是对的出口）
const PKG_INPUT = () => `input[placeholder="${i18n.t('engine.repairPackagePlaceholder', { ns: 'errors' })}"]`

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  __resetDepRepairParkingForTests()
  setCurrentProjectId('p1')
  useDepRepairStore.getState().reset()
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  await i18n.changeLanguage('zh-CN')
})

async function mount(offer: DependencyRepairOffer = OFFER, module = 'ROOT') {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<DependencyRepairCard offer={offer} module={module} script="fig.py" />))
  await act(async () => {})
}

describe('装不了的包（ROOT）修复卡', () => {
  it('中文：点名 + 一个主按钮打开环境选择；没有指定包名输入', async () => {
    await i18n.changeLanguage('zh-CN')
    await mount()
    expect(host.querySelector('[data-repair-not-pip-sentence]')?.textContent).toBe(
      'ROOT 不能用 pip 安装，要在装有它的 Python 环境里运行。',
    )
    expect(host.querySelector(PKG_INPUT())).toBeNull()
    const btn = host.querySelector('[data-repair-open-environment]') as HTMLButtonElement
    expect(btn).not.toBeNull()
    await act(async () => btn.click())
    expect(useUiStore.getState().engineEnvOpen).toBe(true)
  })

  it('英文', async () => {
    await i18n.changeLanguage('en-US')
    await mount()
    expect(host.querySelector('[data-repair-not-pip-sentence]')?.textContent).toBe(
      'ROOT can’t be installed with pip; run it in a Python environment that already has it.',
    )
    expect(host.querySelector(PKG_INPUT())).toBeNull()
  })
})

// TVD-NH7S：ChatGPT 网页沙箱专有的模块（artifact_tool）：哪个环境都没有，主按钮复制「请 AI 去掉它重新生成」的话，不给选环境
const SANDBOX: DependencyRepairOffer = {
  ...OFFER,
  import_name: 'artifact_tool',
  not_pip_installable: { label: 'artifact_tool', hint: 'ai_sandbox' },
}

describe('AI 网页沙箱模块修复卡', () => {
  it('一句话 + 复制给 AI 的话；没有选环境、没有指定包名', async () => {
    await i18n.changeLanguage('zh-CN')
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await mount(SANDBOX, 'artifact_tool')
    expect(host.querySelector('[data-repair-not-pip-sentence]')?.textContent).toBe(
      'artifact_tool 只在 ChatGPT 网页里有、本机装不了，请让 AI 去掉它重新生成脚本。',
    )
    expect(host.querySelector(PKG_INPUT())).toBeNull()
    // 选环境只剩收起的「高级」里那一颗备选（和别的卡一样），不是主按钮
    for (const b of host.querySelectorAll('[data-repair-open-environment]')) expect(b.closest('details')).not.toBeNull()
    const btn = host.querySelector('[data-repair-copy-for-ai]') as HTMLButtonElement
    expect(btn.textContent).toBe('复制给 AI 的话')
    await act(async () => btn.click())
    await act(async () => {})
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('请去掉 artifact_tool'))
    expect(btn.textContent).toBe('已复制，粘贴给 AI')
  })

  it('剪贴板被挡住时不装成功', async () => {
    await i18n.changeLanguage('zh-CN')
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await mount(SANDBOX, 'artifact_tool')
    const btn = host.querySelector('[data-repair-copy-for-ai]') as HTMLButtonElement
    await act(async () => btn.click())
    await act(async () => {})
    expect(btn.textContent).toBe('复制给 AI 的话')
  })
})
