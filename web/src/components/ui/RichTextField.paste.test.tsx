/**
 * 没有焦点的富文本框不接粘贴：在画布上点选带文字的图内元素时，属性页的文字框抢一下焦点并全选、随后焦点还给页面，
 * 框里留着残留选区；浏览器把随后的 ⌘V 派给它，剪贴板里的对象负载（JSON）就被当成文字写进了元素内容。
 * 有焦点时照常粘贴（用户真在框里打字）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'
import { annotationCodec } from '@/lib/richEditor'
import { RichTextField } from './RichTextField'

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rt_paste')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const pasteOn = (el: HTMLElement, text: string) => {
  const e = new Event('paste', { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown }
  e.clipboardData = { getData: (t: string) => (t === 'text/plain' ? text : ''), types: ['text/plain'], files: [], items: [] }
  el.dispatchEvent(e)
  return e
}

describe('RichTextField 粘贴', () => {
  it('没有焦点时不接、不让浏览器默认插入；有焦点时照常', async () => {
    const onChange = vi.fn()
    act(() =>
      root.render(<RichTextField aria-label="t" value="Title" codec={annotationCodec(undefined)} onChange={onChange} />),
    )
    const el = container.querySelector<HTMLElement>('[data-rich-text]')!
    const idle = pasteOn(el, '{"magic":"tavotto/objects@1"}')
    expect(idle.defaultPrevented).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
    expect(el.textContent).toBe('Title')

    await act(async () => el.focus())
    const live = pasteOn(el, 'abc')
    // 有焦点：交给 ProseMirror 的正常粘贴（它自己处理并阻止默认），我们的拦截不介入
    expect(live.defaultPrevented).toBe(true)
    expect(el.textContent).not.toBe('Title')
  })
})
