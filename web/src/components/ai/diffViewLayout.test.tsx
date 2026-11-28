import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DiffView } from './DiffView'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const diff = '@@ -1 +1 @@\n-a = 1\n+a = 2\n'

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
const render = (el: React.ReactNode) => act(async () => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => host.querySelector<HTMLElement>(sel)

it('卡片：计数层不接指针（不盖住复制 / 放大按钮）；没有绿勾；常驻箭头', async () => {
  await render(<DiffView diff={diff} script="p1.py" />)
  expect(q('[data-ai-diff-counts]')!.className).toContain('pointer-events-none')
  expect(q('[data-ai-diff-actions]')!.className).not.toContain('pointer-events-none')
  expect(q('[data-ai-diff-toggle] svg')).toBeTruthy()
  expect(q('[data-ai-diff-header] .text-ok svg')).toBeNull()
})

it('卡片：点标题栏任意处（含空白）收起 / 展开；点操作按钮不收起', async () => {
  await render(<DiffView diff={diff} script="p1.py" />)
  const header = q('[data-ai-diff-header]')!
  expect(q('[data-ai-diff-body]')).toBeTruthy()
  await act(async () => header.click())
  expect(q('[data-ai-diff-body]')).toBeNull()
  expect(q('[data-ai-diff-toggle]')!.getAttribute('aria-expanded')).toBe('false')
  await act(async () => header.click())
  expect(q('[data-ai-diff-body]')).toBeTruthy()
  await act(async () => q('[data-ai-diff-zoom]')!.click())
  expect(q('[data-ai-diff-body]')).toBeTruthy()
})

it('卡片：toggle 按钮被点一次只切换一次（不与标题栏重复）', async () => {
  await render(<DiffView diff={diff} script="p1.py" />)
  await act(async () => q('[data-ai-diff-toggle]')!.click())
  expect(q('[data-ai-diff-body]')).toBeNull()
})

it('「未改动 N 行」不带省略号前缀', async () => {
  const long = '@@ -1,40 +1,40 @@\n' + Array.from({ length: 15 }, (_, i) => ` c${i}`).join('\n') + '\n-a\n+b\n' + Array.from({ length: 15 }, (_, i) => ` d${i}`).join('\n') + '\n'
  await render(<DiffView diff={long} script="p1.py" />)
  const gap = q('[data-diff-row="gap"]')
  if (gap) expect(gap.textContent).not.toContain('⋯')
})

it('panel 形态：只有文件名、+N −N、常驻复制补丁；没有收起 / 放大 / 勾 / 「已修改」；正文常显', async () => {
  await render(<DiffView diff={diff} script="p1.py" variant="panel" />)
  expect(q('[data-ai-diff-variant]')!.getAttribute('data-ai-diff-variant')).toBe('panel')
  expect(q('[data-ai-diff-toggle]')).toBeNull()
  expect(q('[data-ai-diff-zoom]')).toBeNull()
  expect(q('[data-ai-revert]')).toBeNull()
  expect(q('[data-ai-diff-copy]')).toBeTruthy()
  expect(q('[data-ai-diff-copy]')!.closest('[class*="opacity-0"]')).toBeNull()
  expect(q('[data-ai-diff-counts]')!.textContent).toBe('+1−1')
  expect(q('[data-ai-diff-body]')!.className).toContain('overflow-auto')
  expect(q('[data-ai-diff]')!.textContent).not.toContain('已修改')
  expect(q('[data-ai-diff]')!.className).toContain('flex-col')
})
