/**
 * 顶栏说清「存到哪」（ADR 0096）：保存状态的 tooltip 写明去向，绑定的项目文件落后
 * 时排版名旁边亮一颗圆点（与画布页签的「未保存」同一颗）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type TextObject } from '@/types/document'
import { setProjectFile, startAutosave, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { DocumentMenu, SaveStateLabel } from './TopBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const text: TextObject = {
  id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
}

let root: Root
let stopAutosave: () => void
let host: HTMLDivElement

async function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <>
        <DocumentMenu />
        <SaveStateLabel />
      </>,
    )
  })
}

const label = () => host.querySelector<HTMLElement>('[data-save-destination]')!
const dot = () => host.querySelector('[data-project-file-dirty]')

beforeEach(async () => {
  localStorage.clear()
  // 自动保存槽位一律写成功：这里量的是顶栏怎么说，不是写盘本身
  globalThis.fetch = (async (_u: unknown, init?: RequestInit) =>
    init?.method === 'PUT'
      ? new Response('{"ok":true,"saved_at":1,"revision":"r0"}')
      : new Response('{}', { status: 404 })) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_topbar')
  await new Promise((r) => setTimeout(r, 10))
  stopAutosave = startAutosave()
  useDocumentStore.getState().silent((d) => {
    d.objects.push(text)
  })
  useDocumentStore.setState({ saveState: 'clean' })
})

afterEach(async () => {
  stopAutosave()
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  setCurrentProjectId(null)
  useProjectStore.setState({ project: null })
})

describe('保存去向', () => {
  it('没开项目：说「已存在本机」，tooltip 说没有打开项目', async () => {
    await render()
    expect(label().dataset.saveDestination).toBe('local')
    expect(label().textContent).toContain('已存在本机')
    expect(label().title).toContain('没有打开项目')
    expect(dot()).toBeNull()
  })

  it('开着项目但没绑定：tooltip 说只在本机、⌘S 存进项目', async () => {
    setCurrentProjectId('p1')
    useProjectStore.setState({ project: { open: true, id: 'p1' } })
    await render()
    expect(label().dataset.saveDestination).toBe('local')
    expect(label().title).toContain('还没有存进项目')
  })

  it('绑定且最新：「已保存到项目」，tooltip 给出 tavottofile 里的相对路径', async () => {
    setCurrentProjectId('p1')
    useProjectStore.setState({ project: { open: true, id: 'p1' } })
    setProjectFile({ projectId: 'p1', name: 'A', file: 'tavottofile/A.json', revision: 'r', dirty: false })
    await render()
    expect(label().dataset.saveDestination).toBe('project')
    expect(label().textContent).toBe('已保存到项目')
    expect(label().title).toContain('已保存到项目：tavottofile/A.json')
    expect(dot()).toBeNull()
  })

  it('绑定且有改动没存进项目：圆点亮、文字与 tooltip 都说项目里那份落后', async () => {
    setCurrentProjectId('p1')
    useProjectStore.setState({ project: { open: true, id: 'p1' } })
    setProjectFile({ projectId: 'p1', name: 'A', file: 'tavottofile/A.json', revision: 'r', dirty: false })
    await render()
    await act(async () => {
      useDocumentStore.getState().commit(literal('改'), (d) => {
        d.objects[0].x = 5
      })
    })
    expect(dot()).not.toBeNull()
    expect(dot()!.getAttribute('aria-label')).toContain('tavottofile/A.json')
    expect(label().title).toContain('有未保存的改动')
  })

  it('别的项目的绑定在这里不显示成「已保存到项目」', async () => {
    setCurrentProjectId('p2')
    useProjectStore.setState({ project: { open: true, id: 'p2' } })
    setProjectFile({ projectId: 'p1', name: 'A', file: 'tavottofile/A.json', revision: 'r', dirty: true })
    await render()
    expect(label().dataset.saveDestination).toBe('local')
    expect(dot()).toBeNull()
  })
})
