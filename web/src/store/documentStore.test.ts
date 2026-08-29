import { formatMessage, literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { emptyProject } from '@/types/document'
import type { TextObject } from '@/types/document'
import {
  flushAutosave,
  readAutosaveDoc,
  restoreSession,
  startAutosave,
  useDocumentStore,
} from './documentStore'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/**
 * 模拟后端 /api/autosave 槽位（PUT/GET/DELETE），其余请求 404。
 *
 * 修订号用「内容长度 + 前 8 个字符」现算：它只需要满足"内容变了它就变"，
 * 而真后端算的是 sha256。用例断言的是**基线怎么传怎么推进**，不是 hash 算法。
 */
const diskSlots = new Map<string, string>()
const revisionOf = (body: string) => `r${body.length}-${body.slice(0, 8).replace(/\W/g, '')}`
const baseFetchImpl = (async (url: unknown, init?: RequestInit) => {
  const m = String(url).match(/\/api\/autosave\/([^/?]+)/)
  if (m) {
    const id = decodeURIComponent(m[1])
    if (init?.method === 'PUT') {
      const body = String(init.body)
      diskSlots.set(id, body)
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: revisionOf(body) }), {
        status: 200,
      })
    }
    if (init?.method === 'DELETE') {
      diskSlots.delete(id)
      return new Response('{"ok":true}', { status: 200 })
    }
    const v = diskSlots.get(id)
    return new Response(v ?? '{}', {
      status: v ? 200 : 404,
      headers: v ? { 'X-Tavotto-Revision': revisionOf(v) } : undefined,
    })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch
globalThis.fetch = baseFetchImpl

const tick = () => new Promise((r) => setTimeout(r, 10))

const reset = async () => {
  localStorage.clear()
  diskSlots.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_test')
}

describe('多画布数据层', () => {
  beforeEach(reset)

  it('addCanvas 新建并切换；undo 栈按画布隔离', () => {
    const s = () => useDocumentStore.getState()
    s().commit(literal('加字A'), (d) => {
      d.objects.push(text('t1', 'A'))
    })
    const firstId = s().activeCanvasId
    expect(s().past).toHaveLength(1)

    const secondId = s().addCanvas()
    expect(s().activeCanvasId).toBe(secondId)
    expect(s().doc.objects).toHaveLength(0)
    expect(s().past).toHaveLength(0) // 新画布是干净的撤销栈

    s().commit(literal('加字B'), (d) => {
      d.objects.push(text('t2', 'B'))
    })
    s().switchCanvas(firstId)
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t1'])
    expect(s().past.map((e) => formatMessage(e.label))).toEqual(['加字A'])
    expect(formatMessage(s().undo())).toBe('加字A')
    expect(s().doc.objects).toHaveLength(0)

    s().switchCanvas(secondId)
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t2'])
    expect(s().past.map((e) => formatMessage(e.label))).toEqual(['加字B'])
  })

  it('buildProject 汇总激活画布的最新内容', () => {
    const s = () => useDocumentStore.getState()
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', 'x'))
    })
    const pd = s().buildProject()
    expect(pd.schema).toBe(3)
    expect(pd.canvases[0].objects).toHaveLength(1)
    expect(pd.activeCanvasId).toBe(s().activeCanvasId)
  })

  it('duplicateCanvas 换新全部对象/成组 id', () => {
    const s = () => useDocumentStore.getState()
    s().commit(literal('加组'), (d) => {
      d.objects.push({ ...text('t1', 'x'), groupId: 'g1' })
      d.objects.push({ ...text('t2', 'y'), groupId: 'g1' })
      d.layoutGroups = [{ id: 'g1', kind: 'row', order: ['t1', 't2'], gap: 4, align: 'center' }]
    })
    const nid = s().duplicateCanvas(s().activeCanvasId)!
    const copy = s().canvases.find((c) => c.id === nid)!
    expect(copy.objects).toHaveLength(2)
    expect(copy.objects.map((o) => o.id)).not.toContain('t1')
    expect(copy.layoutGroups).toHaveLength(1)
    expect(copy.layoutGroups![0].id).not.toBe('g1')
    expect(copy.layoutGroups![0].order).toEqual(copy.objects.map((o) => o.id))
    expect(copy.objects[0].groupId).toBe(copy.layoutGroups![0].id)
  })

  it('deleteCanvas 守住最后一张；删除激活画布切到邻居', () => {
    const s = () => useDocumentStore.getState()
    expect(s().deleteCanvas(s().activeCanvasId)).toBe(false)
    const secondId = s().addCanvas()
    expect(s().deleteCanvas(secondId)).toBe(true)
    expect(s().canvases).toHaveLength(1)
  })

  it('reorderCanvases 移动显示顺序', () => {
    const s = () => useDocumentStore.getState()
    const first = s().activeCanvasId
    const second = s().addCanvas()
    s().reorderCanvases(1, 0)
    expect(s().canvases.map((c) => c.id)).toEqual([second, first])
  })

  it('标签：打开/关闭/重排；关闭激活标签切到邻居；最后一个不可关', () => {
    const s = () => useDocumentStore.getState()
    const first = s().activeCanvasId
    expect(s().openTabs).toEqual([first])
    expect(s().closeCanvasTab(first)).toBe(false)

    const second = s().addCanvas()
    const third = s().addCanvas()
    expect(s().openTabs).toEqual([first, second, third])

    s().reorderTabs(2, 0)
    expect(s().openTabs).toEqual([third, first, second])

    // 关闭激活标签（third）→ 切到邻居 first；画布仍在
    expect(s().activeCanvasId).toBe(third)
    expect(s().closeCanvasTab(third)).toBe(true)
    expect(s().activeCanvasId).toBe(first)
    expect(s().openTabs).toEqual([first, second])
    expect(s().canvases.map((c) => c.id)).toContain(third)
  })

  it('标签按 documentId 持久化，switchDocument 恢复', async () => {
    const s = () => useDocumentStore.getState()
    const second = s().addCanvas()
    const pd = s().buildProject()
    const docId = s().documentId
    // 换走再换回：openTabs 从本机恢复
    await s().switchDocument(emptyProject(), 'd_other')
    expect(s().openTabs).toHaveLength(1)
    await s().switchDocument(pd, docId)
    expect(s().openTabs).toEqual([pd.canvases[0].id, second])
  })

  it('deleteCanvas 一并关掉对应标签', () => {
    const s = () => useDocumentStore.getState()
    const second = s().addCanvas()
    expect(s().openTabs).toContain(second)
    s().deleteCanvas(second)
    expect(s().openTabs).not.toContain(second)
  })

  it('画布列表的结构性改动也要置 dirty —— 它们只动 canvases，不动 doc', () => {
    const s = () => useDocumentStore.getState()
    const first = s().activeCanvasId
    s().addCanvas()                              // 切到新画布，first 变成非激活
    const stop = startAutosave()
    try {
      // 重命名**非激活**画布：走的是 set({ canvases }) 那条分支，doc 不变
      useDocumentStore.setState({ dirty: false })
      s().renameCanvas(first, '改过的名字')
      expect(s().canvases.find((c) => c.id === first)!.name).toBe('改过的名字')
      expect(s().dirty).toBe(true)

      // 调整画布顺序同理：doc 一个字节没动
      useDocumentStore.setState({ dirty: false })
      s().reorderCanvases(0, 1)
      expect(s().dirty).toBe(true)
    } finally {
      stop()
    }
  })

  it('从磁盘恢复不是一次编辑 —— 打开文档不该把它重写一遍', async () => {
    // 磁盘上先有一份文档，且它就是「上次打开的那个」
    const s = () => useDocumentStore.getState()
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', 'x'))
    })
    expect(flushAutosave()).toBe('saved')
    await tick()
    const docId = s().documentId

    // 换到别处，再像启动那样恢复回来（订阅先于恢复完成挂上，与 App.tsx 同序）
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    localStorage.setItem('tavotto.currentDoc', docId)   // switchDocument 会覆盖它
    const stop = startAutosave()
    try {
      expect(await restoreSession()).toBe(true)
      expect(s().documentId).toBe(docId)
      // 恢复自己写的就是 dirty:false，订阅不许把它翻回来：否则一开文档就
      // 在 1 秒后原样重写一遍（带新的 updatedAt），另一个标签页开着同一份
      // 时还会撞出 stale_write
      expect(s().dirty).toBe(false)
    } finally {
      stop()
    }
  })

  it('自动保存：磁盘落 schema 3，成功后本机副本清空', async () => {
    const s = () => useDocumentStore.getState()
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', 'x'))
    })
    expect(flushAutosave()).toBe('saved')
    await tick()
    const disk = JSON.parse(diskSlots.get(s().documentId)!)
    expect(disk.schema).toBe(3)
    expect(disk.canvases[0].objects[0].id).toBe('t1')
    // 磁盘写成功 → localStorage 不再保存文档主体
    expect(localStorage.getItem(`tavotto.autosave.${s().documentId}`)).toBeNull()
  })

  it('schema 2 旧本机槽位读取时自动迁移并转正到磁盘', async () => {
    localStorage.setItem(
      'tavotto.autosave.d_old',
      JSON.stringify({ schema: 2, name: 'old', page: { w: 80, h: 60 },
                       objects: [text('t9', 'legacy')], guides: [] }),
    )
    const pd = (await readAutosaveDoc('d_old')).doc!
    expect(pd.schema).toBe(3)
    expect(pd.canvases[0].objects[0].id).toBe('t9')
    expect(pd.canvases[0].page).toEqual({ w: 80, h: 60 })
    await tick()
    expect(JSON.parse(diskSlots.get('d_old')!).schema).toBe(3)
  })

  it('本机副本更新时**打开磁盘那份**，副本挪进恢复槽位等用户裁决', async () => {
    const older = { ...emptyProject(), updatedAt: 100 }
    const newer = { ...emptyProject(), updatedAt: 200 }
    diskSlots.set('d_x', JSON.stringify(older))
    localStorage.setItem('tavotto.autosave.d_x', JSON.stringify(newer))
    const { doc, notice } = await readAutosaveDoc('d_x')
    // 主文档照常打开（磁盘那份），**不再让本机副本静默取胜**
    expect(doc!.updatedAt).toBe(100)
    expect(notice).toMatchObject({ kind: 'recovery', docId: 'd_x' })
    expect(notice?.kind === 'recovery' ? notice.summary.savedAt : null).toBe(200)
    // 副本挪走了：原槽位空出来给正常自动保存用，恢复槽位留着
    expect(localStorage.getItem('tavotto.autosave.d_x')).toBeNull()
    expect(localStorage.getItem('tavotto.recovery.d_x')).not.toBeNull()
    // 磁盘一个字节没动：恢复前主文件必须原样
    expect(JSON.parse(diskSlots.get('d_x')!).updatedAt).toBe(100)
  })

  it('本机副本不比磁盘新 = 陈旧残留，直接清掉，不打扰用户', async () => {
    const disk = { ...emptyProject(), updatedAt: 300 }
    const stale = { ...emptyProject(), updatedAt: 300 }
    diskSlots.set('d_y', JSON.stringify(disk))
    localStorage.setItem('tavotto.autosave.d_y', JSON.stringify(stale))
    const { doc, notice } = await readAutosaveDoc('d_y')
    expect(doc!.updatedAt).toBe(300)
    expect(notice).toBeNull()
    expect(localStorage.getItem('tavotto.autosave.d_y')).toBeNull()
    expect(localStorage.getItem('tavotto.recovery.d_y')).toBeNull()
  })
})

