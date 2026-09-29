/**
 * 数据找不到时请用户指认一次（ADR 0106）。判据的主语：后端给的 `missing_input` 载荷在界面上怎么
 * 变成一次指认——
 * ① 以 worker 说出来的那串为主、如实列出其余也找不到的；② 桌面上「找到这个文件…」/「选择所在
 *   文件夹…」各发一次请求（带脚本写的原串与用户选的位置），成功后关框并把「找不到数据」的面板重排；
 *   取消选择器什么都不发；③ 失败留在框里说原因；④ 探路（exists / glob）与 C++ 读取器救不回来：不记改指，
 *   出口是经确认改写脚本（ADR 0110）——选位置 → 后端给的逐行 diff → 勾选才能「修改脚本」→ 提交后关框重排；
 *   一处都改不了时列出逐条原因；提交失败回到选位置那一步说原因；
 * ⑤ 浏览器模式拿不到本机路径：粘贴路径、按 `auto` 发；⑥ 「稍后」之后错误块里能再打开；
 * ⑦ 设置里的改写备份：此刻的状态决定给哪个复原按钮（状态是后端现算的，前端只翻译）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  addInputRemap: vi.fn(),
  removeInputRemap: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
  previewInputPathEdit: vi.fn(),
  commitScriptEdit: vi.fn(),
  listScriptBackups: vi.fn(),
  restoreScriptBackup: vi.fn(),
}))
vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  isDesktop: vi.fn(() => true),
  pickAnyFile: vi.fn(),
  pickDirectory: vi.fn(),
}))

import {
  addInputRemap,
  ApiError,
  commitScriptEdit,
  fetchEngineEnvironment,
  listScriptBackups,
  MISSING_INPUT_CODE,
  previewInputPathEdit,
  restoreScriptBackup,
  type EngineEnvironment,
  type MissingInputOffer,
  type ScriptBackup,
  type ScriptEditPreview,
} from '@/lib/api'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { MissingInputDialog } from '@/components/MissingInputDialog'
import { InputRemapRows, MissingInputButton, ScriptBackupRows } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const addMock = vi.mocked(addInputRemap)
const previewMock = vi.mocked(previewInputPathEdit)
const commitMock = vi.mocked(commitScriptEdit)
const listMock = vi.mocked(listScriptBackups)
const restoreMock = vi.mocked(restoreScriptBackup)
const fileMock = vi.mocked(pickAnyFile)
const dirMock = vi.mocked(pickDirectory)
const desktopMock = vi.mocked(isDesktop)
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
    project: {
      open: true,
      workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'] },
      input_remap: { rules: [], generation: 1 },
    },
  }) as never

const relative = (): MissingInputOffer => ({
  script: 'fig.py',
  requested: 'data/values.txt',
  absolute: false,
  via: 'open',
  others: [{ path: '/Users/a/raw/extra.csv', absolute: true, via: 'open' }],
})

const probeOnly = (): MissingInputOffer => ({
  script: 'fig.py',
  requested: null,
  absolute: false,
  via: 'open',
  others: [{ path: 'run-*.traj', absolute: false, via: 'glob' }],
})

const native = (): MissingInputOffer => ({
  script: 'fig.py',
  requested: '/Users/a/proj/run/x.h5',
  absolute: true,
  via: 'native',
  others: [],
})

const preview = (): ScriptEditPreview => ({
  ok: true,
  token: 'tok-1',
  script: 'fig.py',
  script_abs: '/Volumes/Work/proj/fig.py',
  rows: [{ line: 3, before: 'f = h5py.File("/Users/a/proj/run/x.h5")', after: 'f = h5py.File("/Volumes/B/run/x.h5")' }],
  edits: [
    {
      line: 3,
      before: '"/Users/a/proj/run/x.h5"',
      after: '"/Volumes/B/run/x.h5"',
      value_before: '/Users/a/proj/run/x.h5',
      value_after: '/Volumes/B/run/x.h5',
    },
  ],
  skipped: [{ line: 7, value: '/Users/a/proj/run/', reason: 'fstring' }],
  encoding: 'utf-8',
  backups: { project: '/Volumes/Work/proj/tavottofile/script-backups/fig.py', mirror: '/data/script_backups/p/fig.py' },
  git: { tracked: true, dirty: false },
  checksums: ['SHA256SUMS'],
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
const dialog = () => document.querySelector('[data-dialog="missing-input"]')
const byTestId = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement | null

beforeEach(() => {
  addMock.mockReset()
  previewMock.mockReset()
  commitMock.mockReset()
  listMock.mockReset()
  restoreMock.mockReset()
  fileMock.mockReset()
  dirMock.mockReset()
  desktopMock.mockReturnValue(true)
  // 作废之后会刷新一次环境：这里不关心它的内容，失败就是保持原样
  vi.mocked(fetchEngineEnvironment).mockRejectedValue(new Error('offline'))
  useEnvStore.setState({
    env: env(),
    missingInput: null,
    inputRemapSeen: null,
    rewritePreview: null,
    rewriteSkipped: [],
    rewriteError: null,
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
})

describe('MissingInputDialog', () => {
  it('没有载荷时什么都不渲染', async () => {
    await render(<MissingInputDialog />)
    expect(dialog()).toBeNull()
  })

  it('默认只有一句话 + 一个主按钮 +「稍后」；完整路径、原因、其余路径、说明与改选文件夹都在折叠的「详情」里', async () => {
    // 用户 09-29：「像这样一个卡片太冗杂了……一定要让用户一句话就能够读懂」
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    expect(dialog()).not.toBeNull()
    const details = dialog()!.querySelector('details[data-missing-input-details]') as HTMLDetailsElement
    expect(details.open, '详情默认折叠').toBe(false)
    expect(details.querySelector('summary')!.textContent).toBe(en('missingInputDetails'))
    // 折叠区之外看得见的：标题、那一句话（只有文件名）、两个按钮
    const outside = (el: Element) => !el.closest('details')
    const visibleText = [...dialog()!.querySelectorAll('h2, p')].filter(outside).map((e) => e.textContent)
    expect(visibleText).toEqual([en('missingInputTitle'), en('missingInputSentence', { name: 'values.txt' })])
    expect(visibleText.join('')).not.toContain('data/values.txt')
    const buttons = [...dialog()!.parentElement!.querySelectorAll('button')]
      .filter(outside)
      .map((b) => b.textContent?.trim())
      .filter(Boolean)
    expect(buttons).toEqual([en('missingInputLater'), en('missingInputPickFile')])
    // 折叠区里：完整路径、为什么、其余路径、只影响读取、改选文件夹
    expect(details.querySelector('[data-missing-input-path]')!.textContent).toBe('data/values.txt')
    expect(details.textContent).toContain(en('missingInputWhyRelative'))
    expect(details.querySelector('[data-missing-input-others]')!.textContent).toContain('/Users/a/raw/extra.csv')
    expect(details.textContent).toContain(en('missingInputReadOnly'))
    expect(details.querySelector('[data-testid="missing-input-pick-dir"]')).not.toBeNull()
  })

  it('指认文件：发一次（原串 + 选中的位置 + file），关框、把「找不到数据」的面板重新排上', async () => {
    fileMock.mockResolvedValue('/Volumes/B/proj/data/values.txt')
    addMock.mockResolvedValue({
      ok: true,
      rule: { kind: 'prefix', from: '', to: '/Volumes/B/proj' },
      input_remap: { rules: [{ kind: 'prefix', from: '', to: '/Volumes/B/proj', target_exists: true }], generation: 2 },
    })
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'values.png', status: 'error', code: MISSING_INPUT_CODE,
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    await act(async () => byTestId('missing-input-pick-file')!.click())
    await act(async () => {})
    expect(addMock).toHaveBeenCalledTimes(1)
    expect(addMock).toHaveBeenCalledWith('data/values.txt', '/Volumes/B/proj/data/values.txt', 'file')
    expect(useEnvStore.getState().missingInput).toBeNull()
    expect(dialog()).toBeNull()
    // 发起的窗口按响应带回的代次本地重排，不等事件（ADR 0106 §五）。
    // 重排在两个动态 import 之后：首次加载模块是真异步，不止一个微任务
    expect(useEnvStore.getState().env?.project?.input_remap?.rules).toHaveLength(1)
    await vi.waitFor(() => expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true))
  })

  it('指认文件夹按 dir 发；取消选择器什么都不发', async () => {
    dirMock.mockResolvedValueOnce(null).mockResolvedValueOnce('/Volumes/B/proj')
    addMock.mockResolvedValue({ ok: true, rule: {} as never, input_remap: { rules: [], generation: 2 } })
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    await act(async () => byTestId('missing-input-pick-dir')!.click())
    await act(async () => {})
    expect(addMock).not.toHaveBeenCalled()
    expect(dialog()).not.toBeNull()
    await act(async () => byTestId('missing-input-pick-dir')!.click())
    await act(async () => {})
    expect(addMock).toHaveBeenCalledWith('data/values.txt', '/Volumes/B/proj', 'dir')
  })

  it('后端推不出规则：框留着，说出本地化的原因', async () => {
    dirMock.mockResolvedValue('/Volumes/B')
    addMock.mockRejectedValue(
      new ApiError('x', 400, {
        code: 'input_remap_not_found_in_dir',
        params: { name: 'values.txt', path: '/Volumes/B' },
      }),
    )
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    await act(async () => byTestId('missing-input-pick-dir')!.click())
    await act(async () => {})
    expect(dialog()).not.toBeNull()
    expect(text()).toContain(
      t('backend.input_remap_not_found_in_dir', { ns: 'errors', name: 'values.txt', path: '/Volumes/B' }),
    )
  })

  it('只剩探路（glob）：不记改指，只给「选择所在文件夹」，选了之后去预览改写', async () => {
    dirMock.mockResolvedValue('/Volumes/B/runs')
    previewMock.mockResolvedValue(preview())
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(probeOnly()))
    expect(document.querySelector('[data-missing-input-path]')!.textContent).toBe('run-*.traj')
    expect(text()).toContain(en('missingInputProbe'))
    expect(text()).toContain(en('missingInputRewriteHint'))
    expect(byTestId('missing-input-pick-file'), 'glob 没有「那个文件」可找').toBeNull()
    expect(text()).not.toContain(en('missingInputReadOnly'))
    await act(async () => byTestId('missing-input-pick-dir')!.click())
    await act(async () => {})
    expect(addMock, '探路救不回来，不许记改指').not.toHaveBeenCalled()
    expect(previewMock).toHaveBeenCalledWith('fig.py', 'run-*.traj', '/Volumes/B/runs', 'dir')
    expect(document.querySelector('[data-dialog="missing-input-rewrite"]')).not.toBeNull()
  })

  it('只接受文件夹的探路（listdir / iterdir）：只给「选择所在文件夹」；问文件的探路照旧能选文件', async () => {
    // Codex 评 #730 P2：`via` 都是 probe，要不要文件夹看后端给的 `probe_kind`
    const listdir = (): MissingInputOffer => ({
      script: 'fig.py',
      requested: null,
      absolute: true,
      via: 'open',
      others: [{ path: '/Users/a/proj/runs.v1', absolute: true, via: 'probe', probe_kind: 'dir' }],
    })
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(listdir()))
    expect(byTestId('missing-input-pick-dir')).not.toBeNull()
    expect(byTestId('missing-input-pick-file'), 'listdir 要的是文件夹').toBeNull()
    await act(async () => useEnvStore.getState().dismissMissingInput())
    const isfile: MissingInputOffer = {
      ...listdir(),
      others: [{ path: '/Users/a/proj/a.csv', absolute: true, via: 'probe', probe_kind: 'file' }],
    }
    await act(async () => useEnvStore.getState().requestMissingInput(isfile))
    expect(byTestId('missing-input-pick-file'), 'isfile 问的是文件').not.toBeNull()
    await act(async () => useEnvStore.getState().dismissMissingInput())
    // worker 说出来的那一串（顶层）也一样
    const requestedDir: MissingInputOffer = { ...listdir(), requested: '/Users/a/proj/runs.v1', via: 'probe', probe_kind: 'dir', others: [] }
    await act(async () => useEnvStore.getState().requestMissingInput(requestedDir))
    expect(byTestId('missing-input-pick-file')).toBeNull()
  })

  it('C++ 读取器：说清是读取器直接打开的；确认页只渲染后端给的行，勾选之后才能「修改脚本」', async () => {
    fileMock.mockResolvedValue('/Volumes/B/run/x.h5')
    previewMock.mockResolvedValue(preview())
    commitMock.mockResolvedValue({ ok: true, script: 'fig.py', backup: {} as never })
    useRenderStore.setState({
      byKey: {
        k: {
          fileId: 'fig.png', status: 'error', code: 'script_error', missingInput: native(),
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    const gen = useEnvStore.getState().inputRemapGeneration
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(native()))
    expect(text()).toContain(en('missingInputNative'))
    await act(async () => byTestId('missing-input-pick-file')!.click())
    await act(async () => {})
    expect(previewMock).toHaveBeenCalledWith('fig.py', '/Users/a/proj/run/x.h5', '/Volumes/B/run/x.h5', 'file')
    const confirmPage = document.querySelector('[data-dialog="missing-input-rewrite"]')!
    expect(confirmPage.querySelector('[data-rewrite-warning]')!.textContent).toContain('/Volumes/Work/proj/fig.py')
    const rows = confirmPage.querySelector('[data-rewrite-rows]')!.textContent!
    expect(rows).toContain('/Users/a/proj/run/x.h5')
    expect(rows).toContain('/Volumes/B/run/x.h5')
    expect(confirmPage.querySelector('[data-rewrite-skipped]')!.textContent).toContain(en('rewriteSkip.fstring'))
    expect(text()).toContain(en('rewriteChecksums', { files: 'SHA256SUMS' }))
    expect(text()).toContain(en('rewriteGitClean'))
    const apply = byTestId('missing-input-rewrite-apply')!
    expect(apply.disabled, '没勾选不能改').toBe(true)
    expect(document.activeElement === apply, '「修改脚本」不能是默认焦点').toBe(false)
    await act(async () => byTestId('missing-input-rewrite-confirm')!.click())
    expect(apply.disabled).toBe(false)
    await act(async () => apply.click())
    await act(async () => {})
    expect(commitMock).toHaveBeenCalledWith('tok-1')
    expect(dialog()).toBeNull()
    expect(document.querySelector('[data-dialog="missing-input-rewrite"]')).toBeNull()
    expect(useRenderStore.getState().byKey.k.stale, 'C++ 读取器那条 script_error 没重新排上').toBe(true)
    expect(useEnvStore.getState().inputRemapGeneration).toBe(gen + 1)
  })

  it('「返回」丢掉这份预览、不提交', async () => {
    fileMock.mockResolvedValue('/Volumes/B/run/x.h5')
    previewMock.mockResolvedValue(preview())
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(native()))
    await act(async () => byTestId('missing-input-pick-file')!.click())
    await act(async () => {})
    const back = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === en('rewriteBack'),
    )!
    await act(async () => back.click())
    expect(document.querySelector('[data-dialog="missing-input-rewrite"]')).toBeNull()
    expect(dialog()).not.toBeNull()
    expect(commitMock).not.toHaveBeenCalled()
  })

  it('一处都改不了：留在框里，逐条说出为什么', async () => {
    fileMock.mockResolvedValue('/Volumes/B/run/x.h5')
    previewMock.mockRejectedValue(
      new ApiError('x', 409, {
        code: 'script_edit_nothing_to_change',
        params: { skipped: [{ line: 4, value: '/Users/a/proj/run/', reason: 'fstring' }] },
      }),
    )
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(native()))
    await act(async () => byTestId('missing-input-pick-file')!.click())
    await act(async () => {})
    expect(dialog()).not.toBeNull()
    expect(text()).toContain(t('backend.script_edit_nothing_to_change', { ns: 'errors' }))
    expect(document.querySelector('[data-rewrite-skipped]')!.textContent).toContain(en('rewriteSkip.fstring'))
  })

  it('提交失败（预览之后脚本被改过）：回到选位置那一步说原因', async () => {
    fileMock.mockResolvedValue('/Volumes/B/run/x.h5')
    previewMock.mockResolvedValue(preview())
    commitMock.mockRejectedValue(
      new ApiError('x', 409, { code: 'script_changed_since_preview', params: { script: 'fig.py' } }),
    )
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(native()))
    await act(async () => byTestId('missing-input-pick-file')!.click())
    await act(async () => {})
    await act(async () => byTestId('missing-input-rewrite-confirm')!.click())
    await act(async () => byTestId('missing-input-rewrite-apply')!.click())
    await act(async () => {})
    expect(document.querySelector('[data-dialog="missing-input-rewrite"]')).toBeNull()
    expect(dialog()).not.toBeNull()
    expect(text()).toContain(t('backend.script_changed_since_preview', { ns: 'errors', script: 'fig.py' }))
  })

  it('浏览器模式：粘贴路径后才可点，按 auto 发', async () => {
    desktopMock.mockReturnValue(false)
    addMock.mockResolvedValue({ ok: true, rule: {} as never, input_remap: { rules: [], generation: 2 } })
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    expect(byTestId('missing-input-pick-file')).toBeNull()
    const use = byTestId('missing-input-use-path')!
    expect(use.disabled).toBe(true)
    const input = document.querySelector('[data-dialog="missing-input"] input') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, '  /srv/data  ')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(use.disabled).toBe(false)
    await act(async () => use.click())
    await act(async () => {})
    expect(addMock).toHaveBeenCalledWith('data/values.txt', '/srv/data', 'auto')
  })

  it('「稍后」只关框；错误块里的按钮把它再打开', async () => {
    await render(
      <>
        <MissingInputDialog />
        <MissingInputButton offer={relative()} />
      </>,
    )
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    const later = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === en('missingInputLater'),
    )!
    await act(async () => later.click())
    expect(dialog()).toBeNull()
    await act(async () => byTestId('missing-input-open')!.click())
    expect(dialog()).not.toBeNull()
  })

  it('换项目清掉载荷', async () => {
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    await act(async () => useEnvStore.getState().resetProject())
    expect(useEnvStore.getState().missingInput).toBeNull()
  })

  it('设置里的改写备份：此刻的状态决定给哪个复原按钮', async () => {
    const backups: ScriptBackup[] = [
      { id: 'fig.py/0929_1', kind: 'input_path', script: 'fig.py', created: 1, pristine: true, state: 'current' },
      { id: 'b.py/0929_2', kind: 'input_path', script: 'b.py', created: 2, pristine: true, state: 'changed', current_sha256: 'b-now' },
      { id: 'c.py/0929_3', kind: 'input_path', script: 'c.py', created: 3, pristine: true, state: 'before' },
      { id: 'c.py/0929_4', kind: 'restore', script: 'c.py', created: 4, pristine: false, state: 'current' },
    ]
    listMock.mockResolvedValue({ ok: true, backups })
    restoreMock.mockResolvedValue({ ok: true, script: 'b.py' })
    await render(<ScriptBackupRows />)
    await act(async () => {})
    const row = (id: string) => document.querySelector(`[data-script-backup="${id}"]`)!
    const buttons = (id: string) => [...row(id).querySelectorAll('button')].map((b) => b.textContent?.trim())
    expect(buttons('fig.py/0929_1')).toEqual([en('scriptBackupRestore')])
    expect(buttons('b.py/0929_2')).toEqual([en('scriptBackupUndoEdits'), en('scriptBackupRestoreFull')])
    expect(buttons('c.py/0929_3')).toEqual([])
    expect(document.querySelector('[data-script-backup="c.py/0929_4"]'), '复原前的快照不列').toBeNull()
    await act(async () => (row('b.py/0929_2').querySelector('button') as HTMLButtonElement).click())
    await act(async () => {})
    // 界面按哪一版给的按钮原样带回，后端锁里核对（Codex 评 #730 P1）
    expect(restoreMock).toHaveBeenCalledWith('b.py/0929_2', 'undo_edits', 'b-now')
    expect(listMock, '复原之后重读列表').toHaveBeenCalledTimes(2)
  })

  it('英文界面：对话框与设置行没有中文', async () => {
    await i18n.changeLanguage('en-US')
    useEnvStore.setState({
      env: {
        ...env(),
        project: {
          ...env().project!,
          input_remap: { rules: [{ kind: 'prefix', from: '', to: '/Volumes/B', target_exists: false }], generation: 1 },
        },
      } as never,
    })
    await render(
      <>
        <MissingInputDialog />
        <InputRemapRows />
      </>,
    )
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    expect(text()).toContain(en('inputRemapTargetGone'))
    expect(text()).not.toMatch(/[一-鿿]/)
    // 确认页（路径与代码行是用户自己的，本来就不含中文）
    await act(async () => useEnvStore.getState().dismissMissingInput())
    await act(async () => useEnvStore.getState().requestMissingInput(native()))
    await act(async () => useEnvStore.setState({ rewritePreview: preview() }))
    expect(document.querySelector('[data-dialog="missing-input-rewrite"]')).not.toBeNull()
    expect(text()).toContain(en('rewriteConfirm'))
    expect(text()).not.toMatch(/[一-鿿]/)
  })
})
