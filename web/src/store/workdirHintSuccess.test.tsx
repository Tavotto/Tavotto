/**
 * #1254：build **成功**、但沙盒里 glob 回空而脚本目录里有文件（`ax.plot([])` 画出一条空线）——图可能缺数据。
 * 渲染 / 试运行成功响应带 `workdir_hint` 时，提醒走**通知轨**（胶囊：一句话 + 一个按钮），不是横条、不占画布。
 *
 * 钉：有 hint 才出现；同一面板同一份证据只提醒一次；选了「继续沙盒」（decided）之后不再出现；之后不再带 hint
 * （切到脚本目录）就撤掉；按钮打开的是错误块同一个确认框；试运行成功同样提醒。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EngineRenderOptions, Manifest, WorkdirConfirmation } from '@/lib/api'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

import { NotificationRail } from '@/components/StatusBar'
import { resetWorkdirHintNotices, workdirHintOwner } from '@/lib/workdirHintNotice'
import { t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const hint = {
  kind: 'workdir',
  code: 'workdir_confirmation_required',
  script: 'fig.py',
  reason: 'script_dir_evidence',
  recommended: 'project',
  options: [],
  conflicts: [],
  reads: [],
  probes: ['data/run_*.txt'],
  message: '',
  source: 'runtime',
} as unknown as WorkdirConfirmation

const manifest = { stem: 'Fig1', size_mm: [80, 60], elements: [] } as unknown as Manifest
const ok = (extra: object = {}) => ({ rev: 1, manifest, warnings: [], svg: '<svg/>', ...extra })
const envWith = (decided: boolean) =>
  ({
    ok: true,
    project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project'], decided } },
  }) as never

let container: HTMLDivElement
let root: Root
const sentence = () => t('engine.workdirHintEmptyFigure', { ns: 'errors', script: 'fig.py' })
const status = () => useUiStore.getState().status
const owner = () => useUiStore.getState().statusOwner

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<NotificationRail />))
}

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
  resetWorkdirHintNotices()
  useUiStore.getState().setStatus(null)
  useEnvStore.setState({ env: envWith(false) })
})
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove()
})

describe('渲染成功带 hint', () => {
  it('面板记下 hint，通知轨出现一条胶囊（一句话 + 一个按钮），图照常 ready', async () => {
    engineRender.mockResolvedValue(ok({ workdir_hint: hint }))
    await mount()
    await useRenderStore.getState().render('Fig1.pdf', [])
    const entry = useRenderStore.getState().get(renderKey('Fig1.pdf', []))
    expect(entry.status).toBe('ready')
    expect(entry.workdirHint).toEqual(hint)
    expect(owner()).toMatch(/^workdir\.hint:/)
    await act(async () => {})
    const pill = container.querySelector('[data-toast-layout="pill"]')!
    expect(pill.textContent).toContain(sentence())
    expect(pill.textContent).toContain(t('engine.workdirChooseButton', { ns: 'errors' }))
    // 不是横条：没有任何占布局的提示条元素
    expect(document.querySelector('[data-banner], [role="banner"]')).toBeNull()
  })

  it('按钮打开的是错误块同一个确认框', async () => {
    const request = vi.fn()
    useEnvStore.setState({ requestWorkdirConfirmation: request } as never)
    engineRender.mockResolvedValue(ok({ workdir_hint: hint }))
    await useRenderStore.getState().render('Fig1.pdf', [])
    await act(async () => useUiStore.getState().statusAction!.run())
    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][0]).toEqual(hint)
  })

  it('没有 hint → 不出现', async () => {
    engineRender.mockResolvedValue(ok())
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).toBeNull()
    expect(useRenderStore.getState().get(renderKey('Fig1.pdf', [])).workdirHint).toBeNull()
  })

  it('选了「继续沙盒」（decided）之后不再提醒，面板上的记录仍在', async () => {
    useEnvStore.setState({ env: envWith(true) })
    engineRender.mockResolvedValue(ok({ workdir_hint: hint }))
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).toBeNull()
  })

  it('同一面板同一份证据只提醒一次：关掉之后重渲染不再弹', async () => {
    engineRender.mockResolvedValue(ok({ workdir_hint: hint }))
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).not.toBeNull()
    useUiStore.getState().setStatus(null) // 用户点了 ×
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).toBeNull()
  })

  it('之后不再带 hint（切到脚本目录、读到了数据）→ 把这个面板的提醒撤掉', async () => {
    engineRender.mockResolvedValue(ok({ workdir_hint: hint }))
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).not.toBeNull()
    engineRender.mockResolvedValue(ok())
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).toBeNull()
  })

  it('别的主人的 toast 不被撤', async () => {
    useUiStore.getState().setStatus(t('engine.workdirChooseButton', { ns: 'errors' }) as never, 'info', {
      owner: workdirHintOwner('other', 'x'),
    })
    engineRender.mockResolvedValue(ok())
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(status()).not.toBeNull()
  })
})
