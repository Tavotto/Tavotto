/**
 * 界面真的会说两种语言，而且**切换不需要刷新**。
 *
 * 这一批挂真组件、断言真 DOM 文本。为什么值得单独写：资源一致性（
 * resources.test.ts）只能证明「两份 JSON 结构一样」，证明不了组件确实把 key
 * 送进了 t()——一个忘了迁移的硬编码中文字符串在那批用例里全绿。
 *
 * 切换语言这条尤其要挂 DOM：`setLocale` 换的是 i18next 实例的语言，组件跟不
 * 跟得上取决于它有没有经 `useTranslation()` 订阅。漏订阅的表现是「切了语言，
 * 这个面板还是旧语言，刷新一下才好」——刷新之后就再也复现不了了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Inspector } from '@/components/inspector/Inspector'
import { ProjectPicker } from '@/components/ProjectPicker'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TopBar } from '@/components/TopBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DEFAULT_LOCALE, i18n, setLocale, type Locale } from '@/i18n'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 这批组件都会去问后端要点什么；返回空对象就够，不抛即可 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let container: HTMLDivElement
let root: Root

const mount = (node: React.ReactNode) => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<TooltipProvider>{node}</TooltipProvider>))
}

/**
 * 页面上所有可见文本 + 无障碍名（aria-label / title / placeholder 都算界面文案）。
 *
 * 扫 `document.body` 而不是 container：对话框走 Radix 的 portal，挂在 body 上，
 * 只看 container 会拿到一个空串然后「什么都没漏翻」地全绿。
 */
const uiText = () => {
  const parts = [document.body.textContent ?? '']
  for (const el of document.body.querySelectorAll('[aria-label],[title],[placeholder]')) {
    parts.push(
      el.getAttribute('aria-label') ?? '',
      el.getAttribute('title') ?? '',
      el.getAttribute('placeholder') ?? '',
    )
  }
  return parts.join('\n')
}

const hasCjk = (s: string) => /[一-鿿]/.test(s)

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_i18n_render')
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  await act(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE)
  })
})

/** 换语言并等 React 把订阅者重渲染完 */
const switchTo = async (locale: Locale) => {
  await act(async () => {
    await setLocale(locale)
  })
}

describe('ProjectPicker', () => {
  beforeEach(() => {
    useProjectStore.setState({ recent: [], project: null })
  })

  it('中文界面：标题与主动作是中文', () => {
    mount(<ProjectPicker />)
    const text = uiText()
    expect(text).toContain('选择项目')
    expect(text).toContain('新建项目')
    expect(text).toContain('浏览目录…')
  })

  it('英文界面：同样的位置换成英文，且一个汉字都不剩', async () => {
    mount(<ProjectPicker />)
    await switchTo('en-US')
    const text = uiText()
    expect(text).toContain('Choose a project')
    expect(text).toContain('New project')
    expect(text).toContain('Browse folder…')
    expect(hasCjk(text)).toBe(false)
  })
})

describe('TopBar', () => {
  it('中文 / 英文各渲染一遍，导出按钮跟着换', async () => {
    mount(<TopBar />)
    expect(uiText()).toContain('导出')

    await switchTo('en-US')
    const en = uiText()
    expect(en).toContain('Export')
    expect(hasCjk(en)).toBe(false)
  })

  it('切回中文同样立即生效——单向能换不算能换', async () => {
    mount(<TopBar />)
    await switchTo('en-US')
    expect(uiText()).toContain('Export')

    await switchTo('zh-CN')
    expect(uiText()).toContain('导出')
  })
})

describe('SettingsDialog', () => {
  beforeEach(() => {
    useUiStore.setState({ settingsOpen: true, settingsSection: null })
  })

  it('中文界面：分区名与语言项都在', () => {
    mount(<SettingsDialog />)
    const text = uiText()
    expect(text).toContain('设置')
    expect(text).toContain('界面语言')
  })

  it('语言下拉里两档都用**目标语言自己的名字**写，不跟着界面语言翻译', async () => {
    mount(<SettingsDialog />)
    const options = () =>
      [...document.body.querySelectorAll('option')].map((o) => o.textContent?.trim())
    expect(options()).toEqual(expect.arrayContaining(['简体中文', 'English']))

    await switchTo('en-US')
    expect(options()).toEqual(expect.arrayContaining(['简体中文', 'English']))
  })

  it('在设置里选英文：偏好落盘 + 界面当场变，不用刷新', async () => {
    mount(<SettingsDialog />)
    const select = [...document.body.querySelectorAll('select')].find((s) =>
      [...s.options].some((o) => o.value === 'en-US'),
    )!

    await act(async () => {
      select.value = 'en-US'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(localStorage.getItem('tavotto.locale')).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    const text = uiText()
    expect(text).toContain('Settings')
    expect(text).toContain('Language')
  })
})

describe('Inspector', () => {
  it('中文 / 英文：标签页名与面板无障碍名都跟着换', async () => {
    mount(<Inspector />)
    const zhAria = container.querySelector('aside')!.getAttribute('aria-label')
    expect(zhAria).toBe('右侧面板')
    expect(uiText()).toContain('属性')

    await switchTo('en-US')
    expect(container.querySelector('aside')!.getAttribute('aria-label')).toBe('Right panel')
    const en = uiText()
    expect(en).toContain('Properties')
    expect(hasCjk(en)).toBe(false)
  })
})

describe('切换语言后无需刷新', () => {
  it('已经挂着的组件当场重渲染（不卸载、不重新 mount）', async () => {
    mount(<TopBar />)
    const before = container.querySelector('header')!
    await switchTo('en-US')
    // 还是同一个 DOM 节点：说明是重渲染，不是整棵树被换掉
    expect(container.querySelector('header')).toBe(before)
    expect(uiText()).toContain('Export')
  })

  it('切换只碰语言偏好，不动文档数据', async () => {
    mount(<TopBar />)
    const doc = useDocumentStore.getState().doc
    await switchTo('en-US')
    expect(useDocumentStore.getState().doc).toBe(doc)
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})
