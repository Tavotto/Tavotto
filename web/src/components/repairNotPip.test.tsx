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

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<DependencyRepairCard offer={OFFER} module="ROOT" script="fig.py" />))
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
