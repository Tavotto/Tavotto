/**
 * 路径显示（审计 T34）。
 *
 * 末级目录是界面正文里唯一露出的那一段，而全路径要留在展开项里。这里钉的是
 * 「末级」这个判据本身：末尾斜杠不算一级，没有分隔符时原样返回——回一个空串
 * 的话，确认框上「备份到 」后面就是一片空白，用户读不出备份去了哪儿。
 */
import { describe, expect, it } from 'vitest'

import { dirTail } from './pathDisplay'

describe('dirTail', () => {
  it('取末级目录，末尾斜杠不算一级', () => {
    expect(dirTail('/a/b/original_backups')).toBe('original_backups')
    expect(dirTail('cache/original_backups/')).toBe('original_backups')
    expect(dirTail('C:\\Users\\me\\cache\\original_backups')).toBe('original_backups')
  })

  it('本来就没有分隔符时原样返回，绝不返回空串', () => {
    expect(dirTail('original_backups')).toBe('original_backups')
    expect(dirTail('/')).toBe('/')
  })
})
