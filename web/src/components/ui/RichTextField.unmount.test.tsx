/**
 * QA-WIN-14 / QA-WIN-28：富文本框带着焦点被卸载（点画布空白取消选择 → 属性页整块消失，浏览器的 blur 来不及触发）
 * 时，必须补上 onBlur——宿主在 onFocus 开的事务靠它收，画布就地编辑的待写内容靠它提交。
 * 否则 `history.editText` 事务悬着，下一个离散动作（画箭头）会被并进同一条历史，一次 ⌘Z 一起撤掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type TextObject } from '@/types/document'
import { annotationCodec } from '@/lib/richEditor'
import { RichTextField, richViewOf } from './RichTextField'

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rt_unmount')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('RichTextField 带焦点卸载', () => {
  it('补一次 onBlur；没拿过焦点的卸载不补', async () => {
    const onBlur = vi.fn()
    const mount = () =>
      act(() =>
        root.render(<RichTextField aria-label="t" value="a" codec={annotationCodec(undefined)} onChange={() => {}} onBlur={onBlur} />),
      )
    mount()
    act(() => root.render(<div />))
    expect(onBlur).not.toHaveBeenCalled()
    mount()
    const el = container.querySelector<HTMLElement>('[data-rich-text]')!
    await act(async () => el.focus())
    act(() => root.render(<div />))
    expect(onBlur).toHaveBeenCalledTimes(1)
  })

  it('正常失焦后再卸载：不重复补', async () => {
    const onBlur = vi.fn()
    act(() =>
      root.render(<RichTextField aria-label="t" value="a" codec={annotationCodec(undefined)} onChange={() => {}} onBlur={onBlur} />),
    )
    const el = container.querySelector<HTMLElement>('[data-rich-text]')!
    await act(async () => {
      el.focus()
      el.blur()
    })
    expect(onBlur).toHaveBeenCalledTimes(1)
    act(() => root.render(<div />))
    expect(onBlur).toHaveBeenCalledTimes(1)
  })

  it('属性页文字框：聚焦开 editText 事务，直接卸载后事务已结束，随后的新动作不并入', async () => {
    const st = useDocumentStore.getState
    st().silent((d) => {
      d.objects.push({ id: 't1', type: 'text', text: '旧', sizePt: 10, bold: false, color: '#111', align: 'left', x: 0, y: 0, w: 40, h: 6 } as TextObject)
    })
    const Host = () => (
      <RichTextField
        aria-label="t"
        value="旧"
        codec={annotationCodec(undefined)}
        onChange={(text) =>
          st().commit('editText' as never, (d) => {
            ;(d.objects[0] as TextObject).text = text
          })
        }
        onFocus={() => st().beginTxn('editText' as never)}
        onBlur={() => st().endTxn()}
      />
    )
    act(() => root.render(<Host />))
    const el = container.querySelector<HTMLElement>('[data-rich-text]')!
    await act(async () => {
      el.focus()
      const v = richViewOf(el)!
      v.dispatch(v.state.tr.insertText('新', 0, v.state.doc.content.size))
    })
    expect(st().txn).not.toBeNull()
    act(() => root.render(<div />))
    expect(st().txn).toBeNull()
    act(() => st().commit('addShape' as never, (d) => { d.objects.push({ id: 'a', type: 'text', text: 'x', sizePt: 1, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 1, h: 1 } as TextObject) }))
    act(() => st().undo())
    // 撤销只回退新动作，文字编辑仍保留
    expect(st().doc.objects.map((o) => o.id)).toEqual(['t1'])
    expect((st().doc.objects[0] as TextObject).text).toBe('新')
  })
})
