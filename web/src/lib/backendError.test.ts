/**
 * 后端错误 → 当前语言的一句话。
 *
 * 契约在 `src/tavotto/app.py` 的 API 段首：用户会看到的失败带稳定 `code` +
 * `params`，`error` 里的中文原文是回退。后端**不知道**用户选了哪门语言，所以
 * 翻译只能发生在这一侧。
 *
 * `tests/test_error_codes.py` 从另一头看护同一条契约（后端真发了 code、两种
 * 语言都有文案、占位符与 params 对得上）。这里看护的是选择逻辑本身。
 */
import { afterEach, describe, expect, it } from 'vitest'

import { ApiError, backendErrorText } from './api'
import { DEFAULT_LOCALE, i18n } from '@/i18n'

const err = (message: string, body: Record<string, unknown>, status = 400) =>
  new ApiError(message, status, body)

afterEach(async () => {
  await i18n.changeLanguage(DEFAULT_LOCALE)
})

describe('backendErrorText', () => {
  it('认识的 code：按当前语言说，参数插进去', async () => {
    const e = err('目录不存在: /gone', { code: 'dir_missing', params: { path: '/gone' } })
    expect(backendErrorText(e)).toBe('目录不存在：/gone')

    await i18n.changeLanguage('en-US')
    expect(backendErrorText(e)).toBe('Folder does not exist: /gone')
  })

  it('同一个 error 对象在切语言后给出新语言的文案（不缓存翻译结果）', async () => {
    const e = err('路径无效', { code: 'invalid_path' })
    expect(backendErrorText(e)).toBe('路径无效')
    await i18n.changeLanguage('en-US')
    expect(backendErrorText(e)).toBe('Invalid path')
    await i18n.changeLanguage('zh-CN')
    expect(backendErrorText(e)).toBe('路径无效')
  })

  it('不认识的 code：原样透出后端那句话——一句中文比一串 key 有用', async () => {
    const e = err('某个还没翻译的失败', { code: 'brand_new_code_from_the_future' })
    expect(backendErrorText(e)).toBe('某个还没翻译的失败')

    await i18n.changeLanguage('en-US')
    expect(backendErrorText(e)).toBe('某个还没翻译的失败')
  })

  it('没有 code：原样透出（校验类错误、以及老后端）', () => {
    expect(backendErrorText(err('patches 必须是数组', {}))).toBe('patches 必须是数组')
  })

  it('不是 ApiError 也不崩：普通 Error 取 message，其余 String 化', () => {
    expect(backendErrorText(new Error('boom'))).toBe('boom')
    expect(backendErrorText('boom')).toBe('boom')
    expect(backendErrorText(undefined)).toBe('undefined')
  })

  it('code 有、params 没有：文案里的占位符不会把整条吞掉', async () => {
    // 后端漏塞 params 时，i18next 会把 {{path}} 原样留着——难看，但仍然
    // 说明了发生了什么，比回退到「未知错误」强
    const e = err('目录不存在: /gone', { code: 'dir_missing' })
    expect(backendErrorText(e)).toContain('目录不存在')
    await i18n.changeLanguage('en-US')
    expect(backendErrorText(e)).toContain('Folder does not exist')
  })
})
