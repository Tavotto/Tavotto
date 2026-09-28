/**
 * 项目文件绑定的会话层（ADR 0096 评审第 7 轮）：本会话以内存里的绑定为准，localStorage
 * 只是刷新之后还认得的尽力而为的副本。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  forgetProjectFile,
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
    expect(readProjectFile('d1')).toEqual(B)
  })
})