describe('文字编辑事务', () => {
  beforeEach(reset)

  const s = () => useDocumentStore.getState()

  /** 复刻 TextSection 的 textarea：逐字符 onChange → updateObjects → commit */
  const type = (chars: string) => {
    for (let i = 1; i <= chars.length; i++) {
      s().commit(literal('编辑文字'), (d) => {
        const o = d.objects.find((x) => x.id === 't1')
        if (o?.type === 'text') o.text = chars.slice(0, i)
      })
    }
  }

  const currentText = () => (s().doc.objects[0] as TextObject).text

  it('事务期间连打 5 个字只留一条历史，撤销一次整段回到编辑前', () => {
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', '原'))
    })
    const before = s().past.length

    s().beginTxn(literal('编辑文字'))
    type('ABCDE')
    expect(s().past).toHaveLength(before) // 事务未提交前一条都不进历史
    expect(currentText()).toBe('ABCDE') // 但文档已即时更新
    s().endTxn()

    expect(s().past).toHaveLength(before + 1)
    expect(formatMessage(s().past.at(-1)!.label)).toBe('编辑文字')
    expect(formatMessage(s().undo())).toBe('编辑文字')
    expect(currentText()).toBe('原') // 退到编辑前，不是倒数第二个字
  })

  it('对照：不开事务逐字符 commit 就是 5 条历史，撤销一次只退一个字', () => {
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', '原'))
    })
    const before = s().past.length

    type('ABCDE')

    expect(s().past).toHaveLength(before + 5)
    expect(formatMessage(s().undo())).toBe('编辑文字')
    expect(currentText()).toBe('ABCD')
  })
})

