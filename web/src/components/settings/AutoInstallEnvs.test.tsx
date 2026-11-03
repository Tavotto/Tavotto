// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchConvertAutoInstallEnvs: vi.fn(),
  revokeConvertAutoInstallEnv: vi.fn(),
}))

import { fetchConvertAutoInstallEnvs, revokeConvertAutoInstallEnv } from '@/lib/api'
import { AutoInstallEnvsSection, autoInstallEnvLabel } from './AutoInstallEnvs'

describe('已允许自动装包的环境（设置）', () => {
  let host: HTMLDivElement
  let root: Root
  beforeEach(() => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    vi.mocked(fetchConvertAutoInstallEnvs).mockReset()
    vi.mocked(revokeConvertAutoInstallEnv).mockReset()
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
  })

  it('每行一个环境名（conda: myenv）+ 一个「撤销」；撤销后这一行消失', async () => {
    vi.mocked(fetchConvertAutoInstallEnvs).mockResolvedValue({
      envs: [{ key: 'k1', kind: 'conda', name: 'myenv' }, { key: 'k2', kind: 'other', name: 'lab' }],
    })
    vi.mocked(revokeConvertAutoInstallEnv).mockResolvedValue({ envs: [{ key: 'k2', kind: 'other', name: 'lab' }] })
    await act(async () => root.render(<AutoInstallEnvsSection />))
    const rows = [...host.querySelectorAll('[data-auto-install-env]')]
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining('conda: myenv'), expect.stringContaining('python: lab')])
    expect(host.querySelectorAll('[data-auto-install-revoke]').length).toBe(2)
    await act(async () => (host.querySelector('[data-auto-install-revoke="k1"]') as HTMLButtonElement).click())
    expect(revokeConvertAutoInstallEnv).toHaveBeenCalledWith('k1')
    expect(host.querySelector('[data-auto-install-env="k1"]')).toBeNull()
    expect(host.querySelector('[data-auto-install-env="k2"]')).not.toBeNull()
  })

  it('没有环境时只有一句说明', async () => {
    vi.mocked(fetchConvertAutoInstallEnvs).mockResolvedValue({ envs: [] })
    await act(async () => root.render(<AutoInstallEnvsSection />))
    expect(host.querySelector('[data-auto-install-empty]')?.textContent).toBe('还没有环境被允许自动装包。')
    expect(host.querySelectorAll('button').length).toBe(0)
  })

  it('读取失败也只是空列表，不抛', async () => {
    vi.mocked(fetchConvertAutoInstallEnvs).mockRejectedValue(new Error('x'))
    await act(async () => root.render(<AutoInstallEnvsSection />))
    expect(host.querySelector('[data-auto-install-empty]')).not.toBeNull()
  })

  it('可读名不含路径：种类 + 目录名', () => {
    expect(autoInstallEnvLabel({ kind: 'conda', name: 'a' })).toBe('conda: a')
    expect(autoInstallEnvLabel({ kind: 'project_venv', name: 'p/.venv' })).toBe('venv: p/.venv')
  })
})
