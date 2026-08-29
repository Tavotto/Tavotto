/**
 * 保存状态机、外部修改冲突、崩溃恢复（Prompt 03 / R-06 / R-08）。
 *
 * 与 `documentStore.test.ts` 的分工：那一份看的是**队列与基线**（谁先写、
 * 带什么基线、兜底副本清不清）；这一份看的是**用户看到的状态**——保存走到
 * 哪一步、卡住的时候卡的是什么、恢复副本什么时候出现、裁决之后落到哪。
 *
 * 全部用可控的假磁盘 + 手动 gate，**没有一处 sleep 等状态自己变**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject } from '@/types/document'
import type { ProjectDocument, TextObject } from '@/types/document'
import {
  discardLocalCopy,
  dismissDocNotice,
  flushAutosave,
  hasUnsavedWork,
  overwriteDisk,
  readAutosaveDoc,
  recoverLocalCopy,
  reloadFromDisk,
  restoreSession,
  saveNow,
  startAutosave,
  useDocumentStore,
} from './documentStore'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const s = () => useDocumentStore.getState()
/**
 * 把在途的微任务放干净。**用微任务而不是 `setTimeout`**：其中一个用例要在
 * 假时钟下走完整条 dirty→saving→saved→clean，而假时钟下 `setTimeout(0)` 不会
 * 自己到期——那样等的不是"异步跑完了"，是"这个用例挂住了"。
 * 假磁盘全是立刻 resolve 的 Promise，微任务足够。
 */
const tick = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}
const slotKey = (id: string) => `tavotto.autosave.${id}`
const recoveryKey = (id: string) => `tavotto.recovery.${id}`

/* --------------------------- 假磁盘 --------------------------- */

/** 一份文档在假磁盘上的样子；`rev` 由内容现算，与真后端的 sha256 同义不同法 */
const disk = new Map<string, string>()
const revOf = (body: string) => `r${body.length}:${body.slice(0, 12).replace(/\W/g, '')}`

/** 下一次 PUT 的处置：正常写 / 409 外部修改 / 500 IO 失败 */
let putMode: 'ok' | 'external' | 'io' = 'ok'
/** PUT 的门：非空时 PUT 挂起，releasePuts() 放行 */
let gatePuts = false
const gates: (() => void)[] = []
const puts: { id: string; rev: string | null; base: string | null }[] = []

const fakeFetch = (async (url: unknown, init?: RequestInit) => {
  const raw = String(url)
  const m = raw.match(/\/api\/autosave\/([^/?]+)/)
  if (!m) return new Response('{}', { status: 404 })
  const id = decodeURIComponent(m[1])
  const q = new URL(raw, 'http://t').searchParams

  if (raw.includes('/summary')) {
    const v = disk.get(id)
    if (!v) return new Response('{}', { status: 404 })
    const parsed = JSON.parse(v) as ProjectDocument
    return new Response(
      JSON.stringify({
        schema: parsed.schema, canvases: parsed.canvases.length,
        objects: parsed.canvases.reduce((n, c) => n + c.objects.length, 0),
        updatedAt: parsed.updatedAt, mtime: parsed.updatedAt,
        name: parsed.project.name, revision: revOf(v),
      }),
      { status: 200 },
    )
  }
  if (init?.method === 'PUT') {
    const body = String(init.body)
    puts.push({ id, rev: q.get('base_revision'), base: q.get('base') })
    if (gatePuts) await new Promise<void>((r) => gates.push(r))
    if (putMode === 'io') return new Response('{"error":"disk full"}', { status: 500 })
    if (putMode === 'external') {
      const v = disk.get(id)
      return new Response(
        JSON.stringify({
          code: 'external_change',
          revision: v ? revOf(v) : null,
          summary: v
            ? { schema: 3, canvases: 1, objects: 7, updatedAt: 999, mtime: 999,
                name: 'theirs', revision: revOf(v) }
            : null,
        }),
        { status: 409 },
      )
    }
    disk.set(id, body)
    return new Response(
      JSON.stringify({ ok: true, saved_at: 1234, revision: revOf(body) }),
      { status: 200 },
    )
  }
  if (init?.method === 'DELETE') {
    disk.delete(id)
    return new Response('{"ok":true}', { status: 200 })
  }
  const v = disk.get(id)
  return new Response(v ?? '{}', {
    status: v ? 200 : 404,
    headers: v ? { 'X-Tavotto-Revision': revOf(v) } : undefined,
  })
}) as typeof fetch

const releasePuts = async () => {
  while (gates.length) {
    gates.shift()!()
    await tick()
  }
  await tick()
}

