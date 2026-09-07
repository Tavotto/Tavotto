/**
 * 快捷键帮助（审计 T50）。验收原话：**所有快捷键说明完整可读**。
 *
 * 三件事逐条量：① 分组按用户的任务分且顺序固定；② 说明**不截断**——审计截图
 * 里一半的句子读不到结尾，靠的就是那一行 `truncate`；③ 可搜索，键位与说明
 * 两边都能中。
 *
 * jsdom 没有布局引擎，量不出「有没有真的被切掉」——这里量的是**判据**：
 * 说明那一列不许带截断类、必须允许换行。真像素归 `e2e/i18n.spec.ts` 那条路。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setLocale, t } from '@/i18n'
import { GROUPS, ShortcutHelp, filterGroups } from './ShortcutHelp'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

async function open() {
  useUiStore.getState().setShortcutHelpOpen(true)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ShortcutHelp />)
  })
  await act(async () => {})
}

const groups = () => [...document.querySelectorAll('[data-shortcut-group]')] as HTMLElement[]
const rows = () => [...document.querySelectorAll('[data-shortcut-row]')] as HTMLElement[]
const search = () => document.querySelector('input') as HTMLInputElement

async function type(v: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(search(), v)
    search().dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useUiStore.getState().setShortcutHelpOpen(false)
  await setLocale('zh-CN')
})

describe('快捷键帮助的分组', () => {
  it('按用户的任务分组，顺序固定', async () => {
    await open()
    expect(groups().map((g) => g.dataset.shortcutGroup)).toEqual([
      'file',
      'selection',
      'editing',
      'arrange',
      'view',
      'tools',
      'tutorial',
    ])
  })

  it('每一条说明都有译文，没有漏进界面的原始 key', async () => {
    for (const locale of ['zh-CN', 'en-US'] as const) {
      await setLocale(locale)
      await open()
      const texts = rows().map((r) => r.children[1]?.textContent ?? '')
      expect(texts).toHaveLength(GROUPS.flatMap((g) => g.rows).length)
      for (const s of texts) {
        expect(s.trim()).not.toBe('')
        // i18next 找不到 key 时回显 key 本身；那正是「说明读不到」的另一种形态
        expect(s).not.toMatch(/^key\./)
      }
      await act(async () => root.unmount())
      host.remove()
    }
    await open() // afterEach 要卸一个
  })

  it('分组标题也有译文（改了分组 id 却没加译文会在这里红）', async () => {
    await open()
    for (const g of groups()) {
      const title = g.querySelector('h3')?.textContent ?? ''
      expect(title.trim()).not.toBe('')
      expect(title).not.toContain(`group.${g.dataset.shortcutGroup}`)
    }
  })
})

describe('说明整句可读', () => {
  it('说明那一列不截断、允许换行', async () => {
    await open()
    // 最长的一条（审计截图里被切掉的正是这种）
    const longest = rows()
      .map((r) => r.children[1] as HTMLElement)
      .reduce((a, b) => ((a.textContent ?? '').length >= (b.textContent ?? '').length ? a : b))
    expect(longest.className).not.toContain('truncate')
    expect(longest.className).toContain('whitespace-normal')
    expect(longest.className).toContain('break-words')
    expect((longest.textContent ?? '').length).toBeGreaterThan(20)
  })

  it('每一行渲染的是整句译文，不是被切过的一段', async () => {
    await open()
    const descs = GROUPS.flatMap((g) => g.rows).map((r) => r.desc)
    const shown = rows().map((r) => r.children[1]?.textContent ?? '')
    expect(shown).toEqual(descs.map((d) => t(`key.${d}`, { ns: 'shortcuts' })))
    // 省略号是审计里那半句读不到的直接证据；译文本身没有任何一条以它结尾
    for (const s of shown) expect(s.endsWith('…')).toBe(false)
  })
})

describe('搜索', () => {
  it('按说明搜：只留命中的组与行', async () => {
    await open()
    const before = rows().length
    await type('撤销')
    expect(rows().length).toBeGreaterThan(0)
    expect(rows().length).toBeLessThan(before)
    expect(groups().map((g) => g.dataset.shortcutGroup)).toEqual(['editing'])
  })

  it('按键位搜也能中', async () => {
    await open()
    await type('delete')
    expect(rows().map((r) => r.children[0]?.textContent)).toContain('Delete')
  })

  it('一条都不中时说清楚，不留一片空白', async () => {
    await open()
    await type('绝不会出现的字串 zzzz')
    expect(rows()).toHaveLength(0)
    expect(document.body.textContent).toContain(t('noMatch', { ns: 'shortcuts' }))
  })

  it('关掉再打开，搜索框是空的（下次不是从上次的过滤态开始）', async () => {
    await open()
    await type('撤销')
    await act(async () => useUiStore.getState().setShortcutHelpOpen(false))
    await act(async () => useUiStore.getState().setShortcutHelpOpen(true))
    expect(search().value).toBe('')
    expect(groups()).toHaveLength(GROUPS.length)
  })
})

describe('filterGroups 这条判据本身', () => {
  it('空查询原样返回', () => {
    expect(filterGroups(GROUPS, '   ')).toBe(GROUPS)
  })

  it('大小写不敏感', () => {
    const hit = filterGroups(GROUPS, 'DELETE')
    expect(hit.flatMap((g) => g.rows).some((r) => r.keys === 'Delete')).toBe(true)
  })

  it('空组不留下来', () => {
    for (const g of filterGroups(GROUPS, 'Delete')) expect(g.rows.length).toBeGreaterThan(0)
  })
})
