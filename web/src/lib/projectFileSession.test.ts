import { describe, expect, it } from 'vitest'
import { createProjectFileEditor, type ProjectFileView } from './projectFileSession'

const view = (): ProjectFileView => ({
  version: 1,
  status: 'ok',
  binding: null,
  edit: { autosave_revision: null, axis: null, known: false },
  intent_sequence: 0,
})

describe('ProjectFileAddress 的键只有 projectId + documentId', () => {
  it('同项目不同文档 / 同文档不同项目互不共享会话行', () => {
    const editor = createProjectFileEditor('editor_test')
    const a = { projectId: 'pj_a', documentId: 'd_same' }
    const otherDoc = { projectId: 'pj_a', documentId: 'd_other' }
    const otherProject = { projectId: 'pj_b', documentId: 'd_same' }
    editor.markUserEdit(a)
    editor.observe(a, view())
    expect(editor.support(a)).toBe('supported')
    expect(editor.support(otherDoc)).toBe('unknown')
    expect(editor.support(otherProject)).toBe('unknown')
    expect(editor.capture(a).axis).not.toBeNull()
    expect(editor.capture(otherDoc).axis).toBeNull()
    expect(editor.capture(otherProject).axis).toBeNull()
  })

  it('同一 (projectId, documentId) 是同一行：编辑轴与视图都复用', () => {
    const editor = createProjectFileEditor('editor_test')
    const a = { projectId: 'pj_a', documentId: 'd_same' }
    editor.markUserEdit(a)
    const same = { projectId: 'pj_a', documentId: 'd_same' }
    expect(editor.capture(same).axis).toEqual(editor.capture(a).axis)
    editor.observe(a, { ...view(), intent_sequence: 3 })
    expect(editor.view(same)?.intent_sequence).toBe(3)
  })
})
