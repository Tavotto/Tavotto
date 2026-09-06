import { beforeEach, describe, expect, it } from 'vitest'
import {
  documentHasContent,
  forgetProjectDocument,
  readProjectDocument,
  rememberProjectDocument,
} from './projectDocs'

describe('projectDocs：按项目记「上次开着哪份文档」', () => {
  beforeEach(() => localStorage.clear())

  it('写了就读得回，按项目隔离，忘掉就没了', () => {
    rememberProjectDocument('p_a', { id: 'd1', name: 'Fig 1' })
    rememberProjectDocument('p_b', { id: 'd2', name: 'Fig 2' })
    expect(readProjectDocument('p_a')).toEqual({ id: 'd1', name: 'Fig 1' })
    expect(readProjectDocument('p_b')).toEqual({ id: 'd2', name: 'Fig 2' })
    forgetProjectDocument('p_a')
    expect(readProjectDocument('p_a')).toBeNull()
    expect(readProjectDocument('p_b')).not.toBeNull()
  })

  it('损坏的记录当作没有', () => {
    localStorage.setItem('tavotto.projectDoc.p_a', '{not json')
    expect(readProjectDocument('p_a')).toBeNull()
    localStorage.setItem('tavotto.projectDoc.p_a', JSON.stringify({ name: 'x' }))
    expect(readProjectDocument('p_a')).toBeNull()
  })

  it('documentHasContent：热态画布、其它画布、画布数三处都看', () => {
    const empty = { objects: [], guides: [] }
    expect(documentHasContent({ doc: empty, canvases: [empty] })).toBe(false)
    expect(documentHasContent({ doc: { objects: [{}], guides: [] }, canvases: [empty] })).toBe(true)
    expect(documentHasContent({ doc: empty, canvases: [empty, { objects: [], guides: [{}] }] })).toBe(true)
    expect(documentHasContent({ doc: empty, canvases: [empty, empty] })).toBe(true)
  })
})
