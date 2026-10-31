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

  it('项目外文件夹读不了：点名文件夹，不露占位符', () => {
    const text = formatMessage(
      engineErrorMsg(TEX('import_folder_unreadable', { folder: '.work_deps' }, 'PermissionError: [Errno 13]')),
    )
    expect(text).toContain('.work_deps')
    expect(text).not.toContain('{{')
  })

  it('私有依赖文件夹是为别的 Python 版本装的：点名文件夹与两个版本，不露占位符', () => {
    const text = formatMessage(
      engineErrorMsg(
        TEX('import_folder_abi_mismatch', { folder: '.kegg_python', abi: '3.12', current: '3.14' }, 'ImportError: cannot import name'),
      ),
    )
    expect(text).toContain('.kegg_python')
    expect(text).toContain('3.12')
    expect(text).toContain('3.14')
    expect(text).not.toContain('{{')
  })
})
