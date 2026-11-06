/**
 * Windows（WebView2）上把页面接受了的系统文件放下转给壳（ADR 0092 修订 2026-11-03）。
 *
 * 钉四件事：有 `chrome.webview.postMessageWithAdditionalObjects` 才装；只转带文件的 drop、转的是那一份
 * FileList 与 `DROP_MESSAGE` 的 JSON 字符串；只装一次（主页与画布都订阅也不会一次放下发两条）；没有这条桥（macOS / 浏览器 /
 * 旧运行时）什么都不装、也不抛。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DROP_MESSAGE, ensureDropForwarder, resetDropForwarderForTests } from './desktop'

type W = Window & { chrome?: unknown }

function fakeDrop(files: File[]): Event {
  const ev = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'dataTransfer', { value: { files: Object.assign([...files], { item: (i: number) => files[i] }) } })
  return ev
}

afterEach(() => {
  resetDropForwarderForTests()
  delete (window as W).chrome
})

describe('ensureDropForwarder', () => {
  it('用能通过 wry TryGetWebMessageAsString 的 JSON 字符串转发，保留原 FileList', () => {
    const post = vi.fn()
    ;(window as W).chrome = { webview: { postMessageWithAdditionalObjects: post } }
    ensureDropForwarder()
    const file = new File(['print(1)'], '我的 图.py')
    const drop = fakeDrop([file]) as DragEvent
    document.body.dispatchEvent(drop)
    expect(post).toHaveBeenCalledTimes(1)
    const [message, objects] = post.mock.calls[0]
    // WebView2 的对象消息会让 wry 的 TryGetWebMessageAsString 返回 E_INVALIDARG；
    // 只 mock post 被调过会漏掉这条边界。壳收到的字符串应能直接解出约定的标记。
    expect(typeof message).toBe('string')
    expect(JSON.parse(message as string)).toEqual(DROP_MESSAGE)
    expect(objects).toBe(drop.dataTransfer!.files)
    expect(Array.from(objects as ArrayLike<File>)).toEqual([file])
  })

  it('没有文件的 drop（页面内部拖动）不转', () => {
    const post = vi.fn()
    ;(window as W).chrome = { webview: { postMessageWithAdditionalObjects: post } }
    ensureDropForwarder()
    document.body.dispatchEvent(fakeDrop([]))
    expect(post).not.toHaveBeenCalled()
  })

  it('主页与画布各订阅一次也只装一个监听：一次放下只发一条', () => {
    const post = vi.fn()
    ;(window as W).chrome = { webview: { postMessageWithAdditionalObjects: post } }
    ensureDropForwarder()
    ensureDropForwarder()
    document.body.dispatchEvent(fakeDrop([new File([''], 'a.py')]))
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('页面的 drop 处理先 stopPropagation 也照样转（捕获阶段在 window 上）', () => {
    const post = vi.fn()
    ;(window as W).chrome = { webview: { postMessageWithAdditionalObjects: post } }
    ensureDropForwarder()
    const stop = (e: Event) => e.stopPropagation()
    document.body.addEventListener('drop', stop)
    try {
      document.body.dispatchEvent(fakeDrop([new File([''], 'a.py')]))
    } finally {
      document.body.removeEventListener('drop', stop)
    }
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('没有这条桥（macOS / 浏览器 / 旧运行时）什么都不装', () => {
    ;(window as W).chrome = { webview: {} }
    expect(() => ensureDropForwarder()).not.toThrow()
    // 桥后来才出现也不会被已经「装过」挡住：上面那次没装
    const post = vi.fn()
    ;(window as W).chrome = { webview: { postMessageWithAdditionalObjects: post } }
    ensureDropForwarder()
    document.body.dispatchEvent(fakeDrop([new File([''], 'a.py')]))
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('桥抛异常不影响页面（运行时太旧）', () => {
    ;(window as W).chrome = {
      webview: {
        postMessageWithAdditionalObjects: () => {
          throw new Error('not supported')
        },
      },
    }
    ensureDropForwarder()
    expect(() => document.body.dispatchEvent(fakeDrop([new File([''], 'a.py')]))).not.toThrow()
  })
})
