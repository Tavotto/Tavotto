/**
 * 命名画布文件的基线按**项目 + 名字**记，不只按名字。
 *
 * 画布文件落在当前项目的 `tavottofile/` 下，两个项目各有一张「Fig 1」是再
 * 正常不过的事。只按名字记的话，在 A 里存过的那份 hash 会被当成 B 里同名画布
 * 的基线。后果不是静默覆盖（hash 对不上，后端一律 409），而是**一次没有道理
 * 的「仍然覆盖」提示**——那更坏一点点：反复弹一个用户看不懂的确认框，教会的
 * 是无脑点确认，而真该拦的那一次也就跟着被点过去了。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { setCurrentProjectId } from '@/lib/session'
import {
  forgetLayoutRevisions,
  knownLayoutRevision,
  rememberLayoutRevision,
} from '@/lib/layoutRevision'

beforeEach(() => {
  forgetLayoutRevisions()
  setCurrentProjectId(null)
})

describe('layoutRevision', () => {
  it('同名画布在两个项目里各记各的', () => {
    setCurrentProjectId('pj-a')
    rememberLayoutRevision('Fig 1', 'rev-a')
    setCurrentProjectId('pj-b')
    expect(knownLayoutRevision('Fig 1')).toBeUndefined()

    rememberLayoutRevision('Fig 1', 'rev-b')
    expect(knownLayoutRevision('Fig 1')).toBe('rev-b')
    setCurrentProjectId('pj-a')
    expect(knownLayoutRevision('Fig 1')).toBe('rev-a')
  })

  it('未打开项目（退回数据目录 layouts/）也是独立的一档', () => {
    setCurrentProjectId(null)
    rememberLayoutRevision('Fig 1', 'rev-none')
    setCurrentProjectId('pj-a')
    expect(knownLayoutRevision('Fig 1')).toBeUndefined()
    setCurrentProjectId(null)
    expect(knownLayoutRevision('Fig 1')).toBe('rev-none')
  })

  it('写入 null 只忘掉当前项目下的那一条', () => {
    setCurrentProjectId('pj-a')
    rememberLayoutRevision('Fig 1', 'rev-a')
    setCurrentProjectId('pj-b')
    rememberLayoutRevision('Fig 1', 'rev-b')
    rememberLayoutRevision('Fig 1', null)

    expect(knownLayoutRevision('Fig 1')).toBeUndefined()
    setCurrentProjectId('pj-a')
    expect(knownLayoutRevision('Fig 1')).toBe('rev-a')
  })
})
