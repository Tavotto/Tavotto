/**
 * ⌘S（ADR 0211 第二期 PR 3：**给这一版起名字**）与「排版写成了」事件（ADR 0101 §7）的关系。
 *
 * 改造前 ⌘S 存完（本机那一档）发 `local`，时间线据它打「保存」点。现在工作副本始终自动存着，⌘S 留的是**命名节点**
 * （名字用户自己起，存的时候时间线自己记点），所以 ⌘S 不再发这个事件——发了会在命名节点之外多打一个无名的「保存」点。
 * 这里钉：
 *  - 本机那一份写完（saved / clean / 途中又改过的 dirty）→ 打开命名小框，**不**发 `local`；
 *  - 写没成（冲突 / 失败）→ 不开小框（话先说完），也不发事件；
 *  - 开小框之前先 `saveNow`（把没写的编辑冲刷写完）。
 * 应急回退（后端关了「写进项目」、却开着项目）的 ⌘S 仍是改造前那条路，由 `projectSave.test.ts` 钉着。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const saveNowImpl = vi.fn()
vi.mock('@/store/documentStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/documentStore')>()),
  saveNowWithResult: () => saveNowImpl(),
}))

import { onLayoutSaved } from '@/lib/layoutSaved'
import { runManualSave } from '@/store/actions'
import { useTimelineStore } from '@/store/timelineStore'

let off: (() => void) | null = null
beforeEach(() => useTimelineStore.getState().setNamingOpen(false))
afterEach(() => off?.())

const naming = () => useTimelineStore.getState().namingOpen

describe('runManualSave（⌘S）→ 命名小框', () => {
  it.each([
    [{ state: 'saved', wrote: true }, true],
    [{ state: 'clean', wrote: true }, true],
    [{ state: 'dirty', wrote: true }, true], // 写的途中又改过：按下那一刻的内容已经在盘上，照样可以起名
    [{ state: 'conflict', wrote: false }, false],
    [{ state: 'save_error', wrote: false }, false],
  ])('saveNow 回 %j → 小框开着：%s；从不发 local 事件', async (result, opens) => {
    saveNowImpl.mockResolvedValueOnce(result)
    const seen: string[] = []
    off = onLayoutSaved((via) => seen.push(via))
    await runManualSave()
    expect(naming()).toBe(opens)
    expect(seen).toEqual([])
  })

  it('先冲刷写完、再开小框：saveNow 还没落定时小框不开', async () => {
    let release: (v: { state: string; wrote: boolean }) => void = () => {}
    saveNowImpl.mockImplementationOnce(() => new Promise((res) => (release = res)))
    const pending = runManualSave()
    await Promise.resolve()
    expect(naming()).toBe(false)
    release({ state: 'saved', wrote: true })
    await pending
    expect(naming()).toBe(true)
  })

  it('这一刻的冲刷第一次把工作副本写进项目、建立了绑定：不算「切走了」，照样开小框（不比绑定的文件）', async () => {
    const { setCurrentProjectId } = await import('@/lib/session')
    const { setProjectFile, useDocumentStore } = await import('@/store/documentStore')
    const { setProjectWriteCapability } = await import('@/lib/autosaveProject')
    const { useUiStore } = await import('@/store/uiStore')
    setCurrentProjectId('pj-n')
    setProjectWriteCapability(true)
    useUiStore.getState().setStatus(null as never)
    saveNowImpl.mockImplementationOnce(async () => {
      setProjectFile({ projectId: 'pj-n', name: 'a', file: 'tavottofile/a.json', revision: 'r', dirty: false }, useDocumentStore.getState().documentId)
      return { state: 'saved', wrote: true }
    })
    await runManualSave()
    expect(naming()).toBe(true)
    expect(useUiStore.getState().statusTone).not.toBe('error')
    setProjectWriteCapability(false)
    setProjectFile(null)
    setCurrentProjectId(null)
  })

  it('存的途中换了项目 / 排版：不对此刻开着的那份开小框（话说完，不替用户给别的排版起名）', async () => {
    const { setCurrentProjectId } = await import('@/lib/session')
    setCurrentProjectId('pj-x')
    saveNowImpl.mockImplementationOnce(async () => {
      useTimelineStore.getState().clear()
      setCurrentProjectId('pj-y')
      return { state: 'saved', wrote: true }
    })
    // 有项目 + 开关没开 = 应急回退那条路（改造前的行为，另有用例）；这里只验证「换了项目不会开小框」
    await runManualSave()
    expect(naming()).toBe(false)
    setCurrentProjectId(null)
  })
})
