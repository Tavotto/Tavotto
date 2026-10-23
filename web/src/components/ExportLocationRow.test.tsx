/**
 * 导出对话框「位置」行：至少两级 / 家目录缩成 ~ / 非等宽 / title 是全路径；「更改…」直接弹选择文件夹
 * （桌面 = 原生选择器，浏览器 = DirBrowser），选完写项目的导出文件夹，取消不变，失败说人话。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  desktop: false,
  picked: null as string | null,
  pickTitle: '' as string | undefined,
  patch: vi.fn(),
}))

vi.mock('@/lib/desktop', async (orig) => ({
  ...(await orig<typeof import('@/lib/desktop')>()),
  isDesktop: () => h.desktop,
  pickDirectory: async (title?: string) => {
    h.pickTitle = title
    return h.picked
  },
}))
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  patchProjectSettings: (...a: unknown[]) => h.patch(...a),
}))
vi.mock('./DirBrowser', () => ({
  DirBrowser: (p: { initialPath?: string; fallbackPath?: string; highlight?: string; onClose: () => void; onPick: (path: string, create: boolean) => void }) => (
    <div data-fake-dirbrowser data-initial={p.initialPath} data-fallback={p.fallbackPath} data-highlight={p.highlight}>
      <button data-pick onClick={() => p.onPick('/data/figs/out', false)} />
      <button data-cancel onClick={p.onClose} />
    </div>
  ),
}))

import { initI18n } from '@/i18n'
import { ExportLocationRow, parentDir } from './ExportLocationRow'
import { abbreviateHome } from '@/lib/pathDisplay'
import { useProjectStore } from '@/store/projectStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
const setDir = (dir: string | undefined) =>
  useProjectStore.setState({
    project: { id: 'pa', settings: {}, export_dir: dir, backup_dir: '/b', home_dir: '/Users/ann' } as never,
  })
const q = (sel: string) => host.querySelector<HTMLElement>(sel)
const clickEl = (el: Element) => act(async () => { (el as HTMLElement).click() })

beforeEach(async () => {
  initI18n('zh-CN')
  h.desktop = false
  h.picked = null
  h.patch.mockReset()
  h.patch.mockImplementation(async (p: { export_dir?: string }) => ({
    settings: { export_dir: p.export_dir },
    export_dir: p.export_dir,
    backup_dir: '/b',
  }))
  setDir('/Users/ann/混合等离子体气体文章/export')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<ExportLocationRow />))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('位置显示', () => {
  it('家目录缩成 ~，保留末尾至少两级；title 是完整绝对路径；不是等宽字', () => {
    const el = q('[data-export-location]')!
    expect(el.textContent).toContain('~/混合等离子体气体文章/export')
    expect(el.textContent).not.toContain('/Users/ann')
    expect(el.getAttribute('title')).toBe('/Users/ann/混合等离子体气体文章/export')
    expect(el.className).not.toContain('font-mono')
    // 放不下时从左边省略（rtl + truncate），末尾两级留着
    expect(el.getAttribute('dir')).toBe('rtl')
    expect(el.className).toContain('truncate')
  })

  it('abbreviateHome：只认后端给的真实家目录，不按路径形状猜', () => {
    expect(abbreviateHome('/Users/a/p/export', '/Users/a')).toBe('~/p/export')
    expect(abbreviateHome('/home/a/p/export', '/home/a/')).toBe('~/p/export')
    expect(abbreviateHome('C:\\Users\\a\\p\\export', 'C:\\Users\\a')).toBe('~\\p\\export')
    expect(abbreviateHome('/Users/a', '/Users/a')).toBe('~')
    // 形状像家目录、但不是这台机器的家：不缩
    expect(abbreviateHome('/Users/Shared/exports', '/Users/a')).toBe('/Users/Shared/exports')
    expect(abbreviateHome('C:\\Users\\Public\\exports', 'C:\\Users\\a')).toBe('C:\\Users\\Public\\exports')
    // 前缀相同但不是同一级（/Users/ab 不在 /Users/a 下）
    expect(abbreviateHome('/Users/ab/p', '/Users/a')).toBe('/Users/ab/p')
    // 没有 home（老后端 / 没打开项目）：原样
    expect(abbreviateHome('/Users/a/p')).toBe('/Users/a/p')
  })

  it('/Users/Shared/exports 即使形状像家目录也不缩写（真实 home 是 /Users/ann）', async () => {
    setDir('/Users/Shared/exports')
    await act(async () => root.render(<ExportLocationRow />))
    expect(q('[data-export-location]')!.textContent).toContain('/Users/Shared/exports')
    expect(q('[data-export-location]')!.textContent).not.toContain('~')
  })
})

describe('parentDir', () => {
  it('去掉末级；根与盘符根', () => {
    expect(parentDir('/a/b/export')).toBe('/a/b')
    expect(parentDir('/a/b/export/')).toBe('/a/b')
    expect(parentDir('/export')).toBe('/')
    expect(parentDir('C:\\p\\export')).toBe('C:\\p')
    expect(parentDir('C:\\export')).toBe('C:\\')
    expect(parentDir('export')).toBeUndefined()
  })
})

describe('更改…', () => {
  it('浏览器：弹 DirBrowser（起点是当前导出目录）→ 选目录 → 写项目设置 → 位置更新；不再跳设置页', async () => {
    await clickEl(q('[data-export-location-change]')!)
    const fake = q('[data-fake-dirbrowser]')!
    // 从当前导出目录的上一级打开，当前目录高亮，上一级打不开回落到它自己
    expect(fake.getAttribute('data-initial')).toBe('/Users/ann/混合等离子体气体文章')
    expect(fake.getAttribute('data-highlight')).toBe('/Users/ann/混合等离子体气体文章/export')
    expect(fake.getAttribute('data-fallback')).toBe('/Users/ann/混合等离子体气体文章/export')
    await clickEl(q('[data-pick]')!)
    expect(h.patch).toHaveBeenCalledWith({ export_dir: '/data/figs/out' })
    expect(q('[data-fake-dirbrowser]')).toBeNull()
    expect(q('[data-export-location]')!.getAttribute('title')).toBe('/data/figs/out')
    expect(useProjectStore.getState().project!.export_dir).toBe('/data/figs/out')
  })

  it('浏览器：取消 = 什么都不变、不发请求', async () => {
    await clickEl(q('[data-export-location-change]')!)
    await clickEl(q('[data-cancel]')!)
    expect(q('[data-fake-dirbrowser]')).toBeNull()
    expect(h.patch).not.toHaveBeenCalled()
    expect(q('[data-export-location]')!.getAttribute('title')).toBe('/Users/ann/混合等离子体气体文章/export')
  })

  it('桌面：走原生选择器（不弹 DirBrowser），选完写项目设置', async () => {
    h.desktop = true
    h.picked = '/Users/ann/新位置'
    await clickEl(q('[data-export-location-change]')!)
    expect(q('[data-fake-dirbrowser]')).toBeNull()
    expect(h.pickTitle).toBe('选择导出文件夹')
    expect(h.patch).toHaveBeenCalledWith({ export_dir: '/Users/ann/新位置' })
    expect(q('[data-export-location]')!.textContent).toContain('~/新位置')
  })

  it('桌面：取消原生选择器 = 什么都不变', async () => {
    h.desktop = true
    h.picked = null
    await clickEl(q('[data-export-location-change]')!)
    expect(h.patch).not.toHaveBeenCalled()
  })

  it('后端拒绝（目录不可用）：位置不变，一句话说出原因', async () => {
    h.patch.mockRejectedValueOnce(new Error('boom'))
    await clickEl(q('[data-export-location-change]')!)
    await clickEl(q('[data-pick]')!)
    expect(q('[data-export-location-error]')).not.toBeNull()
    expect(q('[data-export-location]')!.getAttribute('title')).toBe('/Users/ann/混合等离子体气体文章/export')
  })

  it('写入在飞：位置行说「正在更新…」、更改钮灰着；落地后恢复并显示新位置', async () => {
    let release!: () => void
    h.patch.mockImplementationOnce(
      (p: { export_dir?: string }) =>
        new Promise((res) => {
          release = () => res({ settings: { export_dir: p.export_dir }, export_dir: p.export_dir, backup_dir: '/b' })
        }),
    )
    await clickEl(q('[data-export-location-change]')!)
    await clickEl(q('[data-pick]')!)
    expect(q('[data-export-location]')!.textContent).toBe('正在更新…')
    expect(q('[data-export-location]')!.hasAttribute('data-updating')).toBe(true)
    expect(q('[data-export-location-change]')!.hasAttribute('disabled')).toBe(true)
    await act(async () => release())
    expect(q('[data-export-location]')!.hasAttribute('data-updating')).toBe(false)
    expect(q('[data-export-location]')!.getAttribute('title')).toBe('/data/figs/out')
    expect(q('[data-export-location-change]')!.hasAttribute('disabled')).toBe(false)
  })

  it('disabled（导出进行中）：按钮灰着', async () => {
    await act(async () => root.render(<ExportLocationRow disabled />))
    expect(q('[data-export-location-change]')!.hasAttribute('disabled')).toBe(true)
  })
})
