import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createDropArbiter,
  dragHasFiles,
  dropTargetOf,
  folderForPath,
  parentDir,
  pathFromFileUri,
  type DropData,
} from './scriptImport'

const drop = (opts: { uris?: string; files?: string[] }): DropData => {
  const types: string[] = []
  if (opts.uris !== undefined) types.push('text/uri-list')
  if (opts.files?.length) types.push('Files')
  return {
    types,
    getData: (f) => (f === 'text/uri-list' ? (opts.uris ?? '') : ''),
    files: (opts.files ?? []).map((name) => ({ name })),
  }
}

describe('parentDir / folderForPath', () => {
  it('.py 取上级目录；目录原样', () => {
    expect(folderForPath('/Users/a/paper/plot.py')).toBe('/Users/a/paper')
    expect(folderForPath('/Users/a/paper')).toBe('/Users/a/paper')
    expect(folderForPath('/Users/a/paper/')).toBe('/Users/a/paper/')
    expect(folderForPath('C:\\work\\figs\\Plot.PY')).toBe('C:\\work\\figs')
  })

  it('盘符根与 POSIX 根不被削成「当前目录」', () => {
    expect(parentDir('C:\\a.py')).toBe('C:\\')
    expect(parentDir('C:/a.py')).toBe('C:/')
    expect(parentDir('/a.py')).toBe('/')
  })
})

describe('pathFromFileUri', () => {
  it('POSIX、Windows 盘符、UNC、百分号编码', () => {
    expect(pathFromFileUri('file:///Users/a/%E8%AE%BA%E6%96%87%20%E5%9B%BE/plot.py')).toBe(
      '/Users/a/论文 图/plot.py',
    )
    expect(pathFromFileUri('file:///C:/work/plot.py')).toBe('C:/work/plot.py')
    expect(pathFromFileUri('file://server/share/plot.py')).toBe('//server/share/plot.py')
  })

  it('不是 file URI 就不认', () => {
    expect(pathFromFileUri('https://example.com/plot.py')).toBeNull()
    expect(pathFromFileUri('plot.py')).toBeNull()
  })
})

describe('dropTargetOf', () => {
  it('带 file URI：直接得到该打开的目录（跳过注释行）', () => {
    expect(dropTargetOf(drop({ uris: '# comment\r\nfile:///Users/a/paper/plot.py', files: ['plot.py'] }))).toEqual({
      kind: 'path',
      folder: '/Users/a/paper',
    })
    // 放下的是目录：它自己就是项目
    expect(dropTargetOf(drop({ uris: 'file:///Users/a/paper' }))).toEqual({ kind: 'path', folder: '/Users/a/paper' })
  })

  it('只有文件、没有路径（浏览器与桌面壳的常态）：.py 退回选择器，别的说不收', () => {
    expect(dropTargetOf(drop({ files: ['plot.py'] }))).toEqual({ kind: 'no-path', name: 'plot.py' })
    expect(dropTargetOf(drop({ files: ['fig.pdf', 'plot.py'] }))).toEqual({ kind: 'not-script', name: 'fig.pdf' })
    // uri-list 里是网页链接：不算路径，照样看文件
    expect(dropTargetOf(drop({ uris: 'https://x.org/a.py', files: ['a.py'] }))).toEqual({ kind: 'no-path', name: 'a.py' })
  })

  it('什么文件都没有', () => {
    expect(dropTargetOf(drop({}))).toEqual({ kind: 'none' })
  })

  it('dragover 只看 types', () => {
    expect(dragHasFiles(['Files'])).toBe(true)
    expect(dragHasFiles(['text/uri-list'])).toBe(true)
    expect(dragHasFiles(['text/plain'])).toBe(false)
  })
})

describe('createDropArbiter：同一次放下的两条路（页面 drop / 壳的系统拖放）', () => {
  afterEach(() => vi.useRealTimers())

  it('壳的事件先到：随后的页面 drop 不降级', () => {
    vi.useFakeTimers()
    const a = createDropArbiter({ graceMs: 1500 })
    const run = vi.fn()
    const fallback = vi.fn()
    a.native(run)
    a.dom(fallback)
    vi.advanceTimersByTime(5000)
    expect(run).toHaveBeenCalledTimes(1)
    expect(fallback).not.toHaveBeenCalled()
  })

  it('页面 drop 先到：等待期内壳的事件到了，降级取消', () => {
    vi.useFakeTimers()
    const a = createDropArbiter({ graceMs: 1500 })
    const fallback = vi.fn()
    a.dom(fallback)
    vi.advanceTimersByTime(1000)
    a.native(() => {})
    vi.advanceTimersByTime(5000)
    expect(fallback).not.toHaveBeenCalled()
  })

  it('壳一直没发（旁听没装上 / 这次没路径）：等满才降级，只降一次', () => {
    vi.useFakeTimers()
    const a = createDropArbiter({ graceMs: 1500 })
    const fallback = vi.fn()
    a.dom(fallback)
    vi.advanceTimersByTime(1499)
    expect(fallback).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fallback).toHaveBeenCalledTimes(1)
  })

  it('很久以前的壳事件不算数；dispose 清掉等待', () => {
    vi.useFakeTimers()
    let t = 0
    const a = createDropArbiter({ graceMs: 1500, now: () => t })
    a.native(() => {})
    t = 10_000
    const fallback = vi.fn()
    a.dom(fallback)
    a.dispose()
    vi.advanceTimersByTime(5000)
    expect(fallback).not.toHaveBeenCalled()
    a.dom(fallback)
    vi.advanceTimersByTime(1500)
    expect(fallback).toHaveBeenCalledTimes(1)
  })
})
