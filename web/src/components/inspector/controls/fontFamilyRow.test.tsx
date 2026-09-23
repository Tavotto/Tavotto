/**
 * 字体下拉不跟着属性页重画（2026-09-23 性能剖析）。
 *
 * 判据的主语：**Select 这一层有没有被重新渲染**——它下面挂着本机字体并表的几百个
 * 选项，Radix 收起时也会全部重建。Select 被换成一个只计数、记下 props 的假组件：
 * 真的 Radix 在 jsdom 里既量不到开销、也难以操作，而这里要回答的只是「重画了没有」
 * 与「点下去调的是谁的 onChange」。
 *
 * 反证（提交前手工跑过）：去掉 memo → 第一条红；把转发器换回直接传 props.onChange →
 * 「换元素」那条红（字体写到了上一个元素上）；比较器漏掉 options → 「选项变了」那条红。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '@/i18n'
import { TooltipProvider } from '../../ui/Tooltip'

const calls: { onChange: (v: string) => void; labels: string[] }[] = []

vi.mock('../../ui/Select', () => ({
  Select: (p: { onChange: (v: string) => void; options: { label: unknown }[] }) => {
    calls.push({
      onChange: p.onChange,
      labels: p.options.map((o) =>
        String((o.label as { props?: { children?: unknown[] } }).props?.children?.[0] ?? ''),
      ),
    })
    return null
  },
}))

const { FontFamilyRow } = await import('./textRows')

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  calls.length = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  await i18n.changeLanguage('zh-CN')
})

const FONTS = Array.from({ length: 400 }, (_, i) => `Font ${i}`)

function render(props: Partial<Parameters<typeof FontFamilyRow>[0]> = {}) {
  return act(async () => {
    root.render(
      <TooltipProvider>
        <FontFamilyRow
          value="Font 3"
          // 每次都是**新数组、同内容**：真实场景里并表每次都是新引用
          options={[...FONTS]}
          onChange={() => {}}
          optionLabelOf={(o) => o}
          {...props}
        />
      </TooltipProvider>,
    )
  })
}

describe('字体下拉只在数据变了时重画', () => {
  it('父组件重渲染、数据没变：Select 不重画', async () => {
    await render()
    await render()
    await render({ onChange: () => {} }) // 回调换了新引用也不算「变了」
    expect(calls).toHaveLength(1)
  })

  it('值 / 选项内容 / 修改状态变了：照常重画', async () => {
    await render()
    await render({ value: 'Font 4' })
    await render({ value: 'Font 4', options: [...FONTS, 'Font new'] })
    await render({ value: 'Font 4', options: [...FONTS, 'Font new'], overridden: true, onReset: () => {} })
    expect(calls).toHaveLength(4)
  })

  it('换选中另一个字体相同的元素：不重画，但点下去写的是新元素', async () => {
    const first = vi.fn()
    const second = vi.fn()
    await render({ onChange: first })
    await render({ onChange: second }) // 数据全等 → 跳过重画
    expect(calls).toHaveLength(1)
    act(() => calls[0].onChange('Font 9'))
    expect(second).toHaveBeenCalledWith('Font 9')
    expect(first).not.toHaveBeenCalled()
  })

  it('语言切换：选项标签跟着重画', async () => {
    await render({ optionLabelOf: (o) => `${i18n.language}:${o}` })
    await act(async () => {
      await i18n.changeLanguage('en-US')
    })
    await render({ optionLabelOf: (o) => `${i18n.language}:${o}` })
    expect(calls).toHaveLength(2)
    expect(calls[1].labels[0]).toBe('en-US:Font 0')
  })
})
