import { describe, expect, it } from 'vitest'
import { EngineError, engineErrorMsg } from './api'
import { formatMessage } from '@/i18n'

/** TeX 工具链失败（后端 `texdiag`）：结构化 params 进文案，`{{error}}` 仍是 traceback 末行 */
const TEX = (code: string, params: Record<string, unknown>, traceback = 'RuntimeError: latex was not able to process') =>
  new EngineError('脚本执行失败: …', traceback, code, '', undefined, undefined, { params })

describe('engineErrorMsg 带结构化 params', () => {
  it('缺宏包：文案里点名缺的文件，不说成缺 Python 包的模块名', () => {
    const text = formatMessage(engineErrorMsg(TEX('tex_package_missing', { command: 'latex', file: 'type1ec.sty' })))
    expect(text).toContain('type1ec.sty')
    expect(text).not.toContain('{{')
  })

  it('缺命令：点名命令', () => {
    const text = formatMessage(engineErrorMsg(TEX('tex_command_missing', { command: 'latex' })))
    expect(text).toContain('latex')
    expect(text).not.toContain('{{')
  })

  it('params 里同名的 error 盖不掉 traceback 末行', () => {
    const text = formatMessage(
      engineErrorMsg(TEX('tex_failed', { error: 'from-params' }, 'Traceback\nValueError: real last line')),
    )
    expect(text).toContain('ValueError: real last line')
    expect(text).not.toContain('from-params')
  })
})