/**
 * 事务的反向补丁是**前插**累积的（数组末尾才是最早那条），compress() 一度按数组
 * 顺序取第一条 = 最新那条，于是所有事务撤销都只退到倒数第二次更新：拖动松手后
 * ⌘Z 只回退最后一帧（离落点常常就一两个像素，看着像「撤销没反应」）。
 */
describe('事务压缩：撤销回到事务开始前', () => {
  beforeEach(reset)

  const s = () => useDocumentStore.getState()
  const at = () => s().doc.objects[0]

  const seed = () =>
    s().commit(literal('加字'), (d) => {
      d.objects.push(text('t1', 'x'))
    })

  it('拖动：连续 5 次 txnUpdate 后撤销回到起点，不是最后一帧', () => {
    seed()
    s().beginTxn(literal('移动对象'))
    for (const x of [10, 20, 30, 40, 50]) {
      s().txnUpdate((d) => {
        d.objects[0].x = x
      })
    }
    s().endTxn()
    expect(at().x).toBe(50)

    expect(formatMessage(s().undo())).toBe('移动对象')
    expect(at().x).toBe(0)
    // 压缩不该弄丢重做：正向仍是最后一次的值
    s().redo()
    expect(at().x).toBe(50)
  })

  it('同时改 x/y：两个 key 各自回到起点', () => {
    seed()
    s().beginTxn(literal('移动对象'))
    for (const v of [10, 20, 30]) {
      s().txnUpdate((d) => {
        d.objects[0].x = v
        d.objects[0].y = v * 2
      })
    }
    s().endTxn()
    expect([at().x, at().y]).toEqual([30, 60])

    s().undo()
    expect([at().x, at().y]).toEqual([0, 0])
  })

  it('含增删的事务不压缩，全量反向补丁照样退回事务开始前', () => {
    seed()
    const before = s().past.length
    s().beginTxn(literal('移动并加字'))
    s().txnUpdate((d) => {
      d.objects[0].x = 10
    })
    s().txnUpdate((d) => {
      d.objects.push(text('t2', 'y')) // 有 add，compress 直接放弃压缩
    })
    s().txnUpdate((d) => {
      d.objects[0].x = 20
    })
    s().endTxn()
    expect(s().past).toHaveLength(before + 1)

    s().undo()
    expect(at().x).toBe(0)
    expect(s().doc.objects).toHaveLength(1)
  })

  it('discard 路径：不进历史，文档立刻回到事务开始前', () => {
    seed()
    const before = s().past.length
    s().beginTxn(literal('移动对象'))
    for (const x of [10, 20, 30]) {
      s().txnUpdate((d) => {
        d.objects[0].x = x
      })
    }
    s().endTxn({ discard: true })

    expect(at().x).toBe(0) // 全量按序应用，最早那条最后落地
    expect(s().past).toHaveLength(before)
    expect(s().txn).toBeNull()
  })
})

