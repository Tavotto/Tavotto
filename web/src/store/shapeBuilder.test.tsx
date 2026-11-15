/**
 * 形状生成器（⇧M，ADR 0185 M3 第三批）：真的 WASM 内核。进入时切成区域；拖过几块并成一块、⌥ 删；
 * 退出时有改动才一次 commit（id / 样式 / 位置规则与路径查找器相同），没改动不进历史；原对象在别处被改了就不落地退出；
 * 覆盖层的指针交互、⇧M / Esc 键、内核加载不了时灰掉。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { setVecopsSource } from '@/lib/vecops/kernel'
import { ShapeBuilderOverlay } from '@/canvas/ShapeBuilderOverlay'
import { emptyProject, type CanvasObject, type PathObject, type ShapeObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { blockedReason, pathfinderCommand } from './pathfinderActions'
import { useSelectionStore } from './selectionStore'
import {
  beginBuilderGesture,
  endBuilderGesture,
  enterShapeBuilder,
  extendBuilderGesture,
  finishShapeBuilder,
  useShapeBuilderStore,
} from './shapeBuilder'
import { useUiStore } from './uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const WASM = path.resolve(process.cwd(), 'src/lib/vecops/vecops.wasm')
const realSource = async () => {
  const b = readFileSync(WASM)
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const circle = (id: string, cx: number, fill: string): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'ellipse',
  x: cx - 10,
  y: 20,
  w: 20,
  h: 20,
  strokePt: 0.5,
  strokeAlign: 'center',
  color: '#000000',
  fill,
})
const objs = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length
const faces = () => useShapeBuilderStore.getState().faces

async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_sb_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(select)
}
const two = () => seed([circle('a', 30, '#ff0000'), circle('b', 42, '#0000ff')], ['a', 'b'])
/** 区域 i 盖着的是哪个原对象（看它的 key） */
const keyOf = (i: number) => useShapeBuilderStore.getState().sources[faces()[i].key].id

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  setVecopsSource(realSource)
  useShapeBuilderStore.setState({ active: false, sources: [], originals: [], faces: [], gesture: null, hover: null, changed: false })
  useUiStore.setState({ tool: 'select' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  setVecopsSource(null)
})

describe('会话', () => {
  it('进入：两圆切成三块（左月牙、透镜、右月牙），文档不动', async () => {
    await two()
    expect(await enterShapeBuilder()).toBe(true)
    expect(faces()).toHaveLength(3)
    expect(objs().map((o) => o.type)).toEqual(['shape', 'shape'])
    expect(past()).toBe(0)
  })

  it('拖过两块 = 并成一块；⌥ 点一块 = 删掉；退出时一次 commit，结果沿用样式来源的 id（每个原对象只给第一块）', async () => {
    await two()
    await enterShapeBuilder()
    const lens = faces().findIndex((_, i) => keyOf(i) === 'b' && faces().filter((f) => f.key === faces()[i].key).length > 1)
    // 把蓝圆的两块（透镜 + 右月牙）并起来
    const blue = faces().map((_, i) => i).filter((i) => keyOf(i) === 'b')
    expect(blue).toHaveLength(2)
    expect(lens).toBeGreaterThanOrEqual(0)
    beginBuilderGesture(blue[0], false)
    extendBuilderGesture(blue[1])
    await endBuilderGesture()
    expect(faces()).toHaveLength(2)
    // ⌥ 点红色月牙删掉
    const red = faces().findIndex((_, i) => keyOf(i) === 'a')
    beginBuilderGesture(red, true)
    await endBuilderGesture()
    expect(faces()).toHaveLength(1)
    expect(past()).toBe(0) // 会话里不进历史
    finishShapeBuilder()
    expect(past()).toBe(1)
    expect(objs()).toHaveLength(1)
    const p = objs()[0] as PathObject
    expect(p).toMatchObject({ id: 'b', type: 'path', fill: '#0000ff' })
    expect(useSelectionStore.getState().ids).toEqual(['b'])
    useDocumentStore.getState().undo()
    expect(objs().map((o) => o.id)).toEqual(['a', 'b'])
  })

  it('只点一块（合并模式）= 不算改动；一次也没动就退出 = 文档不变、不进历史', async () => {
    await two()
    await enterShapeBuilder()
    beginBuilderGesture(0, false)
    await endBuilderGesture()
    finishShapeBuilder()
    expect(past()).toBe(0)
    expect(objs().map((o) => o.type)).toEqual(['shape', 'shape'])
  })

  it('原对象在别处被改了（撤销 / 别的命令）：不落地退出', async () => {
    await two()
    await enterShapeBuilder()
    beginBuilderGesture(0, true)
    await endBuilderGesture()
    await act(async () =>
      root.render(
        <svg>
          <ShapeBuilderOverlay t={{ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }} />
        </svg>,
      ),
    )
    await act(async () =>
      useDocumentStore.getState().commit(literal('别处改了'), (d) => {
        d.objects[0].x = 0
      }),
    )
    expect(useShapeBuilderStore.getState().active).toBe(false)
    expect(objs().map((o) => o.type)).toEqual(['shape', 'shape'])
  })

  it('换选区：收尾退出（有改动就落地）', async () => {
    await two()
    await enterShapeBuilder()
    beginBuilderGesture(0, true)
    await endBuilderGesture()
    await act(async () =>
      root.render(
        <svg>
          <ShapeBuilderOverlay t={{ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }} />
        </svg>,
      ),
    )
    await act(async () => useSelectionStore.getState().set(['a']))
    expect(useShapeBuilderStore.getState().active).toBe(false)
    expect(objs()).toHaveLength(2)
    expect(objs().every((o) => o.type === 'path')).toBe(true)
    expect(past()).toBe(1)
  })
})

