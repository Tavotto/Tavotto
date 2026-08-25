/**
 * 首次引导契约（§29.7）：只观察不代劳。
 *   * 初始提示点击标题；选中目标后进第二步；
 *   * 未修改 / 改错属性 / 值不对：不完成；
 *   * 值达标但还在渲染：不完成；渲染完成才算，且先「正在核对源文件」；
 *   * 完整性 unchanged 才显示「一个字也没动」；unavailable 不下该结论；
 *   * changed 时整个组件闭嘴（报警横幅是权威）；
 *   * 完成态锁存：undo 把 override 拿掉也不回到第 2 步；
 *   * 跳过回调可用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import type { SourceIntegrity } from '../sourceIntegrity'
import { FEATURED_EXAMPLE } from '../examples'
import { GuidedTask } from './GuidedTask'

const task = FEATURED_EXAMPLE.guidedTask!

const basePanel = (overrides: PanelObject['overrides']): PanelObject => ({
  id: 'p1',
  type: 'panel',
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  fileId: 'kinetics.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides,
})

const integrity = (verdict: SourceIntegrity['verdict']): SourceIntegrity => ({
  verdict,
  originalSha256: 'a'.repeat(64),
  workspaceSha256: verdict === 'unchanged' ? 'a'.repeat(64) : 'b'.repeat(64),
})

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  useUiStore.setState({ selectedGids: [] })
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

interface Props {
  overrides?: PanelObject['overrides']
  verdict?: SourceIntegrity['verdict']
  renderBusy?: boolean
  renderFailed?: boolean
  onDismiss?: () => void
  onRecheck?: () => void
}

const render = ({
  overrides = [],
  verdict = 'unchanged',
  renderBusy = false,
  renderFailed = false,
  onDismiss = vi.fn(),
  onRecheck = vi.fn(),
}: Props = {}) => {
  act(() => {
    root.render(
      <GuidedTask
        task={task}
        scriptName="kinetics.py"
        panel={basePanel(overrides)}
        integrity={integrity(verdict)}
        renderBusy={renderBusy}
        renderFailed={renderFailed}
        onRequestIntegrityRecheck={onRecheck}
        onViewSource={vi.fn()}
        onDismiss={onDismiss}
      />,
    )
  })
}

const taskState = () =>
  container.querySelector('[data-guided-task]')?.getAttribute('data-guided-task')

describe('GuidedTask', () => {
  it('第一步：提示点击标题，不自动选中任何东西', () => {
    render()
    expect(taskState()).toBe('step-1')
    expect(container.textContent).toContain('点击图中的')
    expect(useUiStore.getState().selectedGids).toEqual([])
  })

  it('用户选中目标 gid 后进入第二步（字号提示）', () => {
    render()
    act(() => useUiStore.setState({ selectedGids: [task.targetGid] }))
    render() // 外部状态变化后的重渲染
    expect(taskState()).toBe('step-2')
    expect(container.textContent).toContain('9 pt')
    expect(container.textContent).toContain('12 pt')
  })

  it('改错属性不算完成；值不对不算完成', () => {
    render({ overrides: [{ gid: task.targetGid, prop: 'color', value: '#f00' }] })
    expect(taskState()).toBe('step-2')
    render({ overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 10 }] })
    expect(taskState()).toBe('step-2')
  })

  it('值达标但还在渲染：不完成；渲染落定后完成并触发一次完整性复核', () => {
    const onRecheck = vi.fn()
    render({
      overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }],
      renderBusy: true,
      onRecheck,
    })
    expect(taskState()).toBe('step-2')
    expect(onRecheck).not.toHaveBeenCalled()
    render({
      overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }],
      renderBusy: false,
      onRecheck,
    })
    expect(taskState()).toBe('done')
    expect(onRecheck).toHaveBeenCalledTimes(1)
  })

  it('核对中只说「正在核对源文件」，不提前宣称未改动', () => {
    render({
      overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }],
      verdict: 'checking',
    })
    expect(taskState()).toBe('done')
    expect(container.textContent).toContain('正在核对源文件')
    expect(container.textContent).not.toContain('一个字也没动')
  })

  it('核对通过才显示「kinetics.py 一个字也没动」', () => {
    render({
      overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }],
      verdict: 'unchanged',
    })
    expect(container.textContent).toContain('图已经改变')
    expect(container.textContent).toContain('kinetics.py 一个字也没动')
  })

  it('查不了（unavailable）不下「未改动」的结论', () => {
    render({
      overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }],
      verdict: 'unavailable',
    })
    expect(container.textContent).toContain('图已经改变')
    expect(container.textContent).not.toContain('一个字也没动')
  })

  it('完整性失效（changed）时组件整个不渲染——报警横幅才是权威', () => {
    render({ verdict: 'changed' })
    expect(container.querySelector('[data-guided-task]')).toBeNull()
  })

  it('完成态锁存：undo 拿掉 override 也不回到第 2 步', () => {
    render({ overrides: [{ gid: task.targetGid, prop: 'fontsize', value: 12 }] })
    expect(taskState()).toBe('done')
    render({ overrides: [] }) // undo 之后
    expect(taskState()).toBe('done')
  })

  it('跳过引导可用，不阻塞编辑', () => {
    const onDismiss = vi.fn()
    render({ onDismiss })
    const skip = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('跳过引导'),
    )!
    act(() => skip.click())
    expect(onDismiss).toHaveBeenCalledTimes(1)
    // 不是全屏遮罩：没有铺满视口的 overlay
    expect(container.querySelector('.fixed.inset-0')).toBeNull()
  })
})