/** 上面那套即答即回的 fetch mock；下面按需换成可控延迟的版本再换回来 */
const baseFetch = globalThis.fetch

describe('自动保存磁盘写入队列', () => {
  /** 每个 PUT 一发出就挂起，等 releaseNext() 逐个放行——用来制造「在途」窗口 */
  const putLog: { id: string; body: string }[] = []
  /** 挂起中的 PUT resolver，只能逐个放行——直接丢弃会让 diskBusy 永远卡住 */
  const gate: (() => void)[] = []

  const gatedFetch = (async (url: unknown, init?: RequestInit) => {
    const m = String(url).match(/\/api\/autosave\/([^/?]+)/)
    if (m && init?.method === 'PUT') {
      const id = decodeURIComponent(m[1])
      const body = String(init.body)
      putLog.push({ id, body }) // 发出即记，与是否放行无关
      await new Promise<void>((r) => gate.push(r))
      diskSlots.set(id, body)
      return new Response('{"ok":true}', { status: 200 })
    }
    return baseFetch(url as RequestInfo, init)
  }) as typeof fetch

  /** 放行最早挂起的那个 PUT，并等队列推进到下一个 */
  const releaseNext = async () => {
    gate.shift()?.()
    await tick()
  }

  const slot = (id: string) => localStorage.getItem(`tavotto.autosave.${id}`)

  beforeEach(async () => {
    globalThis.fetch = gatedFetch
    await reset()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_a')
    // switchDocument 会替上一个用例的文档再落一次盘，先排干净再开始记账
    while (gate.length) await releaseNext()
    putLog.length = 0
    diskSlots.clear()
    localStorage.clear()
  })

  afterEach(async () => {
    // 排空，别把在途请求漏给下一个用例（diskBusy 是模块级的）
    while (gate.length) await releaseNext()
    globalThis.fetch = baseFetch
  })

  it('不同文档背靠背排队时都能落盘，兜底副本只清写成功的那个', async () => {
    const s = () => useDocumentStore.getState()

    s().commit(literal('A1'), (d) => {
      d.objects.push(text('t1', 'A1'))
    })
    expect(flushAutosave()).toBe('saved') // d_a 第一次 PUT：在途
    await tick()
    expect(putLog).toHaveLength(1)

    s().commit(literal('A2'), (d) => {
      d.objects.push(text('t2', 'A2'))
    })
    expect(flushAutosave()).toBe('saved') // d_a 最新一份：排队
    await tick()
    expect(putLog).toHaveLength(1)

    // 切文档：旧实现在这里把 d_a 排队的那份整个顶掉
    await s().switchDocument(emptyProject(), 'd_b')
    s().commit(literal('B1'), (d) => {
      d.objects.push(text('t3', 'B1'))
    })
    expect(flushAutosave()).toBe('saved') // d_b：排在 d_a 后面
    await tick()
    expect(putLog).toHaveLength(1)
    expect(slot('d_a')).not.toBeNull()
    expect(slot('d_b')).not.toBeNull()

    await releaseNext() // d_a 第一次写成功 → 推进到 d_a 的最新一份
    expect(putLog.map((e) => e.id)).toEqual(['d_a', 'd_a'])
    expect(slot('d_a')).toBeNull() // 只清 d_a 自己的兜底副本
    expect(slot('d_b')).not.toBeNull()

    await releaseNext() // d_a 最新一份写成功 → 推进到 d_b
    expect(putLog.map((e) => e.id)).toEqual(['d_a', 'd_a', 'd_b'])
    expect(slot('d_b')).not.toBeNull() // d_b 还没写成功，兜底副本必须留着

    await releaseNext() // d_b 写成功
    expect(slot('d_b')).toBeNull()

    // 两个文档的最终版本都真的落了盘（旧实现里 d_a 的 t2 从此消失）
    const diskA = JSON.parse(diskSlots.get('d_a')!)
    expect(diskA.canvases[0].objects.map((o: { id: string }) => o.id)).toEqual(['t1', 't2'])
    const diskB = JSON.parse(diskSlots.get('d_b')!)
    expect(diskB.canvases[0].objects.map((o: { id: string }) => o.id)).toEqual(['t3'])
  })

  it('同一文档连续排队只合并成最新一份，不会重复入队', async () => {
    const s = () => useDocumentStore.getState()

    s().commit(literal('A1'), (d) => {
      d.objects.push(text('t1', 'A1'))
    })
    flushAutosave() // 在途
    for (const label of ['A2', 'A3', 'A4']) {
      s().commit(literal(label), (d) => {
        d.objects.push(text(`t_${label}`, label))
      })
      flushAutosave() // 三次都排进同一个 id 的槽位
    }
    await tick()
    expect(putLog).toHaveLength(1)

    await releaseNext()
    expect(putLog.map((e) => e.id)).toEqual(['d_a', 'd_a'])

    await releaseNext() // 队列已空，不该再冒出第三次
    expect(putLog).toHaveLength(2)
    expect(gate).toHaveLength(0)
    const disk = JSON.parse(diskSlots.get('d_a')!)
    expect(disk.canvases[0].objects).toHaveLength(4)
  })
})

