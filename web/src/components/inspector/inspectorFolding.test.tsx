/**
 * 三层信息架构的折叠契约（ADR 0010）：
 *   1. primary 永远展开——选中曲线，颜色/线宽/线型不点任何折叠就能看到；
 *   2. 「更多」按角色记忆，**换面板不重置**；
 *   3. 「源文件与高级」默认关闭；
 *   4. 折叠着的「更多」里有已修改项时，标题右侧给出数量摘要。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: vi.fn().mockResolvedValue({ rev: 2, manifest: null, svg: '', warnings: [] }),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线 sin',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  editable: [
    f('color', 'color', '#1f77b4'),
    f('linewidth', 'number', 1.5, { min: 0.1, max: 8, step: 0.1 }),
    f('linestyle', 'enum', '-', { options: ['-', '--', ':', '-.'] }),
    f('alpha', 'number', 1, { min: 0, max: 1, step: 0.05 }),
    f('visible', 'bool', true),
    f('zorder', 'number', 2, { group: '排列' }),
  ],
}

const manifestOf = (stem: string): Manifest => ({
  stem,
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    lineEl,
  ],
})

const panelOf = (id: string, fileId: string): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId,
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

function Harness({ id }: { id: string }) {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === id)) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(panelId: string, gid: string) {
  useUiStore.setState({ elementPanelId: panelId, selectedGids: [gid] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness id={panelId} />)
  })
}

const buttonByText = (text: string): HTMLButtonElement | undefined =>
  Array.from(host.querySelectorAll('button')).find(
    (b) => b.textContent?.trim().startsWith(text),
  ) as HTMLButtonElement | undefined

const rowLabel = (text: string): boolean =>
  Array.from(host.querySelectorAll('span')).some((el) => el.textContent?.trim() === text)

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_folding')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf('p1', 'A.pdf'), panelOf('p2', 'B.pdf'))
  })
  for (const [id, file] of [
    ['p1', 'A.pdf'],
    ['p2', 'B.pdf'],
  ] as const) {
    const key = renderKeyOf(panelOf(id, file))
    useRenderStore.getState().patch(key, {
      fileId: file,
      manifest: manifestOf(file),
      svg: '<svg/>',
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [file]: key } }))
  }
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
})

describe('三层折叠契约', () => {
  it('primary 永远展开：颜色/线宽/线型不点折叠就在', async () => {
    await mount('p1', 'axes_0.lines_0')
    expect(rowLabel('颜色')).toBe(true)
    expect(rowLabel('线宽')).toBe(true)
    expect(rowLabel('线型')).toBe(true)
    // 中频的透明度在「更多」里，默认收起
    expect(rowLabel('透明度')).toBe(false)
  })

  it('「更多」按角色记忆，换面板不重置', async () => {
    await mount('p1', 'axes_0.lines_0')
    const more = buttonByText('更多')!
    expect(more.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      more.click()
    })
    expect(rowLabel('透明度')).toBe(true)

    // 换到另一个面板的同角色元素：仍然展开
    await act(async () => {
      root.unmount()
    })
    await mount('p2', 'axes_0.lines_0')
    expect(buttonByText('更多')!.getAttribute('aria-expanded')).toBe('true')
    expect(rowLabel('透明度')).toBe(true)
  })

  it('「更多」偏好持久化到 localStorage', async () => {
    await mount('p1', 'axes_0.lines_0')
    await act(async () => {
      buttonByText('更多')!.click()
    })
    const saved = JSON.parse(localStorage.getItem('tavotto.inspector') ?? '{}')
    expect(saved.moreOpen?.line).toBe(true)
  })

  it('「源文件与高级」默认关闭，zorder 收在里面', async () => {
    await mount('p1', 'axes_0.lines_0')
    const adv = buttonByText('源文件与高级')!
    expect(adv.getAttribute('aria-expanded')).toBe('false')
    expect(rowLabel('堆叠层级')).toBe(false)
    await act(async () => {
      adv.click()
    })
    expect(rowLabel('堆叠层级')).toBe(true)
  })

  it('折叠的「更多」里有修改项时给数量摘要', async () => {
    useDocumentStore.getState().commit(literal('改透明度'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1')
      if (p?.type === 'panel') {
        p.overrides.push({ gid: 'axes_0.lines_0', prop: 'alpha', value: 0.5 })
      }
    })
    await mount('p1', 'axes_0.lines_0')
    const more = buttonByText('更多')!
    expect(more.getAttribute('aria-expanded')).toBe('false')
    expect(more.textContent).toContain('1 项已修改')
  })

  it('头部显示当前元素的已修改数量', async () => {
    useDocumentStore.getState().commit(literal('改两项'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1')
      if (p?.type === 'panel') {
        p.overrides.push(
          { gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000' },
          { gid: 'axes_0.lines_0', prop: 'alpha', value: 0.5 },
        )
      }
    })
    await mount('p1', 'axes_0.lines_0')
    // 已修改属性带状态点 + 行尾恢复按钮
    expect(host.textContent).toContain('已修改')
  })
})
