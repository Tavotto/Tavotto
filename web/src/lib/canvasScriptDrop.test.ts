/**
 * 编辑器里把 .py 拖到画布上的纯判据：判据的主语是**交给 `run` / `degrade` 的东西**（哪个脚本、哪张画布、落点），
 * 以及项目相对路径的换算（项目外 / 不是脚本一律 null，不猜）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createCanvasDropPairer, projectRelativeScript } from './canvasScriptDrop'

describe('projectRelativeScript', () => {
  it.each([
    ['/Users/me/paper', '/Users/me/paper/fig5.py', 'fig5.py'],
    ['/Users/me/paper/', '/Users/me/paper/plots/fig5.py', 'plots/fig5.py'],
    ['C:\\Users\\me\\paper', 'c:\\users\\me\\paper\\Fig5.PY', 'Fig5.PY'],
    ['\\\\server\\share\\paper', '\\\\SERVER\\share\\paper\\a.py', 'a.py'],
    ['/Users/me/论文 数据', '/Users/me/论文 数据/图5.py', '图5.py'],
  ])('%s + %s → %s', (root, path, rel) => {
    expect(projectRelativeScript(root, path)).toBe(rel)
  })

  it.each([
    ['/Users/me/paper', '/Users/me/paper2/fig5.py'], // 前缀相同、不是同一个目录
    ['/Users/me/paper', '/Users/me/other/fig5.py'],
    ['/Users/Me/paper', '/Users/me/paper/fig5.py'], // macOS / Linux 路径按字面比，不折叠大小写
    ['/Users/me/paper', '/Users/me/paper/data.csv'],
    ['/Users/me/paper', '/Users/me/paper/../x/fig5.py'],
    ['/Users/me/paper', '/Users/me/paper'],
  ])('%s + %s → null', (root, path) => {
    expect(projectRelativeScript(root, path)).toBeNull()
  })
})

describe('createCanvasDropPairer', () => {
  afterEach(() => vi.useRealTimers())
  const place = { canvasId: 'c1', at: { x: 10, y: 20 } }

  it('DOM 先到、壳后到：配成一对，用壳的路径与 DOM 的画布 / 落点', () => {
    vi.useFakeTimers()
    const run = vi.fn()
    const degrade = vi.fn()
    const p = createCanvasDropPairer<string>({ graceMs: 1000, run })
    p.dom(place, degrade)
    p.native('/a/fig.py')
    vi.advanceTimersByTime(2000)
    expect(run).toHaveBeenCalledExactlyOnceWith('/a/fig.py', place)
    expect(degrade).not.toHaveBeenCalled()
  })

  it('壳先到、DOM 后到：同样配成一对', () => {
    vi.useFakeTimers()
    const run = vi.fn()
    const p = createCanvasDropPairer<string>({ graceMs: 1000, run })
    p.native('/a/fig.py')
    vi.advanceTimersByTime(500)
    p.dom(place, () => {})
    expect(run).toHaveBeenCalledExactlyOnceWith('/a/fig.py', place)
  })

  it('只有 DOM（拿不到路径的宿主）：等满了才降级，且只降级一次', () => {
    vi.useFakeTimers()
    const run = vi.fn()
    const degrade = vi.fn()
    const p = createCanvasDropPairer<string>({ graceMs: 1000, run })
    p.dom(place, degrade)
    vi.advanceTimersByTime(999)
    expect(degrade).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(degrade).toHaveBeenCalledOnce()
    expect(run).not.toHaveBeenCalled()
  })

  it('只有壳的事件（文件放在页面别处，例如附件区）：什么都不运行；之后迟到的 DOM 也配不上它', () => {
    vi.useFakeTimers()
    const run = vi.fn()
    const degrade = vi.fn()
    const p = createCanvasDropPairer<string>({ graceMs: 1000, run })
    p.native('/a/fig.py')
    vi.advanceTimersByTime(1500)
    expect(run).not.toHaveBeenCalled()
    p.dom(place, degrade)
    vi.advanceTimersByTime(1500)
    expect(run).not.toHaveBeenCalled()
    expect(degrade).toHaveBeenCalledOnce()
  })
})
