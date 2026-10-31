import { afterEach, describe, expect, it } from 'vitest'
import { inputModality, installInputModality } from './inputModality'

const key = (k: string, init: KeyboardEventInit = {}) =>
  document.body.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }))
const press = (type: 'pointerdown' | 'mousedown') =>
  document.body.dispatchEvent(new MouseEvent(type, { bubbles: true }))

let off: (() => void) | null = null
afterEach(() => off?.())

describe('输入方式（D4：程序化 focus 不画键盘焦点环）', () => {
  it('启动后什么都没发生：没有属性（焦点环照旧，默认不压制）', () => {
    off = installInputModality()
    expect(inputModality()).toBeNull()
  })

  it('指针按下 → pointer；Tab / 方向键 → keyboard', () => {
    off = installInputModality()
    press('pointerdown')
    expect(inputModality()).toBe('pointer')
    key('Tab')
    expect(inputModality()).toBe('keyboard')
    press('mousedown')
    expect(inputModality()).toBe('pointer')
    key('ArrowDown')
    expect(inputModality()).toBe('keyboard')
  })

  it('Enter / Space / Esc 是操作不是导航：鼠标点完按 Enter 仍是 pointer（反证：把 Enter 加进导航键这条会变红）', () => {
    off = installInputModality()
    press('pointerdown')
    for (const k of ['Enter', ' ', 'Escape']) key(k)
    expect(inputModality()).toBe('pointer')
  })

  it('带 ⌘ / Ctrl / Alt 的组合键不切换', () => {
    off = installInputModality()
    press('pointerdown')
    key('Tab', { ctrlKey: true })
    key('ArrowLeft', { metaKey: true })
    expect(inputModality()).toBe('pointer')
  })

  it('卸载后清掉属性并停止监听', () => {
    off = installInputModality()
    press('pointerdown')
    off()
    off = null
    expect(inputModality()).toBeNull()
    press('pointerdown')
    expect(inputModality()).toBeNull()
  })
})
