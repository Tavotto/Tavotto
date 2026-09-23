import { t as translate } from '@/i18n'
import type { UserEnvironmentSource } from '@/lib/api'

/**
 * 用户环境（ADR 0079）的名字：依赖弹窗的每一行与通知轨上「已改用……」说的是同一句，只有这一份实现。
 * 文案键写成字面量：i18n 的死键门禁按「源码里出现过这个串」判活。
 */
const SOURCE_NAME: Record<UserEnvironmentSource, string> = {
  vscode: 'engine.userEnvSource_vscode',
  python_version_file: 'engine.userEnvSource_python_version_file',
  environment_yml: 'engine.userEnvSource_environment_yml',
  shebang: 'engine.userEnvSource_shebang',
  login_shell: 'engine.userEnvSource_login_shell',
  conda: 'engine.userEnvSource_conda',
  pyenv: 'engine.userEnvSource_pyenv',
  system: 'engine.userEnvSource_system',
}

/** 老后端 / 不认识的来源退回「系统 Python」那一句 */
export const userEnvironmentName = (env: {
  source: UserEnvironmentSource | string
  label: string
}): string =>
  translate(SOURCE_NAME[env.source as UserEnvironmentSource] ?? SOURCE_NAME.system, {
    ns: 'errors',
    label: env.label,
  })
