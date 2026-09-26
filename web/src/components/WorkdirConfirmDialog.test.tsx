/**
 * 首开的那一次确认（U03，ADR 0057）。判据的主语：后端给的 `workdir_confirmation_required`
 * 载荷在界面上怎么变成一次选择——
 * ① 推荐项预选、歧义时不预选（机器不裁决）；② 「运行」只发一次 PATCH（`confirmed`：
 *   不再弹第二层确认框）并把「先选目录」的面板重新排上；③ 「稍后」只关框，载荷留在
 *   渲染条目上、错误块的按钮能再打开；④ 换项目清掉；⑤ 同一时刻只开一份。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  setProjectWorkdir: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
}))

import {
  setProjectWorkdir,
  WORKDIR_CONFIRMATION_CODE,
  type EngineEnvironment,
  type WorkdirConfirmation,
} from '@/lib/api'
import { WorkdirConfirmDialog } from '@/components/WorkdirConfirmDialog'
import { WorkdirChooseButton } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const setMock = vi.mocked(setProjectWorkdir)
const en = (key: string, values?: Record<string, unknown>) => t(`engine.${key}`, { ns: 'errors', ...values })

const env = (): EngineEnvironment =>
  ({
    ok: true,
    python: '/usr/bin/python3',
    source: 'system',
    matplotlib: '3.9',
    managed: false,
    bundled: false,
    runtime: {} as never,
    state: 'idle',
    project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'] } },
  }) as never

const option = (mode: WorkdirConfirmation['options'][number]['mode'], found: string[], recommended = false) => ({
  mode,
  cwd_origin: mode === 'project_root' ? 'project.root' : mode === 'project' ? 'script.parent' : 'sandbox',
  write_mode: mode === 'sandbox' ? 'sandboxed' : 'project_dir',
  found,
  recommended,
})

const rootEvidence = (): WorkdirConfirmation => ({
  kind: 'workdir',
  code: WORKDIR_CONFIRMATION_CODE,
  script: 'scripts/entry.py',
  reason: 'project_root_evidence',
  recommended: 'project_root',
  options: [option('project_root', ['data/points.csv'], true), option('project', []), option('sandbox', [])],
  conflicts: [],
  reads: ['data/points.csv'],
})

const ambiguous = (): WorkdirConfirmation => ({
  kind: 'workdir',
  code: WORKDIR_CONFIRMATION_CODE,
  script: 'sub/plot.py',
  reason: 'ambiguous_data',
  recommended: null,
  options: [option('project_root', ['data.csv']), option('project', ['data.csv']), option('sandbox', ['data.csv'])],
  conflicts: ['data.csv'],
  reads: ['data.csv'],
})

//: 用户实报（2026-09-26）的形状：脚本 `glob.glob('run-*-*[Ll]ongrun.traj')` 找同目录的轨迹，
//: 沙盒里找不到——证据指向脚本目录（ADR 0084）；沙盒那档不列探路目标（回退救不回 glob）
const scriptDirEvidence = (): WorkdirConfirmation => ({
  kind: 'workdir',
  code: WORKDIR_CONFIRMATION_CODE,
  script: '长时间 数据/analysis.py',
  reason: 'script_dir_evidence',
  recommended: 'project',
  options: [
    option('project_root', []),
    option('project', ['run-*-*[Ll]ongrun.traj'], true),
    option('sandbox', []),
  ],
  conflicts: [],
  reads: [],
  probes: ['run-*-*[Ll]ongrun.traj'],
})

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
const dialog = () => document.querySelector('[data-dialog="workdir-confirm"]')
const radio = (mode: string) =>
  document.querySelector(`[data-workdir-option="${mode}"] input[type="radio"]`) as HTMLInputElement | null
const button = (label: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label) as
    | HTMLButtonElement
    | undefined

beforeEach(() => {
  setMock.mockReset()
  useUiStore.getState().setConfirm(null)
  useEnvStore.setState({ env: env(), workdirConfirmation: null })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
})

describe('WorkdirConfirmDialog', () => {
  it('没有载荷时什么都不渲染', async () => {
    await render(<WorkdirConfirmDialog />)
    expect(dialog()).toBeNull()
  })

  it('证据唯一指向项目根：三档都列出、推荐项预选、找得到的文件按档显示', async () => {
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(rootEvidence()))
    expect(dialog()).not.toBeNull()
    expect(text()).toContain(en('workdirChooseRootEvidence', { script: 'scripts/entry.py' }))
    expect(radio('project_root')!.checked).toBe(true)
    expect(radio('project')!.checked).toBe(false)
    expect(text()).toContain(en('workdirRecommended'))
    expect(text()).toContain('data/points.csv')
    expect(text()).toContain(en('workdirOptionFoundNone'))
  })

  it('探路调用只在脚本目录找得到：说清原因、预选脚本目录（ADR 0084）', async () => {
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(scriptDirEvidence()))
    expect(text()).toContain(en('workdirChooseScriptDirEvidence', { script: '长时间 数据/analysis.py' }))
    expect(text()).not.toContain(en('workdirChooseRootEvidence', { script: '长时间 数据/analysis.py' }))
    expect(radio('project')!.checked).toBe(true)
    expect(radio('project_root')!.checked).toBe(false)
    expect(text()).toContain('run-*-*[Ll]ongrun.traj')
  })

  it('歧义时不预选（机器不裁决），「运行」在选之前不可点', async () => {
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(ambiguous()))
    expect(text()).toContain(en('workdirChooseAmbiguous', { script: 'sub/plot.py' }))
    expect(text()).toContain(en('workdirChooseConflicts', { files: 'data.csv' }))
    for (const m of ['project_root', 'project', 'sandbox']) expect(radio(m)!.checked).toBe(false)
    expect(text()).not.toContain(en('workdirRecommended'))
    expect(button(en('workdirChooseRun'))!.disabled).toBe(true)
  })

  it('选一档点「运行」：只发一次 PATCH、不弹第二层确认、关框并把「先选目录」的面板重新排上', async () => {
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'project_root', modes: [] },
      project: { open: true, workdir: { mode: 'project_root', modes: ['sandbox', 'project', 'project_root'] } },
    } as never)
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'entry_cwd.pdf', status: 'error', code: WORKDIR_CONFIRMATION_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(rootEvidence()))
    await act(async () => button(en('workdirChooseRun'))!.click())
    await act(async () => {})
    expect(setMock).toHaveBeenCalledTimes(1)
    expect(setMock).toHaveBeenCalledWith('project_root')
    expect(useUiStore.getState().confirm, '不该再弹第二层确认框').toBeNull()
    expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    expect(dialog()).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('project_root')
  })

  it('选「继续沙盒」也发 PATCH：模式没变但决定要记住，下一张图不再问', async () => {
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'sandbox', modes: [] },
      project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'] } },
    } as never)
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(ambiguous()))
    await act(async () => radio('sandbox')!.click())
    await act(async () => button(en('workdirChooseRun'))!.click())
    await act(async () => {})
    expect(setMock).toHaveBeenCalledWith('sandbox')
  })

  it('「稍后」只关框；载荷留在渲染条目上，错误块的按钮能再打开', async () => {
    const payload = rootEvidence()
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'entry_cwd.pdf', status: 'error', code: WORKDIR_CONFIRMATION_CODE,
          confirmation: payload, lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(
      <>
        <WorkdirConfirmDialog />
        <WorkdirChooseButton confirmation={useRenderStore.getState().byKey.k.confirmation} />
      </>,
    )
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(payload))
    await act(async () => button(en('workdirChooseLater'))!.click())
    await act(async () => {})
    expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    expect(setMock).not.toHaveBeenCalled()
    // 「稍后」不碰渲染条目：载荷还在那里，也不把这张图标成 stale（那会让同步器再撞一次门）
    expect(useRenderStore.getState().byKey.k.confirmation).toEqual(payload)
    expect(useRenderStore.getState().byKey.k.stale).toBe(false)
    await act(async () => button(en('workdirChooseButton'))!.click())
    expect(useEnvStore.getState().workdirConfirmation).toEqual(payload)
    expect(dialog()).not.toBeNull()
  })

  it('同一时刻只开一份：后来的载荷不覆盖先到的', async () => {
    await render(<WorkdirConfirmDialog />)
    const first = rootEvidence()
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(first))
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(ambiguous()))
    expect(useEnvStore.getState().workdirConfirmation).toEqual(first)
  })

  it('在途渲染的失败回来时项目已经换了：A 的问题不弹到 B 上（Codex #456 P1）', async () => {
    await render(<WorkdirConfirmDialog />)
    setCurrentProjectId('proj-b')
    try {
      // 渲染是在 A 上发出去的（那一刻的 pj = proj-a），回来时当前项目是 B → 丢
      await act(async () => useEnvStore.getState().requestWorkdirConfirmation(rootEvidence(), 'proj-a'))
      expect(useEnvStore.getState().workdirConfirmation).toBeNull()
      expect(dialog()).toBeNull()
      // 同一个项目的照常弹
      await act(async () => useEnvStore.getState().requestWorkdirConfirmation(rootEvidence(), 'proj-b'))
      expect(dialog()).not.toBeNull()
    } finally {
      setCurrentProjectId(null)
    }
  })

  it('换项目时清掉：A 项目问的问题不能由 B 项目回答', async () => {
    const { fetchEngineEnvironment } = await import('@/lib/api')
    vi.mocked(fetchEngineEnvironment).mockResolvedValue(env())
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(rootEvidence()))
    await act(async () => useEnvStore.getState().resetProject())
    expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    expect(dialog()).toBeNull()
  })

  it('英文界面没有中文泄漏', async () => {
    await i18n.changeLanguage('en-US')
    await render(<WorkdirConfirmDialog />)
    await act(async () => useEnvStore.getState().requestWorkdirConfirmation(ambiguous()))
    expect(text()).not.toMatch(/[一-鿿]/)
    expect(text()).toContain('Project root')
  })
})
