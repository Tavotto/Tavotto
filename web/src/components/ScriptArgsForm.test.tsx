/**
 * 参数表单（T07）：真实 schema（后端快照，`tests/golden/script_args_form_vectors.json`）→ 在界面里填写 →
 * 草稿里的 token 等于向量里的 `after`（后端用同一份 `after` 跑过真 argparse）。表单只改 token，不另存意图。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import golden from '../../../tests/golden/script_args_form_vectors.json'
import type { ScriptArgsSchema } from '@/lib/scriptArgsForm'
import { ScriptArgvEditor } from '@/components/ScriptArgvEditor'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useScriptArgvStore } from '@/store/scriptArgvStore'

const fetchScriptArguments = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchScriptArguments: (...a: unknown[]) => fetchScriptArguments(...a),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const schemas = golden.schemas as unknown as Record<string, ScriptArgsSchema>
let host: HTMLDivElement
let root: Root

const tokens = () => useScriptArgvStore.getState().drafts['plot.py']?.tokens ?? []

const open = async (schema: ScriptArgsSchema) => {
  fetchScriptArguments.mockResolvedValue({ ok: true, script: 'plot.py', arguments: schema })
  act(() => {
    root.render(
      <TooltipProvider>
        <ScriptArgvEditor script="plot.py" />
      </TooltipProvider>,
    )
  })
  const details = host.querySelector('details')!
  await act(async () => {
    details.open = true
    details.dispatchEvent(new Event('toggle'))
  })
}

const field = (dest: string) => host.querySelector<HTMLElement>(`[data-testid="argv-field-${dest}"]`)!
const typeInto = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const textOf = (dest: string) => field(dest).querySelector<HTMLInputElement>('input[type="text"], input[type="password"]')!

beforeEach(() => {
  useScriptArgvStore.getState().clear()
  fetchScriptArguments.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('ScriptArgsForm', () => {
  it('A01：六个必填逐项填写 → token 等于向量（后端已用真 argparse 验过这串 token）', async () => {
    await open(schemas.fft6)
    expect(fetchScriptArguments).toHaveBeenCalledWith('plot.py')
    expect(host.querySelector('[data-testid="argv-form-missing"]')).not.toBeNull()
    const fill: [string, string][] = [
      ['freq', '3'],
      ['amp', '2.5'],
      ['phase', '0.25'],
      ['n', '16'],
      ['tag', 'T'],
    ]
    for (const [dest, v] of fill) typeInto(textOf(dest), v)
    // choices → 下拉；直接走 store 同一条编辑（Radix Select 在 jsdom 里不弹层）
    const fft6 = golden.cases.find((c) => c.name === 'fft6_fill_all')!
    expect(tokens()).toEqual(fft6.after.slice(0, 10))
    // 只剩 --mode（choices → 下拉；Radix Select 在 jsdom 里不弹层，它那一步的 token 由 golden 用例钉）
    expect(host.querySelector('[data-testid="argv-form-missing"]')!.textContent).toMatch(/--mode\b/)
    expect(host.querySelector('[data-testid="argv-form-missing"]')!.textContent).not.toMatch(/--freq/)
  })

  it('默认值只是占位符：读一遍不写 token；删光输入 = 空字符串（一个值），× = 不提供', async () => {
    useScriptArgvStore.setState({ drafts: { 'plot.py': { tokens: ['in.csv', '3'], sensitive: false } } })
    await open(schemas.mixed)
    expect(tokens()).toEqual(['in.csv', '3'])
    const scale = textOf('scale')
    expect(scale.placeholder).toMatch(/1\.0/)
    expect(scale.value).toBe('')
    typeInto(textOf('k_value'), 'a')
    typeInto(textOf('k_value'), '')
    expect(tokens()).toEqual(['in.csv', '3', '--k-value', ''])
    act(() => field('k_value').querySelector<HTMLButtonElement>('button')!.click())
    expect(tokens()).toEqual(['in.csv', '3'])
  })

  it('不认识的 token 原样保留且列出来；切换视图（表单 ↔ 参数列表）不重建', async () => {
    useScriptArgvStore.setState({
      drafts: { 'plot.py': { tokens: ['--alpha', '1', '--known', 'a'], sensitive: false } },
    })
    await open(schemas.partial)
    expect(host.querySelector('[data-testid="argv-form-status"]')!.textContent).toMatch(
      /部分|partly/,
    )
    expect(host.querySelector('[data-testid="argv-form-other"]')!.textContent).toContain('"--alpha"')
    typeInto(textOf('known'), 'b')
    expect(tokens()).toEqual(['--alpha', '1', '--known', 'b'])
    // 参数列表里看到的就是同一串（同一个 store 草稿）
    const list = [...host.querySelectorAll<HTMLInputElement>('li input')].filter((i) =>
      i.getAttribute('aria-label')?.match(/个参数|Argument/),
    )
    expect(list.map((i) => i.value)).toEqual(['--alpha', '1', '--known', 'b'])
  })

  it('就地错误：编辑被拒时输入框保留原值与焦点，token 不变，并说出原因', async () => {
    await open(schemas.mixed)
    const src = textOf('src')
    src.focus()
    typeInto(src, '-weird')
    expect(tokens()).toEqual([])
    expect(textOf('src').value).toBe('-weird')
    expect(document.activeElement).toBe(textOf('src'))
    expect(field('src').querySelector('[data-testid="argv-field-error"]')).not.toBeNull()
  })

  it('子命令脚本：表单只读，参数只在列表里填', async () => {
    await open(schemas.subcmd)
    expect(host.querySelector('[data-testid="argv-form-readonly"]')).not.toBeNull()
    expect(field('g').querySelector('[data-testid="argv-field-readonly"]')).not.toBeNull()
  })

  it('输出文件参数说明写入位置，且不会出现任何覆盖开关', async () => {
    await open(schemas.mixed)
    expect(field('out').textContent).toMatch(/写这个文件|writes this file/)
    expect(tokens().some((t) => /overwrite|force/.test(t))).toBe(false)
  })

  it('敏感：表单输入框是密码框，"其他参数"里不显示原文', async () => {
    useScriptArgvStore.setState({
      drafts: { 'plot.py': { tokens: ['--alpha', 'S3CRET', '--known', 'x'], sensitive: true } },
    })
    await open(schemas.partial)
    expect(textOf('known').type).toBe('password')
    expect(host.querySelector('[data-testid="argv-form-other"]')!.textContent).not.toContain('S3CRET')
  })

  it('粘贴命令：简单调用拆成 token；带管道的拒绝、原文留在框里', async () => {
    await open(schemas.mixed)
    const paste = host.querySelector<HTMLInputElement>('[data-testid="argv-paste-plot.py"] input')!
    typeInto(paste, 'python plot.py "数据/样本 1.csv" 7 --scale 2')
    act(() =>
      host.querySelector<HTMLButtonElement>('[data-testid="argv-paste-plot.py"] button')!.click(),
    )
    expect(tokens()).toEqual(['数据/样本 1.csv', '7', '--scale', '2'])
    typeInto(paste, 'python plot.py | tee log')
    act(() =>
      host.querySelector<HTMLButtonElement>('[data-testid="argv-paste-plot.py"] button')!.click(),
    )
    expect(tokens()).toEqual(['数据/样本 1.csv', '7', '--scale', '2'])
    expect(host.querySelector('[data-testid="argv-paste-error"]')).not.toBeNull()
    expect(paste.value).toBe('python plot.py | tee log')
  })

  it('schema 取不到：只有参数列表（表单是便利，不是门槛）', async () => {
    fetchScriptArguments.mockRejectedValue(new Error('offline'))
    act(() => {
      root.render(
        <TooltipProvider>
          <ScriptArgvEditor script="plot.py" />
        </TooltipProvider>,
      )
    })
    const details = host.querySelector('details')!
    await act(async () => {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    })
    expect(host.querySelector('[data-testid="argv-form-plot.py"]')).toBeNull()
  })
})
