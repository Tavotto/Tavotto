/** `OptionGrid framed`：格子里放白底缩略图时，选中那一格另加 1px 内描边（浅色透明、暗色是随主题的浅墨）；缺省关，别的用法一点不变。 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { OptionGrid } from './OptionGrid'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const render = async (framed?: boolean) => {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () =>
    root.render(
      <TooltipProvider>
        <OptionGrid value="a" ariaLabel="g" columns={2} onChange={() => {}} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]} {...(framed === undefined ? {} : { framed })} />
      </TooltipProvider>,
    ),
  )
  return { host, done: () => act(async () => root.unmount()).then(() => host.remove()) }
}
const cell = (host: HTMLElement, v: string) => host.querySelector<HTMLElement>(`[data-value="${v}"]`)!

describe('OptionGrid framed', () => {
  it('缺省 / false：选中格的类名里没有描边（逐字与以前一样）', async () => {
    for (const f of [undefined, false]) {
      const { host, done } = await render(f)
      expect(cell(host, 'a').className).not.toContain('inset-ring')
      expect(cell(host, 'a').className).toContain('bg-thumb')
      await done()
    }
  })

  it('framed：只有选中的那一格带 1px 内描边（inset-ring-thumb-frame），其它格没有；选中态仍是浮起 + 字重（不只靠颜色）', async () => {
    const { host, done } = await render(true)
    expect(cell(host, 'a').className).toContain('inset-ring')
    expect(cell(host, 'a').className).toContain('inset-ring-thumb-frame')
    expect(cell(host, 'a').className).toContain('shadow-thumb')
    expect(cell(host, 'b').className).not.toContain('inset-ring')
    await done()
  })
})

describe('index.css：--color-thumb-frame 浅色透明、暗色（两段逐字相同的暗色表）是随主题的浅墨', () => {
  const css = readFileSync(`${process.cwd()}/src/index.css`, 'utf8')
  it('一处透明、两处暗色表里同值', () => {
    expect(css.match(/--color-thumb-frame: transparent;/g)).toHaveLength(1)
    const dark = css.match(/--color-thumb-frame: color-mix\(in srgb, var\(--color-ink\) 55%, transparent\);/g)
    expect(dark).toHaveLength(2)
  })
})
