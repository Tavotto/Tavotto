import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { OriginEntryList } from '@/components/OriginDetails'
import { repairShortMessage } from '@/components/DependencyRepairCard'
import { i18n } from '@/i18n'
import type { JointOrigin } from '@/lib/api'

// 找不到的包点名（ADR 0061 修订 2026-10-21）：一句话，中英文各一份
describe('dependency_not_found 点名', () => {
  afterEach(async () => {
    await i18n.changeLanguage('zh-CN')
  })

  it('有来源文件：软件源里找不到 X（来自 Y）', async () => {
    await i18n.changeLanguage('zh-CN')
    expect(repairShortMessage('dependency_not_found', { name: 'ghost-pkg', source: 'requirements.txt' })).toBe(
      '软件源里找不到 ghost-pkg（来自 requirements.txt）。',
    )
  })

  it('没有声明文件：来源写 import 名', async () => {
    await i18n.changeLanguage('zh-CN')
    expect(repairShortMessage('dependency_not_found', { name: 'ghost-pkg', import_name: 'ghost' })).toBe(
      '软件源里找不到 ghost-pkg（来自 import ghost）。',
    )
  })

  it('只认得名字：不带来源的一句；认不出名字：退回原来的通用句', async () => {
    await i18n.changeLanguage('zh-CN')
    expect(repairShortMessage('dependency_not_found', { name: 'ghost-pkg' })).toBe('软件源里找不到 ghost-pkg。')
    expect(repairShortMessage('dependency_not_found')).toBe('软件源里找不到这个包，在详情里核对包名。')
  })

  it('英文', async () => {
    await i18n.changeLanguage('en-US')
    expect(repairShortMessage('dependency_not_found', { name: 'ghost-pkg', source: 'requirements.txt' })).toBe(
      'The package index has no ghost-pkg (from requirements.txt).',
    )
  })

  it('别的错误码不受影响', async () => {
    await i18n.changeLanguage('zh-CN')
    expect(repairShortMessage('dependency_conflict', { name: 'x' })).toBe(repairShortMessage('dependency_conflict'))
  })
})

describe('来源详情：装不了的包 / 本地模块缺失', () => {
  const base = {
    bucket: 'unknown',
    distribution: '',
    reason: 'unresolved',
    resolution_status: 'unresolved',
    distribution_status: '',
    origin_kind: '',
    provenance: '',
    candidates: [],
    coverage: '',
  } as const

  async function render(origins: JointOrigin[]) {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(<OriginEntryList origins={origins} title="t" />))
    const text = host.textContent ?? ''
    await act(async () => root.unmount())
    host.remove()
    return text
  }

  it('ROOT：内置 Python 装不了，请用装有它的环境', async () => {
    await i18n.changeLanguage('zh-CN')
    const text = await render([
      { ...base, import_name: 'ROOT', not_pip_installable: { label: 'ROOT', hint: 'own_env' } } as unknown as JointOrigin,
    ])
    expect(text).toContain('这个脚本需要 ROOT，内置 Python 装不了，请用装有它的环境。')
  })

  it('本地模块缺失：点名模块与被加进搜索路径的文件夹', async () => {
    await i18n.changeLanguage('zh-CN')
    const text = await render([
      { ...base, import_name: 'pdf_x', local_module_missing: { search_dirs: ['pdf'] } } as unknown as JointOrigin,
    ])
    expect(text).toContain('找不到本地模块 pdf_x（脚本把 pdf/ 加进了搜索路径，请确认这个文件夹在）。')
  })
})
