/** `MenuRadioItem` 的 `roomy`：不带说明也走「带说明」那一档的版式（同组里有的行带小字时对齐用）；不传 = 逐字不变（紧凑版式）。 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import { Menu, MenuRadioGroup, MenuRadioItem } from './Menu'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const render = async () => {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () =>
    root.render(
      <Menu trigger={<button data-trigger>m</button>}>
        <MenuRadioGroup value="b" onValueChange={() => {}}>
          <MenuRadioItem value="a" data-x="plain">plain</MenuRadioItem>
          <MenuRadioItem value="b" roomy data-x="roomy">roomy</MenuRadioItem>
          <MenuRadioItem value="c" description="d" data-x="desc">desc</MenuRadioItem>
        </MenuRadioGroup>
      </Menu>,
    ),
  )
  await act(async () => {
    document.querySelector('[data-trigger]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
  })
  return () => act(async () => root.unmount()).then(() => host.remove())
}

describe('MenuRadioItem roomy', () => {
  it('缺省紧凑（pl-6）；roomy 与带说明同一版式（pl-8 items-start），勾也在同一档位置', async () => {
    const done = await render()
    const row = (x: string) => document.querySelector<HTMLElement>(`[data-x="${x}"]`)!
    expect(row('plain').className).toContain('pl-6')
    expect(row('plain').className).not.toContain('pl-8')
    for (const x of ['roomy', 'desc']) {
      expect(row(x).className).toContain('pl-8')
      expect(row(x).className).toContain('items-start')
    }
    expect(row('roomy').querySelector('[class*="left-2"]')).not.toBeNull() // 选中的 roomy 行：勾与带说明的一档
    expect(row('roomy').querySelector('[class*="left-1.5"]')).toBeNull()
    await done()
  })
})
