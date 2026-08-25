/**
 * Landing 的结构契约（§29.3）：案例库是主角、三张卡片都可看代码可启动、
 * 中央试验台在场、上传入口是次级且上传前就能看到单文件边界、桌面版出口在。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EXAMPLES } from '../examples'
import { PlaygroundLanding } from './PlaygroundLanding'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const renderLanding = (onLaunch = vi.fn(), onFile = vi.fn()) => {
  act(() => {
    root.render(<PlaygroundLanding onLaunch={onLaunch} onFile={onFile} />)
  })
  return { onLaunch, onFile }
}

const buttonsNamed = (name: string) =>
  [...container.querySelectorAll('button')].filter((b) => b.textContent?.includes(name))

describe('PlaygroundLanding', () => {
  it('主标题与副题可见，案例库是第一主角', () => {
    renderLanding()
    expect(container.textContent).toContain('挑一张图，亲手改一次。')
    expect(container.textContent).toContain('运行和编辑都在你的浏览器里完成')
    const cards = container.querySelectorAll('[data-example-card]')
    expect(cards).toHaveLength(3)
  })

  it('每张卡片：真实封面 + 名称 + 说明 + 可编辑提示 + 查看代码 + 开始体验', () => {
    renderLanding()
    for (const ex of EXAMPLES) {
      const card = container.querySelector(`[data-example-card="${ex.id}"]`)!
      expect(card).toBeTruthy()
      const img = card.querySelector('img')!
      expect(img.getAttribute('src')).toBeTruthy()
      expect(img.getAttribute('alt')).toBeTruthy()
      // 尺寸显式声明，防 layout shift
      expect(Number(img.getAttribute('width'))).toBeGreaterThan(0)
      expect(Number(img.getAttribute('height'))).toBeGreaterThan(0)
      expect(card.textContent).toContain(ex.filename)
      expect([...card.querySelectorAll('button')].map((b) => b.textContent)).toEqual(
        expect.arrayContaining([expect.stringContaining('查看代码'), expect.stringContaining('开始体验')]),
      )
    }
  })

  it('主推案例带「适合第一次体验」徽章，且只有一张', () => {
    renderLanding()
    const badges = [...container.querySelectorAll('[data-example-card]')].filter((c) =>
      c.textContent?.includes('适合第一次体验'),
    )
    expect(badges).toHaveLength(1)
  })

  it('中央试验台在场：拖放提示 + 点击等价路径说明', () => {
    renderLanding()
    expect(container.textContent).toContain('把案例拖到这里')
    expect(container.textContent).toContain('开始体验')
    const stage = container.querySelector('[data-stage-state]')
    expect(stage?.getAttribute('data-stage-state')).toBe('idle')
  })

  it('点「开始体验」把正确的案例交给 onLaunch', () => {
    const { onLaunch } = renderLanding()
    const kinetics = container.querySelector('[data-example-card="kinetics"]')!
    const start = [...kinetics.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('开始体验'),
    )!
    act(() => start.click())
    expect(onLaunch).toHaveBeenCalledTimes(1)
    expect(onLaunch.mock.calls[0][0].id).toBe('kinetics')
  })

  it('卡片聚焦后 Enter 启动（拖拽不是唯一入口）', () => {
    const { onLaunch } = renderLanding()
    const card = container.querySelector<HTMLElement>('[data-example-card="calibration"]')!
    expect(card.tabIndex).toBe(0)
    act(() => {
      card.focus()
      card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(onLaunch).toHaveBeenCalledTimes(1)
    expect(onLaunch.mock.calls[0][0].id).toBe('calibration')
  })

  it('上传入口是次级：边界说明在上传前可见，支持范围可展开', () => {
    renderLanding()
    expect(container.textContent).toContain('已有一个独立脚本？')
    expect(container.textContent).toContain('仅适合不依赖本地数据、同目录模块或本地资源的单文件脚本')
    expect(buttonsNamed('上传独立脚本')).toHaveLength(1)
    // disclosure 展开后能读到「不适合」清单
    const details = [...container.querySelectorAll('details')].find((d) =>
      d.textContent?.includes('查看支持范围'),
    )!
    act(() => {
      details.open = true
    })
    expect(details.textContent).toContain('不适合')
    expect(details.textContent).toContain('read_csv')
  })

  it('选择文件走 onFile（校验链在 PlaygroundApp）', () => {
    const { onFile } = renderLanding()
    const input = container.querySelector<HTMLInputElement>('input[type=file]')!
    const file = new File(['print(1)\n'], 'mine.py', { type: 'text/x-python' })
    act(() => {
      Object.defineProperty(input, 'files', { value: [file], configurable: true })
      input.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onFile).toHaveBeenCalledTimes(1)
    expect(onFile.mock.calls[0][0].name).toBe('mine.py')
  })

  it('桌面版出口存在（下载链接 + 完整项目说明）', () => {
    renderLanding()
    const links = [...container.querySelectorAll('a')].filter((a) =>
      a.textContent?.includes('下载 Tavotto'),
    )
    expect(links.length).toBeGreaterThanOrEqual(1)
    expect(container.textContent).toContain('桌面版')
  })
})