/** 有内容的一份项目文档（空文档不落盘） */
const seeded = (updatedAt: number, name = 'proj', objects = 1): ProjectDocument => {
  const pd = emptyProject()
  pd.project.name = name
  pd.canvases[0].objects = Array.from({ length: objects }, (_, i) => text(`t${i}`, 'x'))
  pd.updatedAt = updatedAt
  return pd
}

let stopAutosave: (() => void) | null = null

beforeEach(async () => {
  globalThis.fetch = fakeFetch
  disk.clear()
  puts.length = 0
  gates.length = 0
  gatePuts = false
  putMode = 'ok'
  localStorage.clear()
  await s().switchDocument(emptyProject(), 'd_reset')
  await tick()
  disk.clear()
  localStorage.clear()
  puts.length = 0
  // 订阅一直开着：`dirty` 是由它置上的，不是 `commit()` 自己置的。
  // 只在个别用例里开的话，"保存期间继续编辑"那条会因为根本没人把 dirty
  // 置上而恒绿——它守的正是那一位。
  stopAutosave = startAutosave()
})

afterEach(async () => {
  await releasePuts()
  stopAutosave?.()
  stopAutosave = null
  vi.useRealTimers()
})

describe('保存状态机', () => {
  it('编辑 → dirty → saving → saved → clean（saved 是短暂反馈态）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await s().switchDocument(seeded(1), 'd_sm')
    await vi.advanceTimersByTimeAsync(50)
    expect(s().saveState).toBe('saved')

    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    expect(s().saveState).toBe('dirty')

    gatePuts = true
    await vi.advanceTimersByTimeAsync(1200) // 防抖窗口过去
    await vi.advanceTimersByTimeAsync(0)
    expect(s().saveState).toBe('saving')

    gatePuts = false
    await releasePuts()
    expect(s().saveState).toBe('saved')
    expect(s().lastPersisted).toBe(1234) // 磁盘回的 saved_at，不是本机时钟

    await vi.advanceTimersByTimeAsync(2000)
    expect(s().saveState).toBe('clean')
  })

  it('保存期间继续编辑：写成功后仍是 dirty，绝不显示成已保存', async () => {
    await s().switchDocument(seeded(1), 'd_during')
    await tick()
    gatePuts = true
    s().commit(literal('第一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    flushAutosave()
    await tick()
    expect(s().saveState).toBe('saving')

    // 快照已经发出去了，这一笔不在里面
    s().commit(literal('保存期间又改了一笔'), (d) => {
      d.objects.push(text('n2', 'B'))
    })
    gatePuts = false
    await releasePuts()
    expect(s().dirty).toBe(true)
    expect(s().saveState).toBe('dirty')
  })

  it('写盘失败 → save_error，内存与本机副本都留着，重试能救回来', async () => {
    await s().switchDocument(seeded(1), 'd_err')
    await tick()
    putMode = 'io'
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    expect(await saveNow()).toBe('save_error')
    expect(s().saveState).toBe('save_error')
    expect(localStorage.getItem(slotKey('d_err'))).not.toBeNull()

    putMode = 'ok'
    expect(await saveNow()).toBe('saved')
    expect(JSON.parse(disk.get('d_err')!).canvases[0].objects).toHaveLength(2)
  })

  it('hasUnsavedWork 只对真有未落盘工作的四个状态为真', () => {
    expect(['dirty', 'saving', 'save_error', 'conflict'].every(
      (v) => hasUnsavedWork(v as never))).toBe(true)
    expect(['clean', 'saved'].some((v) => hasUnsavedWork(v as never))).toBe(false)
  })
})

describe('手动保存（⌘S）', () => {
  it('saveNow 等到磁盘真的写完才返回', async () => {
    await s().switchDocument(seeded(1), 'd_manual')
    await tick()
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    const state = await saveNow()
    expect(state).toBe('saved')
    // 返回的那一刻磁盘上就是这一版——不是"排进队列了"
    expect(JSON.parse(disk.get('d_manual')!).canvases[0].objects).toHaveLength(2)
  })

  it('连按多次合并成一次写入，不并发覆盖', async () => {
    await s().switchDocument(seeded(1), 'd_multi')
    await tick()
    puts.length = 0
    gatePuts = true
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    const runs = [saveNow(), saveNow(), saveNow()]
    await tick()
    gatePuts = false
    await releasePuts()
    const states = await Promise.all(runs)
    expect(states.every((x) => x === 'saved' || x === 'clean')).toBe(true)
    // 三次调用最多一次在途 + 一次排队合并，绝不是三次并发 PUT
    expect(puts.length).toBeLessThanOrEqual(2)
  })

  it('空文档没什么可存：直接 clean，不产生 PUT', async () => {
    await s().switchDocument(emptyProject(), 'd_empty')
    await tick()
    puts.length = 0
    expect(await saveNow()).toBe('clean')
    expect(puts).toHaveLength(0)
  })
})

describe('外部修改冲突（R-08）', () => {
  const enterConflict = async () => {
    await s().switchDocument(seeded(1), 'd_conf')
    await tick()
    disk.set('d_conf', JSON.stringify(seeded(999, 'theirs', 7)))
    putMode = 'external'
    s().commit(literal('本窗口改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    await saveNow()
  }

  it('409 external_change → conflict，磁盘不被覆盖，摘要带上"那边是什么"', async () => {
    await enterConflict()
    expect(s().saveState).toBe('conflict')
    expect(s().saveIssue).toMatchObject({ kind: 'external', docId: 'd_conf' })
    expect(s().saveIssue!.disk).toMatchObject({ objects: 7, name: 'theirs' })
    expect(JSON.parse(disk.get('d_conf')!).project.name).toBe('theirs')
    expect(localStorage.getItem(slotKey('d_conf'))).not.toBeNull()
  })

  it('冲突期间的自动保存只写本机副本，一次都不再撞磁盘', async () => {
    await enterConflict()
    puts.length = 0
    s().commit(literal('冲突期间再改'), (d) => {
      d.objects.push(text('n2', 'B'))
    })
    flushAutosave()
    await tick()
    expect(puts).toHaveLength(0)
    expect(s().saveState).toBe('conflict')
    expect(JSON.parse(localStorage.getItem(slotKey('d_conf'))!).canvases[0].objects)
      .toHaveLength(3)
  })

  it('重新加载：磁盘那份进内存，**刚才的内存版本变成可恢复的副本**', async () => {
    await enterConflict()
    putMode = 'ok'
    expect(await reloadFromDisk()).toBe(true)
    expect(s().projectMeta.name).toBe('theirs')
    expect(s().doc.objects).toHaveLength(7)
    expect(s().saveState).toBe('clean')
    // 「重新加载」不是一个会丢东西的按钮
    expect(s().docNotice).toMatchObject({ kind: 'recovery', docId: 'd_conf' })
    expect(localStorage.getItem(recoveryKey('d_conf'))).not.toBeNull()
  })

  it('明确覆盖：拿 409 里回的 hash 当基线写过去，**不是关掉校验**', async () => {
    await enterConflict()
    putMode = 'ok'
    puts.length = 0
    expect(await overwriteDisk()).toBe('saved')
    expect(puts[0].rev).toBe(revOf(JSON.stringify(seeded(999, 'theirs', 7))))
    expect(JSON.parse(disk.get('d_conf')!).project.name).toBe('proj')

    // 覆盖之后校验仍然生效：再来一次外部修改照样挡下
    putMode = 'external'
    s().commit(literal('再改一笔'), (d) => {
      d.objects.push(text('n3', 'C'))
    })
    await saveNow()
    expect(s().saveState).toBe('conflict')
  })

  it('从没读过磁盘就写：先确认，发现有一份没读过的 → 冲突而不是覆盖', async () => {
    // 另一个进程刚在这个 id 上写了一份，本窗口从没读过它
    disk.set('d_unseen', JSON.stringify(seeded(500, 'someone-else', 4)))
    await s().switchDocument(seeded(1), 'd_unseen')
    await tick()
    await tick()
    expect(s().saveState).toBe('conflict')
    expect(JSON.parse(disk.get('d_unseen')!).project.name).toBe('someone-else')
  })
})

describe('崩溃恢复', () => {
  it('本机副本比磁盘新 → 打开主文档 + 提供恢复；主文档一个字节不动', async () => {
    disk.set('d_rec', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(slotKey('d_rec'), JSON.stringify(seeded(500, 'crashed', 5)))
    localStorage.setItem('tavotto.currentDoc', 'd_rec')

    expect(await restoreSession()).toBe(true)
    expect(s().projectMeta.name).toBe('main')
    expect(s().docNotice).toMatchObject({ kind: 'recovery', docId: 'd_rec' })
    expect(JSON.parse(disk.get('d_rec')!).project.name).toBe('main')
  })

  it('恢复动作只进内存并置 dirty —— 用户确认保存后才覆盖主文档', async () => {
    disk.set('d_rec2', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(slotKey('d_rec2'), JSON.stringify(seeded(500, 'crashed', 5)))
    localStorage.setItem('tavotto.currentDoc', 'd_rec2')
    await restoreSession()

    expect(recoverLocalCopy()).toBe(true)
    expect(s().projectMeta.name).toBe('crashed')
    expect(s().doc.objects).toHaveLength(5)
    expect(s().saveState).toBe('dirty')
    expect(s().docNotice).toBeNull()
    // 关键：此刻磁盘上还是主版本
    expect(JSON.parse(disk.get('d_rec2')!).project.name).toBe('main')

    await saveNow()
    expect(JSON.parse(disk.get('d_rec2')!).project.name).toBe('crashed')
  })

  it('保留主版本：只删自己那一个恢复键，别的键一个不动', async () => {
    disk.set('d_rec3', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(slotKey('d_rec3'), JSON.stringify(seeded(500, 'crashed', 5)))
    localStorage.setItem('tavotto.currentDoc', 'd_rec3')
    localStorage.setItem(recoveryKey('d_other'), '{"keep":"me"}')
    localStorage.setItem('unrelated.key', 'untouched')
    await restoreSession()

    discardLocalCopy()
    expect(s().docNotice).toBeNull()
    expect(localStorage.getItem(recoveryKey('d_rec3'))).toBeNull()
    expect(localStorage.getItem(recoveryKey('d_other'))).toBe('{"keep":"me"}')
    expect(localStorage.getItem('unrelated.key')).toBe('untouched')
  })

  it('没裁决完的恢复副本跨会话仍在：下一次打开还会问', async () => {
    disk.set('d_rec4', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(recoveryKey('d_rec4'), JSON.stringify(seeded(500, 'crashed', 5)))
    const { doc, notice } = await readAutosaveDoc('d_rec4')
    expect(doc!.project.name).toBe('main')
    expect(notice).toMatchObject({ kind: 'recovery' })
  })

  it('损坏的恢复副本不拦住打开文档', async () => {
    disk.set('d_bad', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(slotKey('d_bad'), '{ not json')
    const { doc, notice } = await readAutosaveDoc('d_bad')
    expect(doc!.project.name).toBe('main')
    expect(notice).toBeNull()
  })
})

describe('未来 schema 的文档', () => {
  it('不打开、不覆盖，明确说出来', async () => {
    disk.set('d_future', JSON.stringify({ ...seeded(100, 'newer'), schema: 9 }))
    localStorage.setItem('tavotto.currentDoc', 'd_future')

    expect(await restoreSession()).toBe(false)
    expect(s().docNotice).toEqual({ kind: 'schema_too_new', docId: 'd_future', schema: 9 })
    // 磁盘上那份原封不动
    expect(JSON.parse(disk.get('d_future')!).schema).toBe(9)

    dismissDocNotice()
    expect(s().docNotice).toBeNull()
  })
})

describe('关闭与刷新保护', () => {
  /** 派一个真的 beforeunload，看 `defaultPrevented`——那才是浏览器的判据 */
  const leave = () => {
    const ev = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(ev)
    return ev.defaultPrevented
  }

  it('clean 的文档刷新不拦：每次都弹一次的话，真该拦的那次也拦不住了', async () => {
    await s().switchDocument(seeded(1), 'd_clean')
    await tick()
    expect(s().saveState).toBe('saved')
    expect(leave()).toBe(false)
  })

  it('dirty / save_error / conflict 都拦，并且离开前再冲刷一次', async () => {
    await s().switchDocument(seeded(1), 'd_leave')
    await tick()
    s().commit(literal('改一笔'), (d) => {
      d.objects.push(text('n1', 'A'))
    })
    expect(s().saveState).toBe('dirty')
    expect(leave()).toBe(true)
    // 拦下来之前先把最后一次改动落进本机副本 + 排队写盘（防抖窗口内也不丢）
    await tick()
    expect(JSON.parse(disk.get('d_leave')!).canvases[0].objects).toHaveLength(2)

    putMode = 'io'
    s().commit(literal('再改一笔'), (d) => {
      d.objects.push(text('n2', 'B'))
    })
    await saveNow()
    expect(s().saveState).toBe('save_error')
    expect(leave()).toBe(true)
  })

  it('未决的恢复副本不算未保存工作：那份副本本来就在磁盘上躺着', async () => {
    disk.set('d_ln', JSON.stringify(seeded(100, 'main', 2)))
    localStorage.setItem(slotKey('d_ln'), JSON.stringify(seeded(500, 'crashed', 5)))
    localStorage.setItem('tavotto.currentDoc', 'd_ln')
    await restoreSession()
    expect(s().docNotice).toMatchObject({ kind: 'recovery' })
    expect(s().saveState).toBe('clean')
    expect(leave()).toBe(false)
  })
})
