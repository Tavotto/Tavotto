/**
 * 设置页的「安装 Codex 集成」按钮（issue #170 / ADR 0012）。
 *
 * 这里钉的是**关闭条件第 3 条**：假一个失败的 `--json` 输出，界面显示的必须是
 * 翻译过的原因，**不是 `codex_cli_missing` 这串下划线英文**（与 #76 的
 * `unsupported_props` 同一条纪律）。
 *
 * 判据的主语说清楚：量的是**界面上真的出现了什么文字**（`document.body.textContent`），
 * 不是「函数返回了什么」——后者在组件把它扔掉的时候照样绿。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  runCodexIntegration: vi.fn(),
}))

import { CodexShellError } from '@/lib/codexInstall'
import { runCodexIntegration } from '@/lib/desktop'
import { t } from '@/i18n'
import { CodexIntegrationPanel } from './CodexIntegrationPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const runMock = vi.mocked(runCodexIntegration)

const ci = (key: string) => t(`settings.agents.codexInstall.${key}`, { ns: 'dialogs' })

let host: HTMLDivElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<CodexIntegrationPanel />)
  })
}

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byLabel = (label: string) => buttons().find((b) => (b.textContent ?? '').includes(label))!

/** 点「安装 Codex 集成」，等这一轮 async 跑完 */
async function clickInstall() {
  const b = byLabel(ci('action'))
  await act(async () => b.click())
  await act(async () => {})
}

beforeEach(() => {
  runMock.mockReset()
  // isDesktop() 认的就是这个标记（Tauri 2 注入）。**用真的 isDesktop()**，
  // 把它也 mock 掉的话「浏览器模式不渲染」那条就成了自己验自己。
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
})

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
})

/** 引擎失败时那一行 JSON 的形状（`engine/codexinstall.py` 的 `_emit()`） */
const failedJson = (code: string, detail: string) =>
  JSON.stringify({
    ok: false,
    action: 'install',
    steps: [
      { step: 'codex_cli', ok: true, skipped: false, detail: '/usr/local/bin/codex' },
      { step: 'marketplace', ok: false, skipped: false, detail, error_code: code },
    ],
    error_code: code,
    error: detail,
  })

describe('失败按 error_code 翻译，不透传英文 code', () => {
  // 引擎的五个 `ERR_*`（齐全性由 tests/test_desktop_codex_button.py 从引擎枚举着比；
  // 这里比的是**界面上显示的是哪一句**）
  const CODES = [
    'codex_cli_missing',
    'marketplace_add_failed',
    'plugin_add_failed',
    'provision_failed',
    'health_failed',
  ]

  for (const code of CODES) {
    it(`${code}：显示翻译过的原因，界面上不出现 code 本身`, async () => {
      runMock.mockResolvedValue(failedJson(code, 'raw output from codex'))
      await mount()
      await clickInstall()

      expect(text()).toContain(ci(`error.${code}`))
      expect(text()).not.toContain(code)
      // 兜底那句也不该出现——出现了说明它落到了 `other`，等于这条 code 没文案
      expect(text()).not.toContain(ci('error.other'))
    })
  }

  it('codex_cli_missing 要把「找过哪些位置」显示出来', async () => {
    const searched = '找不到 codex 命令。找过：PATH、/opt/homebrew/bin'
    runMock.mockResolvedValue(failedJson('codex_cli_missing', searched))
    await mount()
    await clickInstall()

    expect(text()).toContain(searched)
    expect(text()).not.toContain('codex_cli_missing')
  })

  it('认不出的 code（老界面 + 新引擎）落到兜底句，仍然不甩英文 code', async () => {
    runMock.mockResolvedValue(failedJson('brand_new_failure', 'whatever'))
    await mount()
    await clickInstall()

    expect(text()).toContain(ci('error.other'))
    expect(text()).not.toContain('brand_new_failure')
  })

  it('连那行 JSON 都没拿到（壳侧失败）也显示成人话', async () => {
    runMock.mockRejectedValue(new CodexShellError('cli_not_found'))
    await mount()
    await clickInstall()

    expect(text()).toContain(ci('error.cli_not_found'))
    expect(text()).not.toContain('cli_not_found')
  })

  it('输出不是 JSON / 形状不对时报「没拿到结果」，不假装装好了', async () => {
    runMock.mockResolvedValue('Traceback (most recent call last):')
    await mount()
    await clickInstall()

    expect(text()).toContain(ci('error.bad_output'))
    expect(text()).not.toContain(ci('doneNewSession'))
  })
})

describe('成功路径', () => {
  const okJson = JSON.stringify({
    ok: true,
    action: 'install',
    steps: [
      { step: 'codex_cli', ok: true, skipped: false, detail: '/usr/local/bin/codex' },
      { step: 'marketplace', ok: true, skipped: true, detail: '已登记' },
      { step: 'plugin', ok: true, skipped: false, detail: '已安装' },
    ],
  })

  it('逐步显示名字 + 状态，「跳过」是独立一档', async () => {
    runMock.mockResolvedValue(okJson)
    await mount()
    await clickInstall()

    expect(text()).toContain(ci('step.marketplace'))
    expect(text()).toContain(ci('step.plugin'))
    expect(text()).toContain(ci('stepState.skipped'))
    expect(text()).toContain(ci('stepState.done'))
  })

  it('收尾只说「新开一个 Codex 会话」，不说「已启用」', async () => {
    runMock.mockResolvedValue(okJson)
    await mount()
    await clickInstall()

    expect(text()).toContain(ci('doneNewSession'))
    expect(runMock).toHaveBeenCalledWith('install')
  })
})

describe('重新诊断', () => {
  it('走的是 doctor（只诊断不改动），不是 install', async () => {
    runMock.mockResolvedValue(
      JSON.stringify({ ok: true, action: 'doctor', steps: [{ step: 'health', ok: true, skipped: false }] }),
    )
    await mount()
    const b = byLabel(ci('doctor'))
    await act(async () => b.click())
    await act(async () => {})

    expect(runMock).toHaveBeenCalledWith('doctor')
    expect(runMock).not.toHaveBeenCalledWith('install')
    expect(text()).toContain(ci('healthy'))
    // doctor 不改动任何东西，所以绝不该说「装好了」
    expect(text()).not.toContain(ci('doneNewSession'))
  })
})

describe('浏览器模式', () => {
  it('没有壳就没有这个入口（不画一个按不动的按钮）', async () => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    await mount()
    expect(buttons()).toHaveLength(0)
    expect(text()).not.toContain(ci('action'))
  })
})
