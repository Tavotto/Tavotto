import { afterEach, describe, expect, it } from 'vitest'
import { apiUrl, currentProjectId, setCurrentProjectId, withProject } from './session'

/**
 * pj 认领的两条路（查询参数 / 请求头）。回归重点是**下载类链接**：
 * `<a href>` 与 `<img src>` 加不了请求头，唯一的出路是 apiUrl() 补上 `?pj=`，
 * 漏一处就会让非默认项目的标签页去别的图库找文件。
 */
describe('apiUrl', () => {
  afterEach(() => setCurrentProjectId(null))

  it('未绑定项目时原样返回（后端按默认项目处理）', () => {
    setCurrentProjectId(null)
    expect(currentProjectId()).toBeNull()
    expect(apiUrl('/exports/Fig1_20260817.pdf')).toBe('/exports/Fig1_20260817.pdf')
  })

  it('导出下载链接挂上 pj —— ExportDialog 的 <a href> 就走这条', () => {
    setCurrentProjectId('p2')
    expect(apiUrl('/exports/Fig1_20260817.pdf')).toBe('/exports/Fig1_20260817.pdf?pj=p2')
    // 项目包（POST /api/package）回的也是 /exports/<name>，同一条路
    expect(apiUrl('/exports/paper_pack.zip')).toBe('/exports/paper_pack.zip?pj=p2')
  })

  it('已有查询串时用 & 追加，不把原参数冲掉', () => {
    setCurrentProjectId('p2')
    expect(apiUrl('/api/render?id=Fig1.pdf&w=400')).toBe('/api/render?id=Fig1.pdf&w=400&pj=p2')
  })

  it('项目 id 里的特殊字符要转义，否则会被当成新参数', () => {
    setCurrentProjectId('a&b=c')
    expect(apiUrl('/exports/x.pdf')).toBe('/exports/x.pdf?pj=a%26b%3Dc')
  })
})

describe('withProject', () => {
  afterEach(() => setCurrentProjectId(null))

  it('补请求头且保留调用方自己的 headers', () => {
    setCurrentProjectId('p2')
    const init = withProject({ method: 'POST', headers: { 'Content-Type': 'application/json' } })
    expect(init).toEqual({
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Tavotto-Project': 'p2' },
    })
  })

  it('未绑定项目时原样返回 init', () => {
    setCurrentProjectId(null)
    const init = { method: 'POST' }
    expect(withProject(init)).toBe(init)
  })
})
