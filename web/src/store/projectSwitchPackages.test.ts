/**
 * 换项目时包管理的状态必须换代（ADR 0038，本轮评审 P2）。
 *
 * 「在 PyPI 查找」的结果是**全局的、不带项目身份**的一份状态，而它答的每一句
 * 话都属于某一个项目：`installed` 说的是那个项目的受管环境里装了哪一版，
 * `source` 说的是那个项目解释器读到的索引源配置。改造前 `resetForNewProject()`
 * 既不清它、也不作废在途的那一次——于是 A 项目的查找结果会一直开在 B 的包页面
 * 上，而那一页的安装按钮作用在 B 上。
 *
 * 两条判据分别看两件不同的事，**各自可被单独拆掉**（同一条保证做两遍的话，
 * 拆掉其中一遍会照样全绿）：
 *
 *   * `clear()` 里的 `set(IDLE_LOOKUP)` 管**已经落地**的那份；
 *   * `clear()` 里的换代管**还在飞**的那些。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import type { PackageLookup } from '@/lib/api'
import { usePackageStore } from './packageStore'
import { useProjectStore } from './projectStore'

/** A 项目那次查找的答案——`installed` / `source` 是它属于哪个项目的证据 */
const A_ANSWER: PackageLookup = {
  name: 'lmfit',
  versions: ['1.3.4', '1.3.2'],
  latest: '1.3.4',
  installed: '1.3.2',
  source: 'custom_index',
}

/** 把 `/api/engine/packages/lookup` 那一次请求**扣在手里**，由用例决定何时回答 */
let releaseLookup: ((body: unknown, status?: number) => void) | null = null

globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  if (u.includes('/api/engine/packages/lookup')) {
    return new Promise<Response>((resolve) => {
      releaseLookup = (body, status = 200) =>
        resolve(
          new Response(JSON.stringify(body), {
            status,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
    })
  }
  const body = u.includes('/api/engine/packages')
    ? { environment: null, capability: { available: true }, user: [], builtin: [] }
    : u.includes('/api/engine/environment')
      ? { ok: true, python: '/p', source: 'system', project: { open: true } }
      : u.includes('/api/projects/recent')
        ? { recent: [] }
        : u.includes('/api/projects/open')
          ? []
          : u.includes('/api/panels')
            ? { figures_dir: '/new', panels: [] }
            : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

const switchProject = () =>
  useProjectStore
    .getState()
    .adoptOpenedProject({ id: 'p2', path: '/new', name: 'new', writable: true, open: true } as never)

beforeEach(() => {
  releaseLookup = null
  setCurrentProjectId('p1')
  usePackageStore.setState({
    data: null,
    loading: false,
    loadError: '',
    progress: null,
    busy: false,
    errorCode: '',
    errorText: '',
    lookup: { query: '', status: 'idle', result: null, code: '', text: '' },
  })
})
afterEach(() => vi.restoreAllMocks())

describe('换项目时的包管理状态', () => {
  it('已经落地的查找结果被清掉——它说的 installed 是旧项目环境里的版本', async () => {
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.(A_ANSWER)
    await run
    expect(usePackageStore.getState().lookup.result).toEqual(A_ANSWER)

    await switchProject()

    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('idle')
    expect(after.result).toBeNull()
    expect(after.query).toBe('')
  })

  it('A 的查找在切到 B 之后才回来：整份被丢弃，store 里查不到它', async () => {
    const run = usePackageStore.getState().runLookup('lmfit')
    expect(usePackageStore.getState().lookup.status).toBe('loading')

    await switchProject()
    // 切完项目**之后**答案才回来——这一步是这条用例的全部意义
    releaseLookup?.(A_ANSWER)
    await run
    await new Promise((r) => setTimeout(r, 0))

    const after = usePackageStore.getState().lookup
    // 断言的是「store 里没有它」，不是「界面上看不见」
    expect(after.result).toBeNull()
    expect(after.status).toBe('idle')
    expect(JSON.stringify(after)).not.toContain('custom_index')
    expect(JSON.stringify(after)).not.toContain('1.3.2')
  })

  it('查找失败的那一次迟到回来同样被丢弃（走的是 catch 那一支）', async () => {
    // 502 才真的进 `runLookup` 的 catch。回 200 带一个 code 字段的话
    // `jsonFetch` 会当成成功，这条用例就跑到了 try 那一支上——名字说的是失败
    // 路径，量到的却是成功路径。
    const run = usePackageStore.getState().runLookup('lmfit')
    await switchProject()
    releaseLookup?.({ code: 'package_lookup_offline', error: '连不上索引' }, 502)
    await run
    await new Promise((r) => setTimeout(r, 0))

    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('idle')
    expect(after.code).toBe('')
    expect(after.text).toBe('')
  })

  it('先证明 502 真的进了 catch：不切项目时它会落成 error', async () => {
    // 上一条用例的前提。没有这一条，`jsonFetch` 哪天不再对 502 抛异常，那条
    // 「被丢弃」就会因为**根本没有答案落地**而恒绿。
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.({ code: 'package_lookup_offline', error: '连不上索引' }, 502)
    await run
    const after = usePackageStore.getState().lookup
    expect(after.status).toBe('error')
    expect(after.code).toBe('package_lookup_offline')
  })

  it('清单与上一次的错误也跟着丢：它们说的是旧项目那个受管环境', async () => {
    usePackageStore.setState({
      data: { environment: { python: '/a/bin/python' }, user: [{ distribution: 'lmfit' }] } as never,
      loadError: '旧项目的加载失败',
      errorCode: 'package_busy',
      errorText: '旧项目的错误',
    })
    await switchProject()
    const s = usePackageStore.getState()
    expect(s.data).toBeNull()
    expect(s.loadError).toBe('')
    expect(s.errorCode).toBe('')
    expect(s.errorText).toBe('')
  })

  it('换代之后再查一次，新项目自己的答案照常落地（不是把功能关掉了）', async () => {
    await switchProject()
    const run = usePackageStore.getState().runLookup('lmfit')
    releaseLookup?.({ ...A_ANSWER, installed: '', source: 'pypi' })
    await run
    expect(usePackageStore.getState().lookup.result?.source).toBe('pypi')
    expect(usePackageStore.getState().lookup.status).toBe('found')
  })
})
