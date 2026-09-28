/**
 * 数据找不到时请用户指认一次（ADR 0106）。判据的主语：后端给的 `missing_input` 载荷在界面上怎么
 * 变成一次指认——
 * ① 以 worker 说出来的那串为主、如实列出其余也找不到的；② 桌面上「找到这个文件…」/「选择所在
 *   文件夹…」各发一次请求（带脚本写的原串与用户选的位置），成功后关框并把「找不到数据」的面板重排；
 *   取消选择器什么都不发；③ 失败留在框里说原因；④ 探路（exists / glob）救不回来时不给按钮、说清怎么办；
 * ⑤ 浏览器模式拿不到本机路径：粘贴路径、按 `auto` 发；⑥ 「稍后」之后错误块里能再打开。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  addInputRemap: vi.fn(),
  removeInputRemap: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
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
  MISSING_INPUT_CODE,
  type EngineEnvironment,
  type MissingInputOffer,
} from '@/lib/api'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { MissingInputDialog } from '@/components/MissingInputDialog'
import { InputRemapRows, MissingInputButton } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const addMock = vi.mocked(addInputRemap)
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
      input_remap: { rules: [] },
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
  fileMock.mockReset()
  dirMock.mockReset()
  desktopMock.mockReturnValue(true)
  useEnvStore.setState({ env: env(), missingInput: null })
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

  it('以脚本要的那串为主，说清是相对路径，列出其余也找不到的', async () => {
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(relative()))
    expect(dialog()).not.toBeNull()
    expect(document.querySelector('[data-missing-input-path]')!.textContent).toBe('data/values.txt')
    expect(text()).toContain(en('missingInputBody', { script: 'fig.py' }))
    expect(text()).toContain(en('missingInputWhyRelative'))
    expect(document.querySelector('[data-missing-input-others]')!.textContent).toContain('/Users/a/raw/extra.csv')
    expect(text()).toContain(en('missingInputReadOnly'))
  })

  it('指认文件：发一次（原串 + 选中的位置 + file），关框、把「找不到数据」的面板重新排上', async () => {
    fileMock.mockResolvedValue('/Volumes/B/proj/data/values.txt')
    addMock.mockResolvedValue({
      ok: true,
      rule: { kind: 'prefix', from: '', to: '/Volumes/B/proj' },
      input_remap: { rules: [{ kind: 'prefix', from: '', to: '/Volumes/B/proj', target_exists: true }] },
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
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
    expect(useEnvStore.getState().env?.project?.input_remap?.rules).toHaveLength(1)
  })

  it('指认文件夹按 dir 发；取消选择器什么都不发', async () => {
    dirMock.mockResolvedValueOnce(null).mockResolvedValueOnce('/Volumes/B/proj')
    addMock.mockResolvedValue({ ok: true, rule: {} as never, input_remap: { rules: [] } })
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

  it('只剩探路（glob）救不回来：说清怎么办，不给点了也没用的按钮', async () => {
    await render(<MissingInputDialog />)
    await act(async () => useEnvStore.getState().requestMissingInput(probeOnly()))
    expect(document.querySelector('[data-missing-input-path]')!.textContent).toBe('run-*.traj')
    expect(text()).toContain(en('missingInputProbe'))
    expect(byTestId('missing-input-pick-file')).toBeNull()
    expect(byTestId('missing-input-pick-dir')).toBeNull()
    expect(text()).not.toContain(en('missingInputReadOnly'))
  })

  it('浏览器模式：粘贴路径后才可点，按 auto 发', async () => {
    desktopMock.mockReturnValue(false)
    addMock.mockResolvedValue({ ok: true, rule: {} as never, input_remap: { rules: [] } })
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

  it('英文界面：对话框与设置行没有中文', async () => {
    await i18n.changeLanguage('en-US')
    useEnvStore.setState({
      env: {
        ...env(),
        project: {
          ...env().project!,
          input_remap: { rules: [{ kind: 'prefix', from: '', to: '/Volumes/B', target_exists: false }] },
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
  })
})
