/**
 * 项目文件绑定的会话层（ADR 0096 评审第 7 轮）：本会话以内存里的绑定为准，localStorage
 * 只是刷新之后还认得的尽力而为的副本。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  forgetProjectFile,
  findDocumentsForProjectFile,
  forgetProjectFilesInSession,
  readProjectFile,
  writeProjectFile,
  type ProjectFileBinding,
} from './projectFile'

const B: ProjectFileBinding = {
  projectId: 'pjA',
  name: '排版一',
  file: 'tavottofile/排版一.json',
  revision: 'r1',
  dirty: false,
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('写不进本机存储时本会话照常', () => {
  it('setItem 抛错：读回的是刚写的那份', () => {
    writeProjectFile('d1', B)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })
    writeProjectFile('d1', { ...B, revision: 'r2', dirty: true })
    expect(readProjectFile('d1')).toMatchObject({ revision: 'r2', dirty: true })
  })

  it('removeItem 也抛错：解绑之后读回 null，不读回那份旧的持久副本', () => {
    writeProjectFile('d1', B)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    forgetProjectFile('d1')
    expect(localStorage.getItem('tavotto.projectFile.d1')).not.toBeNull()
    expect(readProjectFile('d1')).toBeNull()
  })

  it('会话层空着（刷新之后）：读持久副本', () => {
    writeProjectFile('d1', B)
    forgetProjectFilesInSession()
    expect(readProjectFile('d1')).toEqual({ ...B, at: expect.any(Number) })
  })
})

describe('findDocumentsForProjectFile 读最新的持久绑定（Codex #1734 P1）', () => {
  it('同名工作副本的 legacy 与 v4 绑定独立查找、独立判断 dirty', () => {
    forgetProjectFilesInSession()
    writeProjectFile('shared', B)
    writeProjectFile('shared', B, 'v4')
    expect(findDocumentsForProjectFile(B.projectId, B.file, 'v4')).toEqual(['shared'])
    writeProjectFile('shared', { ...B, dirty: true }, 'v4')
    expect(findDocumentsForProjectFile(B.projectId, B.file, 'v4')).toEqual([])
    expect(findDocumentsForProjectFile(B.projectId, B.file)).toEqual(['shared'])
    forgetProjectFilesInSession()
  })
  it('别的窗口把它置 dirty 之后，本窗口缓存里的干净旧绑定不能让它被放行', () => {
    writeProjectFile('d1', B)
    const raw = JSON.parse(localStorage.getItem('tavotto.projectFile.d1')!)
    localStorage.setItem('tavotto.projectFile.d1', JSON.stringify({ ...raw, dirty: true }))
    expect(findDocumentsForProjectFile(B.projectId, B.file)).toEqual([])
  })

  it('存储写不进去时退回会话缓存', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    writeProjectFile('d1', B)
    expect(findDocumentsForProjectFile(B.projectId, B.file)).toEqual(['d1'])
  })

  it('第一次写成功、之后置 dirty 时持久副本写失败：以 dirty 的会话绑定为准，不沿用', () => {
    writeProjectFile('d1', B)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    writeProjectFile('d1', { ...B, dirty: true })
    expect(readProjectFile('d1')?.dirty).toBe(true)
    expect(JSON.parse(localStorage.getItem('tavotto.projectFile.d1')!).dirty).toBe(false) // 持久副本停在旧值
    expect(findDocumentsForProjectFile(B.projectId, B.file)).toEqual([])
  })
})