describe('覆盖层与键盘', () => {
  function Host() {
    useKeyboard()
    return (
      <svg>
        <ShapeBuilderOverlay t={{ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }} />
      </svg>
    )
  }
  const keydown = (key: string, init: KeyboardEventInit = {}) =>
    act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }))
    })

  it('⇧M 进入、画出每块区域；按下一块再经过另一块、松手 = 合并；Enter 退出落地', async () => {
    await two()
    await act(async () => root.render(<Host />))
    await keydown('M', { shiftKey: true })
    // 进入要先加载内核（异步）
    await vi.waitFor(() => expect(useShapeBuilderStore.getState().active).toBe(true))
    await act(async () => {})
    const face = (i: number) => host.querySelector(`[data-builder-face="${i}"]`)!
    expect(host.querySelectorAll('[data-builder-face]')).toHaveLength(3)
    await act(async () => face(0).dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 })))
    await act(async () => face(1).dispatchEvent(new MouseEvent('pointerover', { bubbles: true })))
    expect(face(1).getAttribute('data-picked')).toBe('true')
    await act(async () => {
      window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    })
    await act(async () => {})
    expect(host.querySelectorAll('[data-builder-face]')).toHaveLength(2)
    await keydown('Enter')
    expect(useShapeBuilderStore.getState().active).toBe(false)
    expect(past()).toBe(1)
  })

  it('Esc 也退出；会话里 Delete 不删选中的对象', async () => {
    await two()
    await act(async () => root.render(<Host />))
    await keydown('M', { shiftKey: true })
    await vi.waitFor(() => expect(useShapeBuilderStore.getState().active).toBe(true))
    await act(async () => {})
    await keydown('Delete')
    expect(objs()).toHaveLength(2)
    await keydown('Escape')
    expect(useShapeBuilderStore.getState().active).toBe(false)
    expect(past()).toBe(0)
  })
})

it('内嵌画布（内核加载不了）：命令灰掉、说原因', async () => {
  setVecopsSource(async () => null)
  await two()
  const { loadVecops } = await import('@/lib/vecops/kernel')
  await loadVecops()
  const c = pathfinderCommand('shape-builder')
  expect(c.available()).toBe(true)
  expect(blockedReason(c)).not.toBeNull()
  expect(await enterShapeBuilder()).toBe(false)
})
