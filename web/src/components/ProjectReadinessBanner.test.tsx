/**
 * 项目摘要横幅（Prompt 08 §五）。
 *
 * 它是打开旧项目时用户看到的第一句话，所以两头都要守住：**该说的时候说清楚**
 * （几张能编辑、几张待连接、几张仅排版），**不该说的时候一个字不说**
 * （全部可编辑、空项目、报告还没到、这一版已经关过）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ProjectReadinessBanner } from '@/components/ProjectReadinessBanner'
import type { ReadinessPanel, ReadinessReport, ReadinessStatus } from '@/lib/api'
import {
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panelOf = (id: string, status: ReadinessStatus): ReadinessPanel => ({
  id,
  stem: id.replace(/\.[^.]+$/, ''),
  status,
  reason_code: status === 'editable' ? 'registered_source' : 'no_source_candidate',
  script: status === 'editable' ? 'a.py' : null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  details: {},
})

function reportOf(statuses: ReadinessStatus[], fingerprint = 'fp-1'): ReadinessReport {
  const panels = statuses.map((s, i) => panelOf(`Fig${i}.pdf`, s))
  const summary = {
    total: panels.length,
    editable: 0,
    auto_linkable: 0,
    needs_probe: 0,
    conflict: 0,
    source_missing: 0,
    layout_only: 0,
  }
  for (const p of panels) summary[p.status] += 1
  return {
    project_id: 'pj-a',
    fingerprint,
    generated_at: 1,
    summary,
    panels,
    conflicts: [],
    project: { writable: true, registry_valid: true, scan_ok: true, can_rescan: true },
    issues: [],
  }
}

let host: HTMLElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ProjectReadinessBanner />)
  })
}

const bar = () => host.querySelector('[role="status"]')
const buttonNamed = (name: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(name))

beforeEach(() => {
  localStorage.clear()
  resetReadinessBookkeeping()
  useProjectReadinessStore.getState().clear()
  useUiStore.setState({ registryOpen: false })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('显示条件', () => {
  it('有非可编辑的图：给出一句带数字的摘要', async () => {
    useProjectReadinessStore.setState({
      report: reportOf(['editable', 'editable', 'needs_probe', 'layout_only']),
    })
    await mount()
    const text = bar()?.textContent ?? ''
    // 四个数字都在，而且**分得开**：待连接与仅排版不是同一件事
    expect(text).toContain('4')
    expect(text).toContain('2 张可编辑')
    expect(text).toContain('1 张待连接')
    expect(text).toContain('1 张仅排版')
  })

  it('文案里不出现实现术语', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['conflict', 'editable']) })
    await mount()
    expect(bar()?.textContent ?? '').not.toMatch(/registry|注册表|stem|probe|AST|manifest/i)
  })

  it('全部可编辑：一个字都不说', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['editable', 'editable']) })
    await mount()
    expect(bar()).toBeNull()
  })

  it('空项目：不说', async () => {
    useProjectReadinessStore.setState({ report: reportOf([]) })
    await mount()
    expect(bar()).toBeNull()
  })

  it('报告还没到：不闪一条空提示', async () => {
    await mount()
    expect(bar()).toBeNull()
  })

  it('接入中心已经开着：不重复说同一份事实', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['layout_only']) })
    useUiStore.setState({ registryOpen: true })
    await mount()
    expect(bar()).toBeNull()
  })
})

describe('两个动作', () => {
  it('「查看接入状态」打开接入中心', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['layout_only']) })
    await mount()
    await act(async () => buttonNamed('查看接入状态')!.click())
    expect(useUiStore.getState().registryOpen).toBe(true)
  })

  it('「关闭」之后这一版不再出现，**画布上什么都没变**', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['layout_only']) })
    await mount()
    await act(async () => buttonNamed('关闭')!.click())
    expect(bar()).toBeNull()
    // 关横幅不是"处理掉了"：报告本身原样留着，接入中心里照旧看得到
    expect(useProjectReadinessStore.getState().report).not.toBeNull()
  })

  it('关掉之后事实变了（fingerprint 换代）：重新出现', async () => {
    useProjectReadinessStore.setState({ report: reportOf(['layout_only'], 'fp-1') })
    await mount()
    await act(async () => buttonNamed('关闭')!.click())
    expect(bar()).toBeNull()

    await act(async () => {
      useProjectReadinessStore.setState({
        report: reportOf(['layout_only'], 'fp-2'),
      })
    })
    expect(bar()).not.toBeNull()
  })
})
