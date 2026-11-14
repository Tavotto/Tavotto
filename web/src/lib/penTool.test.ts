/**
 * 钢笔状态机（`lib/penTool.penReduce`，ADR 0185 M1）的每一个转移。纯函数，判据的主语是「这次事件之后的状态与命令」。
 */
import { describe, expect, it } from 'vitest'
import { PEN_IDLE, penPreview, penReduce, wouldClose, type PenEvent, type PenState } from './penTool'

const OPTS = { dragTolMm: 0.5, closeTolMm: 1 }
const run = (events: PenEvent[], from: PenState = PEN_IDLE) => {
  let s = from
  const commits = []
  for (const ev of events) {
    const r = penReduce(s, ev, OPTS)
    s = r.state
    if (r.commit) commits.push(r.commit)
  }
  return { s, commits }
}
const click = (x: number, y: number): PenEvent[] => [
  { type: 'down', at: [x, y] },
  { type: 'up', at: [x, y] },
]

describe('penReduce', () => {
  it('idle + 按下 → 开始画：一个尖角点，按着', () => {
    const { s } = run([{ type: 'down', at: [1, 2] }])
    expect(s.kind).toBe('drawing')
    if (s.kind !== 'drawing') return
    expect(s.nodes).toEqual([{ p: [1, 2], in: [1, 2], out: [1, 2], smooth: false }])
    expect(s.press).toMatchObject({ index: 0, dragged: false, closing: false })
  })

  it('按着挪不到拖动阈值：还是尖角', () => {
    const { s } = run([{ type: 'down', at: [0, 0] }, { type: 'move', at: [0.3, 0] }])
    expect(s.kind === 'drawing' && s.nodes[0].smooth).toBe(false)
  })

  it('按着拖：平滑点，出手柄跟指针、入手柄对称', () => {
    const { s } = run([{ type: 'down', at: [0, 0] }, { type: 'move', at: [3, 4] }])
    if (s.kind !== 'drawing') throw new Error('没在画')
    expect(s.nodes[0]).toEqual({ p: [0, 0], in: [-3, -4], out: [3, 4], smooth: true })
    expect(s.press?.dragged).toBe(true)
  })

  it('拖的时候按 ⌥：只动出手柄，入手柄不动、变尖角', () => {
    const { s } = run([
      { type: 'down', at: [0, 0] },
      { type: 'move', at: [3, 4] },
      { type: 'move', at: [5, 0], alt: true },
    ])
    if (s.kind !== 'drawing') throw new Error('没在画')
    expect(s.nodes[0]).toEqual({ p: [0, 0], in: [-3, -4], out: [5, 0], smooth: false })
  })

  it('松手：不再按着；之后的移动只挪橡皮筋，不改点', () => {
    const { s } = run([...click(0, 0), { type: 'move', at: [7, 7] }])
    if (s.kind !== 'drawing') throw new Error('没在画')
    expect(s.press).toBeNull()
    expect(s.cursor).toEqual([7, 7])
    expect(s.nodes).toHaveLength(1)
    expect(penPreview(s, OPTS)?.rubber).toEqual([[0, 0], [7, 7]])
  })

  it('再按一下（不在起点）：追加一个点', () => {
    const { s } = run([...click(0, 0), ...click(10, 0)])
    expect(s.kind === 'drawing' && s.nodes.map((n) => n.p)).toEqual([[0, 0], [10, 0]])
  })

  it('点到起点（≥ 2 个点）：闭合并完成，回到 idle', () => {
    const { s, commits } = run([...click(0, 0), ...click(10, 0), ...click(10, 10), { type: 'down', at: [0.4, 0.3] }])
    expect(commits).toHaveLength(0) // 按下还没完成，松手才算
    const r = penReduce(s, { type: 'up', at: [0.4, 0.3] }, OPTS)
    expect(r.state).toEqual(PEN_IDLE)
    expect(r.commit?.closed).toBe(true)
    expect(r.commit?.nodes.map((n) => n.p)).toEqual([[0, 0], [10, 0], [10, 10]])
  })

  it('只有一个点时点回起点：不闭合，是再加一个点', () => {
    const s = run(click(0, 0)).s
    expect(wouldClose(s, [0, 0], OPTS)).toBe(false)
    const after = run(click(0.2, 0), s).s
    expect(after.kind === 'drawing' && after.nodes).toHaveLength(2)
  })

  it('停在起点上：预览给「再点就闭合」的提示', () => {
    const s = run([...click(0, 0), ...click(10, 0), { type: 'move', at: [0.2, 0.2] }]).s
    expect(penPreview(s, OPTS)?.closeHint).toBe(true)
  })

  it('结束（Enter / Esc / 换工具）：≥ 2 个点 → 开放路径；回到 idle', () => {
    const { s, commits } = run([...click(0, 0), ...click(10, 0), { type: 'finish' }])
    expect(s).toEqual(PEN_IDLE)
    expect(commits).toEqual([
      {
        closed: false,
        nodes: [
          { p: [0, 0], in: [0, 0], out: [0, 0], smooth: false },
          { p: [10, 0], in: [10, 0], out: [10, 0], smooth: false },
        ],
      },
    ])
  })

  it('结束时不足两个点：什么都不留', () => {
    const { s, commits } = run([...click(0, 0), { type: 'finish' }])
    expect(s).toEqual(PEN_IDLE)
    expect(commits).toHaveLength(0)
  })

  it('取消：丢掉画到一半的，不出命令', () => {
    const { s, commits } = run([...click(0, 0), ...click(5, 5), { type: 'cancel' }])
    expect(s).toEqual(PEN_IDLE)
    expect(commits).toHaveLength(0)
  })

  it('idle 时的移动 / 松手 / 结束：什么都不发生', () => {
    for (const ev of [{ type: 'move', at: [1, 1] }, { type: 'up', at: [1, 1] }, { type: 'finish' }] as PenEvent[]) {
      const r = penReduce(PEN_IDLE, ev, OPTS)
      expect(r.state).toEqual(PEN_IDLE)
      expect(r.commit).toBeUndefined()
    }
  })

  it('已经按着又来一次按下（漏了 up）：忽略', () => {
    const s = run([{ type: 'down', at: [0, 0] }]).s
    expect(penReduce(s, { type: 'down', at: [9, 9] }, OPTS).state).toBe(s)
  })

  it('不改入参（纯函数）', () => {
    const s = run(click(0, 0)).s
    const snap = JSON.stringify(s)
    penReduce(s, { type: 'down', at: [3, 3] }, OPTS)
    expect(JSON.stringify(s)).toBe(snap)
  })
})
