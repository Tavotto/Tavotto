/**
 * 树的展开 / 收起高度过渡（TreeBranch）：首次挂载不播、翻转才播；收起时保活一个退场时长，
 * 期间容器 inert + aria-hidden 且放的是上一次展开时的行；之后卸载。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { findVisibleRow, TreeBranch } from './TreeBranch'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const render = (open: boolean, kids: string[] = ['a', 'b']) =>
  act(async () =>
    root.render(
      <ul role="tree">
        <TreeBranch open={open}>
          {kids.map((k) => (
            <li key={k} role="treeitem" data-row={k}>
              {k}
            </li>
          ))}
        </TreeBranch>
      </ul>,
    ),
  )
const branch = () => host.querySelector<HTMLElement>('[data-tree-branch]')
const wait = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))))

describe('TreeBranch', () => {
  it('首次挂载就是展开的：直接就位，不播动画；行在 role=group 里', async () => {
    await render(true)
    expect(branch()!.dataset.treeBranch).toBe('open')
    expect(branch()!.className).not.toMatch(/animate-reveal/)
    expect(host.querySelector('[role="group"]')!.querySelectorAll('[data-row]')).toHaveLength(2)
    expect(branch()!.hasAttribute('inert')).toBe(false)
  })

  it('一开始就是收起的：什么都不渲染', async () => {
    await render(false, [])
    expect(branch()).toBeNull()
  })

  it('展开（翻转）才播动画：reveal-in', async () => {
    await render(false, [])
    await render(true)
    expect(branch()!.dataset.treeBranch).toBe('open')
    expect(branch()!.className).toMatch(/data-\[tree-branch=open\]:animate-reveal-in/)
    expect(host.querySelectorAll('[data-row]')).toHaveLength(2)
  })

  it('收起：保活一个退场时长，期间 inert + aria-hidden 且还是上一次的行；之后卸载', async () => {
    await render(true)
    await render(false, []) // 调用方收起后传进来的是空的
    const b = branch()!
    expect(b.dataset.treeBranch).toBe('closed')
    expect(b.hasAttribute('inert')).toBe(true)
    expect(b.getAttribute('aria-hidden')).toBe('true')
    expect(b.className).toMatch(/data-\[tree-branch=closed\]:animate-reveal-out/)
    expect([...host.querySelectorAll('[data-row]')].map((e) => e.textContent)).toEqual(['a', 'b'])
    await wait(200)
    expect(branch()).toBeNull()
    expect(host.querySelectorAll('[data-row]')).toHaveLength(0)
  })

  it('收起中又展开：回到 open，不卸载', async () => {
    await render(true)
    await render(false, [])
    await render(true, ['a', 'b', 'c'])
    await wait(200)
    expect(branch()!.dataset.treeBranch).toBe('open')
    expect(host.querySelectorAll('[data-row]')).toHaveLength(3)
  })

  it('findVisibleRow：只认可见的行——收起保活期间（inert + aria-hidden）里的行当作不存在', async () => {
    await render(true)
    expect(findVisibleRow(host, 'data-row', 'a')?.textContent).toBe('a')
    expect(findVisibleRow(host, 'data-row', 'zzz')).toBeNull()
    await render(false, [])
    expect(host.querySelector('[data-row="a"]')).not.toBeNull() // 还在 DOM 里
    expect(findVisibleRow(host, 'data-row', 'a')).toBeNull()
    await wait(200)
    expect(findVisibleRow(host, 'data-row', 'a')).toBeNull()
  })
})