/**
 * 同一 documentId 被两个标签页同时开着：后保存的整份覆盖先保存的。
 * 前端带「上次成功落盘时的 updatedAt」当基线，后端发现磁盘更新就回 409
 * stale_write——本窗口这次改动只留在本机兜底副本里，绝不清、绝不静默重试。
 */
describe('自动保存的跨标签页写覆盖', () => {
  const puts: { id: string; base: string | null; rev: string | null }[] = []
  const errors: { id: string; reason: string }[] = []
  const gate: (() => void)[] = []
  /** 下一批 PUT 是否被后端当作过期写挡下 */
  let stale = false

  const conflictFetch = (async (url: unknown, init?: RequestInit) => {
    const m = String(url).match(/\/api\/autosave\/([^/?]+)/)
    if (m && init?.method === 'PUT') {
      const id = decodeURIComponent(m[1])
      const q = new URL(String(url), 'http://t').searchParams
      puts.push({ id, base: q.get('base'), rev: q.get('base_revision') })
      await new Promise<void>((r) => gate.push(r))
      if (stale) {
        return new Response(JSON.stringify({ code: 'stale_write', theirs: 999 }), {
          status: 409,
        })
      }
      diskSlots.set(id, String(init.body))
      return new Response('{"ok":true,"saved_at":1,"revision":"rNEW"}', { status: 200 })
    }
    return baseFetch(url as RequestInfo, init)
  }) as typeof fetch

  const releaseNext = async () => {
    gate.shift()?.()
    await tick()
  }
  const drain = async () => {
    // 先让在途的微任务跑完：每份文档第一次落盘前会先 GET 一次确认磁盘状况
    // （ensureDiskKnown），那一下不经 gate，PUT 要等它回来才发出去。
    await tick()
    while (gate.length) await releaseNext()
  }
  const slot = (id: string) => localStorage.getItem(`tavotto.autosave.${id}`)
  const onError = (ev: Event) => {
    errors.push((ev as CustomEvent<{ id: string; reason: string }>).detail)
  }

  beforeEach(async () => {
    globalThis.fetch = conflictFetch
    stale = false
    localStorage.clear()
    diskSlots.clear()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cc_setup')
    await drain()
    puts.length = 0
    errors.length = 0
    diskSlots.clear()
    localStorage.clear()
    window.addEventListener('tavotto:autosave-error', onError)
  })

  afterEach(async () => {
    stale = false
    await drain() // 别把在途请求漏给下一个用例（diskBusy 是模块级的）
    window.removeEventListener('tavotto:autosave-error', onError)
    globalThis.fetch = baseFetch
  })

  /** 空文档不触发落盘（flushAutosave 判 'empty'），要落盘就得有内容 */
  const seeded = (updatedAt: number) => {
    const pd = emptyProject()
    pd.canvases[0].objects = [text('t0', 'seed')]
    pd.updatedAt = updatedAt
    return pd
  }

  it('读档时以磁盘那份的 updatedAt 为基线，写成功后推进', async () => {
    const s = () => useDocumentStore.getState()
    // 磁盘上已有一份（另一个标签页存的），updatedAt = 4242
    diskSlots.set('d_base', JSON.stringify(seeded(4242)))

    const pd = (await readAutosaveDoc('d_base')).doc!
    expect(pd.updatedAt).toBe(4242)
    await s().switchDocument(pd, 'd_base') // 切进去会立刻落一次盘
    await drain()
    // 两个基线都带上：updatedAt 管跨标签页，内容 hash 管编辑器外的改动
    expect(puts[0]).toMatchObject({ id: 'd_base', base: '4242' })
    expect(puts[0].rev).toBeTruthy()

    const firstWritten = JSON.parse(diskSlots.get('d_base')!).updatedAt as number
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('t1', 'A'))
    })
    flushAutosave()
    await drain()
    // 写成功后基线推进到刚落盘的那一版，下一次带的就是它
    expect(puts[1]).toMatchObject({ id: 'd_base', base: String(firstWritten), rev: 'rNEW' })
  })

  it('首次写不带基线：后端无从校验，兼容旧路径', async () => {
    const s = () => useDocumentStore.getState()
    await s().switchDocument(emptyProject(), 'd_fresh')
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('t1', 'A'))
    })
    flushAutosave()
    await drain()
    // 没有 updatedAt 基线，但**有**修订号基线：`absent` = 「我读过，磁盘上没有」。
    // 少了它，两个标签页同时新建同一份文档时后写的那个会整份盖掉先写的。
    expect(puts[0]).toEqual({ id: 'd_fresh', base: null, rev: 'absent' })
  })

  it('409 stale_write：兜底副本留着、报 stale、队列不死锁', async () => {
    const s = () => useDocumentStore.getState()
    await s().switchDocument(emptyProject(), 'd_stale')
    await drain()
    puts.length = 0
    errors.length = 0

    stale = true
    s().commit(literal('本窗口改一笔'), (d) => {
      d.objects.push(text('t1', 'A'))
    })
    expect(flushAutosave()).toBe('saved') // 在途
    s().commit(literal('再改一笔'), (d) => {
      d.objects.push(text('t2', 'B'))
    })
    flushAutosave() // 排队
    await tick()
    expect(puts).toHaveLength(1)

    await releaseNext() // 第一次被 409 挡下
    expect(errors).toEqual([{ id: 'd_stale', reason: 'stale' }])
    // 本机兜底副本绝不能清：磁盘上没有这份改动，清了就真丢了
    expect(slot('d_stale')).not.toBeNull()
    // 冲突未决 → 排在队列里的那一份**不再往磁盘上撞**。撞也只会再 409 一次，
    // 而每撞一次就多一条"保存失败"，用户以为自己遇到了四个问题。
    // 内容一个字节没丢：它在本机副本里等裁决。
    expect(puts).toHaveLength(1)
    expect(s().saveState).toBe('conflict')
    expect(s().saveIssue).toMatchObject({ kind: 'stale', docId: 'd_stale' })
    expect(diskSlots.has('d_stale')).toBe(false) // 磁盘上对方那份没被覆盖

    // 冲突期间继续编辑：状态不被顶掉，本机副本继续更新，磁盘一次都不碰
    s().commit(literal('冲突期间还在改'), (d) => {
      d.objects.push(text('t3', 'C'))
    })
    flushAutosave()
    await tick()
    expect(s().saveState).toBe('conflict')
    expect(puts).toHaveLength(1)
    expect(JSON.parse(slot('d_stale')!).canvases[0].objects).toHaveLength(3)

    // 写盘链路没被这次冲突堵死：换个文档照样落盘
    stale = false
    await s().switchDocument(emptyProject(), 'd_after')
    s().commit(literal('新文档改一笔'), (d) => {
      d.objects.push(text('t3', 'C'))
    })
    flushAutosave()
    await drain()
    expect(diskSlots.has('d_after')).toBe(true)
  })

  it('普通写盘失败仍报 io，与 stale 分开', async () => {
    const s = () => useDocumentStore.getState()
    await s().switchDocument(emptyProject(), 'd_io')
    await drain()
    errors.length = 0

    const prev = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).includes('/api/autosave/') && init?.method === 'PUT') {
        return new Response('{"error":"磁盘满了"}', { status: 500 })
      }
      return baseFetch(url as RequestInfo, init)
    }) as typeof fetch
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('t1', 'A'))
    })
    flushAutosave()
    await tick()
    globalThis.fetch = prev

    expect(errors).toEqual([{ id: 'd_io', reason: 'io' }])
    expect(slot('d_io')).not.toBeNull()
  })
})
