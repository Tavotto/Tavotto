/**
 * 流式回答的逐词显影（`lib/streamMarkdown` + `components/ai/Markdown`）。
 *
 * 钉三件事：
 *  1. streaming 时每个词一个 span（中文靠 Intl.Segmenter 切词，不是整段一个），
 *     非 streaming 一个 span 都没有——终稿到达后视觉零变化；
 *  2. **文字追加时旧词的 DOM 节点原样保留**（同一个对象），只有新词是新节点——
 *     这是「旧字纹丝不动、新字浮现一次」的全部机制：淡入是挂载动画，节点不重建就不重播；
 *  3. 标点贴在前一个词上、空白不包 span、markdown 照样生效（span 在 strong 里面）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Markdown } from '@/components/ai/Markdown'
import { STREAM_WORD_CLASS, segmentWords } from './streamMarkdown'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

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

const render = (text: string, streaming: boolean) =>
  act(async () => root.render(<Markdown text={text} streaming={streaming} />))
const words = () => Array.from(host.querySelectorAll<HTMLElement>(`span.${STREAM_WORD_CLASS}`))

describe('segmentWords', () => {
  it('中文按词典切，不是整段一个片段', () => {
    const pieces = segmentWords('正在改写脚本').filter((p) => !p.space)
    expect(pieces.length).toBeGreaterThanOrEqual(2)
    expect(pieces.map((p) => p.text).join('')).toBe('正在改写脚本')
  })

  it('标点贴在前一个词上；空白是独立片段且不算词', () => {
    const pieces = segmentWords('Hello, world.')
    expect(pieces).toEqual([
      { text: 'Hello,', space: false },
      { text: ' ', space: true },
      { text: 'world.', space: false },
    ])
  })

  it('拼回去与原文逐字相同（切词不丢字、不加字）', () => {
    const src = '图例移到 upper left，字号 8 pt。\n\n第二段 `code` 与 **粗体**'
    expect(segmentWords(src).map((p) => p.text).join('')).toBe(src)
  })
})

describe('Markdown streaming', () => {
  it('streaming 时每个词一个 span；非 streaming 一个都没有', async () => {
    await render('Hello world', true)
    expect(words().map((w) => w.textContent)).toEqual(['Hello', 'world'])
    await render('Hello world', false)
    expect(words()).toHaveLength(0)
    expect(host.textContent).toBe('Hello world')
  })

  it('文字追加时旧词的节点原样保留，只有新词是新节点', async () => {
    await render('Hello wor', true)
    const before = words()
    expect(before).toHaveLength(2)
    await render('Hello world again', true)
    const after = words()
    expect(after).toHaveLength(3)
    // 同一个 DOM 对象：React 复用了它，挂载动画不会重播
    expect(after[0]).toBe(before[0])
    expect(after[1]).toBe(before[1])
    expect(after[1].textContent).toBe('world')
    expect(before).not.toContain(after[2])
  })

  it('中文流式：多个词各自一个 span', async () => {
    await render('正在改写脚本', true)
    expect(words().length).toBeGreaterThanOrEqual(2)
    expect(host.textContent).toBe('正在改写脚本')
  })

  it('markdown 照样生效：粗体里的词也是 span，空白留在 span 外', async () => {
    await render('a **bold** word', true)
    const strong = host.querySelector('strong')!
    expect(strong.querySelector(`span.${STREAM_WORD_CLASS}`)?.textContent).toBe('bold')
    // 三个词、两个空格：空格是裸文字节点
    expect(words()).toHaveLength(3)
    expect(host.querySelector('p')!.textContent).toBe('a bold word')
  })
})
