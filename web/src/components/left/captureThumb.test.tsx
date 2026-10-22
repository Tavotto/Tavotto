/**
 * 捕获结果弹层的缩略图（CaptureThumb）：有预览 / 加载中 / 失败三态，
 * 以及项目切换后迟到的响应被丢弃。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CapturedFigureDescriptor } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { CaptureThumb, ProbeResultsDialog } from './ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const desc = (stem: string, size: [number, number] = [150, 150]): CapturedFigureDescriptor => ({
  asset_id: `runtime:fig11.py#${stem}`,
  script: 'fig11.py',
  entry: '__main__',
  stem,
  capture_source: 'pyplot',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: size,
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

let host: HTMLElement
let root: Root

const render = async (node: React.ReactNode) => {
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
}
const thumb = () => host.querySelector<HTMLElement>('[data-capture-thumb]')!
const img = () => host.querySelector<HTMLImageElement>('img')

beforeEach(() => {
  setCurrentProjectId('pa')
  useRuntimeAssetStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  setCurrentProjectId(null)
})

describe('CaptureThumb', () => {
  it('加载中：骨架占位、<img> 懒加载、取图走 runtime 预览端点、alt 是图名', async () => {
    await render(<CaptureThumb d={desc('Fig11_a')} />)
    expect(thumb().dataset.captureThumb).toBe('loading')
    expect(img()?.getAttribute('loading')).toBe('lazy')
    expect(img()?.getAttribute('alt')).toBe('Fig11_a')
    expect(img()?.getAttribute('src')).toContain('/api/runtime/preview?id=')
    expect(img()?.getAttribute('src')).toContain(encodeURIComponent('runtime:fig11.py#Fig11_a'))
  })

  it('有预览：load 之后显示图，槽固定 64×48、图幅按比例', async () => {
    await render(<CaptureThumb d={desc('wide', [160, 80])} />)
    await act(async () => img()!.dispatchEvent(new Event('load')))
    expect(thumb().dataset.captureThumb).toBe('ready')
    expect(thumb().style.width).toBe('64px')
    expect(thumb().style.height).toBe('48px')
    const frame = img()!.parentElement as HTMLElement
    expect(frame.style.width).toBe('64px')
    expect(frame.style.height).toBe('32px')
  })

  it('失败：换成中性图标，不留 <img>', async () => {
    await render(<CaptureThumb d={desc('gone')} />)
    await act(async () => img()!.dispatchEvent(new Event('error')))
    expect(thumb().dataset.captureThumb).toBe('failed')
    expect(img()).toBeNull()
    expect(host.querySelector('[data-capture-thumb-fallback]')).not.toBeNull()
  })

  it('迟到响应：项目换了之后 load / error 回调不改状态（不触发重渲染），且重渲染后不再渲染 <img>', async () => {
    await render(<CaptureThumb d={desc('late')} />)
    const el = img()!
    expect(thumb().dataset.captureThumb).toBe('loading')
    await act(async () => {
      setCurrentProjectId('pb') // 切项目（对话框尚未卸载的空窗）；切项目本身不会触发这个组件重渲染
    })
    // 回调里的代际判断挡住 setState：没有重渲染，渲染期 live 判断没有机会介入，属性仍是切走前渲染出的 loading。
    // 没有这道判断 → setState 触发重渲染 → live 为假 → 变成 failed（或 ready），这里就红。
    await act(async () => el.dispatchEvent(new Event('load')))
    expect(thumb().dataset.captureThumb).toBe('loading')
    await act(async () => el.dispatchEvent(new Event('error')))
    expect(thumb().dataset.captureThumb).toBe('loading')
    // A → B → A：回到同一个 id 也已换代，迟到响应仍被丢弃
    await act(async () => {
      setCurrentProjectId('pa')
    })
    await act(async () => el.dispatchEvent(new Event('load')))
    expect(thumb().dataset.captureThumb).toBe('loading')
    // 之后任何一次重渲染：A 项目的 id 不许再拿去取图
    await render(<CaptureThumb d={desc('late')} />)
    expect(img()).toBeNull()
  })
})

describe('ProbeResultsDialog 行布局', () => {
  it('每行 缩略图 + 图名 + 尺寸 + 按钮；失败的缩略图不影响「添加到画布」', async () => {
    await render(
      <ProbeResultsDialog
        script="fig11_xps_c1s_analysis.py"
        descriptors={[desc('Fig11_xps_chemistry'), desc('Fig11_xps_chemistry_a')]}
        dropped={0}
        open
        onOpenChange={() => {}}
      />,
    )
    const rows = document.querySelectorAll('[data-capture-row]')
    expect(rows).toHaveLength(2)
    for (const r of rows) {
      expect(r.querySelector('[data-capture-thumb]')).not.toBeNull()
      expect(r.querySelectorAll('button')).toHaveLength(1)
    }
    const first = rows[0].querySelector('img')!
    await act(async () => first.dispatchEvent(new Event('error')))
    expect(rows[0].querySelector('button')?.hasAttribute('disabled')).toBe(false)
  })
})
