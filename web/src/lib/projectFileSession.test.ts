import { describe, expect, it } from 'vitest'
import { createProjectFileEditor, type ProjectFileView } from './projectFileSession'

const address = { projectId: 'project-A', format: 'v4' as const, documentId: 'working-A' }
const view = (over: Partial<ProjectFileView> = {}): ProjectFileView => ({
  version: 1, status: 'ok', intent_sequence: 3,
  binding: { sequence: 3, name: 'A', file: 'tavottofile/documents/v4/A.json', revision: 'named-rev', dirty: false },
  edit: { autosave_revision: 'working-rev', axis: { editor_id: 'previous-editor', sequence: 7 }, known: true },
  ...over,
})

describe('本机工作副本的用户编辑轴', () => {
  it('每次明确恢复分支生成独立身份，旧capture不改，后续用户编辑沿新分支递增', () => {
    const editor = createProjectFileEditor('original-editor')
    editor.observe(address, view()); editor.markUserEdit(address)
    const old = editor.capture(address)
    editor.newRecoveryBranch(address); editor.markUserEdit(address)
    const first = editor.capture(address)
    expect(first.axis?.editor_id).not.toBe(old.axis?.editor_id)
    expect(first.axis?.sequence).toBe(1)
    editor.markUserEdit(address)
    expect(editor.capture(address).axis).toEqual({ ...first.axis, sequence: 2 })
    editor.newRecoveryBranch(address); editor.markUserEdit(address)
    expect(editor.capture(address).axis?.editor_id).not.toBe(first.axis?.editor_id)
    expect(editor.capture(address).axis?.sequence).toBe(1)
    expect(old.axis).toEqual({ editor_id: 'original-editor', sequence: 1 })
  })
  it('真实默认工厂的两活跃实例各自生成内存editor身份', () => {
    const a = createProjectFileEditor(), b = createProjectFileEditor()
    a.markUserEdit(address); b.markUserEdit(address)
    expect(a.capture(address).axis?.editor_id).not.toBe(b.capture(address).axis?.editor_id)
    expect(a.capture(address).axis?.sequence).toBe(1)
    expect(b.capture(address).axis?.sequence).toBe(1)
  })
  it('继承轴仅供未编辑/派生；首次用户编辑换本实例 seq1，此后原入口递增', () => {
    const editor = createProjectFileEditor('this-editor')
    editor.observe(address, view(), 'working-rev')
    const initial = editor.capture(address)
    expect(initial.axis).toEqual({ editor_id: 'previous-editor', sequence: 7 })
    expect(editor.capture(address)).toEqual(initial)
    editor.markUserEdit(address)
    expect(editor.capture(address)).toMatchObject({
      axis: { editor_id: 'this-editor', sequence: 1 }, base_axis: initial.axis,
    })
    const queued = editor.capture(address)
    editor.markUserEdit(address)
    expect(editor.capture(address).axis).toEqual({ editor_id: 'this-editor', sequence: 2 })
    expect(queued.axis).toEqual({ editor_id: 'this-editor', sequence: 1 })
    expect(queued.base_axis).toEqual(initial.axis)
  })

  it('同原轴的两个标签 seq1仍是不同编辑身份，不取跨编辑器最大值', () => {
    const a = createProjectFileEditor('editor-A'), b = createProjectFileEditor('editor-B')
    a.observe(address, view()); b.observe(address, view())
    a.markUserEdit(address); b.markUserEdit(address)
    expect(a.capture(address).axis).toEqual({ editor_id: 'editor-A', sequence: 1 })
    expect(b.capture(address).axis).toEqual({ editor_id: 'editor-B', sequence: 1 })
    expect(a.capture(address).base_axis).toEqual(b.capture(address).base_axis)
  })

  it('AS成功不抹在途新编辑；旧captured祖先不随回包替换', () => {
    const editor = createProjectFileEditor('editor-A')
    editor.observe(address, view())
    editor.markUserEdit(address)
    const queued = editor.capture(address)
    editor.markUserEdit(address)
    editor.accepted(queued, { ...view(), state: 'accepted', edit: { known: true, autosave_revision: 'next-rev', axis: queued.axis } })
    expect(editor.capture(address).axis).toEqual({ editor_id: 'editor-A', sequence: 2 })
    expect(editor.capture(address).base_axis).toEqual(queued.axis)
    expect(queued.base_axis).toEqual(view().edit.axis)
  })

  it('A→B→A用关联代次拒绝旧回执；项目和格式各自隔离', () => {
    const editor = createProjectFileEditor('editor-A')
    editor.observe(address, view())
    const capture = editor.capture(address)
    editor.reserved(address, { sequence: 5, binding_sequence: 3, name: 'A', file: 'A.json', autosave_revision: 'working-rev', axis: capture.axis })
    expect(editor.accepted(capture, { ...view(), state: 'bound' })).toBe(false)
    expect(editor.isLatest(address, 3)).toBe(false)
    expect(editor.support({ ...address, format: 'legacy' })).toBe('unknown')
    expect(editor.support({ ...address, projectId: 'project-B' })).toBe('unknown')
  })

  it('GET新revision不取得旧正文资格，unknown不清旧绑定或继承旧轴', () => {
    const editor = createProjectFileEditor('editor-A')
    expect(editor.observe(address, view(), 'different-rev')).toBe(false)
    expect(editor.view(address)).toBeNull()
    editor.observe(address, view({ edit: { ...view().edit, known: false } }))
    expect(editor.capture(address).axis).toBeNull()
    expect(editor.view(address)?.binding).not.toBeNull()
    editor.observe(address, { version: 1, status: 'unavailable' })
    expect(editor.support(address)).toBe('unavailable')
    expect(editor.view(address)?.binding).not.toBeNull()
  })
})
