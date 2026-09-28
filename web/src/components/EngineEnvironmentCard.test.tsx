/**
 * 渲染环境卡片里「项目已改用：<路径>」那一行的缘由（ADR 0044 / 0107）：缺包时无提示自动采用的系统 Python
 * 不是「你为项目选择的」——措辞按 `automatic` 分；两种都给「改用内置环境」一键改回。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchEngineEnvironment: vi.fn(),
}))

import type { EngineEnvironment } from '@/lib/api'
import { EngineEnvironmentCard } from '@/components/EngineEnvironmentCard'
import { t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const en = (key: string, values?: Record<string, unknown>) =>
  t(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

const envWith = (automatic: boolean): EngineEnvironment =>
  ({
    ok: true,
    python: 'C:\\Python312\\python.exe',
    source: 'system',
    matplotlib: '3.11.2',
    managed: false,
    bundled: false,
    runtime: {} as never,
    state: 'idle',
    project: {
      open: true,
      source: 'system',
      python: 'C:\\Python312\\python.exe',
      automatic,
      trigger: automatic ? 'missing_dependency' : 'user_selected',
      module: 'adjustText',
    },
  }) as never

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}
const text = () => document.body.textContent ?? ''

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('项目改用的系统 Python：缘由按 automatic 分', () => {
  it('自动采用的：说「已自动改用这台机器上装着它的 Python」，不说「你选择的」', async () => {
    useEnvStore.setState({ env: envWith(true) })
    await render(<EngineEnvironmentCard />)
    expect(text()).toContain(en('projectEnvUsingSystem', { path: 'C:\\Python312\\python.exe' }))
    expect(text()).toContain(en('projectEnvWhySystemAuto', { module: 'adjustText' }))
    expect(text()).not.toContain(en('projectEnvWhySystem', { module: 'adjustText' }))
    expect(text()).toContain(en('projectEnvUseBuiltIn'))
  })

  it('用户挑的：仍说「已改用你为项目选择的现有环境」', async () => {
    useEnvStore.setState({ env: envWith(false) })
    await render(<EngineEnvironmentCard />)
    expect(text()).toContain(en('projectEnvWhySystem', { module: 'adjustText' }))
    expect(text()).not.toContain(en('projectEnvWhySystemAuto', { module: 'adjustText' }))
    expect(text()).toContain(en('projectEnvUseBuiltIn'))
  })
})
