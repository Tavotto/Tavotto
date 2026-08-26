import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { backendErrorText, patchProjectSettings } from '@/lib/api'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { TextInput } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import { InlineWarning, SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 项目与路径。当前项目 / 脚本数 / 导出目录 / 备份目录 / 只读四件事保留；
 * 「默认路径规则」「目录怎么解析」「设置按项目保存」进问号。
 *
 * **只读模式的副作用留在页面上**（InlineWarning）：它改变了「写回原始文件」
 * 这条会碰磁盘的能力，属于「当前设置产生的重要副作用」，不许折叠。
 */
export function ProjectSettings() {
  useTranslation('dialogs')
  const project = useProjectStore((s) => s.project)
  const [exportDir, setExportDir] = useState(project?.settings?.export_dir ?? '')
  const [backupDir, setBackupDir] = useState(project?.settings?.backup_dir ?? '')
  const [error, setError] = useState<string | null>(null)
  const readonly = project?.settings?.allow_write_back === false

  const save = async (patch: Parameters<typeof patchProjectSettings>[0]) => {
    setError(null)
    try {
      const res = await patchProjectSettings(patch)
      useProjectStore.setState((s) =>
        s.project
          ? {
              project: {
                ...s.project,
                settings: res.settings,
                export_dir: res.export_dir,
                backup_dir: res.backup_dir,
              },
            }
          : s,
      )
    } catch (e) {
      setError(backendErrorText(e))
    }
  }

  return (
    <SettingSection>
      <SettingRow label={st('project.current')} help={st('project.currentHint')}>
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2"
          title={project?.figures_dir}
        >
          {project?.figures_dir ?? '—'}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            useUiStore.getState().setSettingsOpen(false)
            useProjectStore.setState({ phase: 'none' }) // Picker 接管；可从最近项目回来
          }}
        >
          {st('project.switch')}
        </Button>
      </SettingRow>

      <SettingRow label={st('project.scripts')} help={st('project.scriptsHint')}>
        <span className="flex-1 text-xs text-ink-2">
          {st('project.scriptCount', { count: project?.scripts ?? 0 })}
          {(project?.scripts ?? 0) === 0 && st('project.noScriptsSuffix')}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            useUiStore.getState().setSettingsOpen(false)
            useUiStore.getState().setRegistryOpen(true)
          }}
        >
          {st('project.registry')}
        </Button>
      </SettingRow>

      <SettingRow label={st('project.exportDir')} help={st('project.dirHint')}>
        <TextInput
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          onBlur={() => void save({ export_dir: exportDir })}
          placeholder={st('project.defaultPlaceholder', { path: project?.export_dir ?? 'exports/' })}
          className="flex-1"
        />
      </SettingRow>

      <SettingRow label={st('project.backupDir')} help={st('project.backupDirHint')}>
        <TextInput
          value={backupDir}
          onChange={(e) => setBackupDir(e.target.value)}
          onBlur={() => void save({ backup_dir: backupDir })}
          placeholder={st('project.defaultPlaceholder', {
            path: project?.backup_dir ?? 'cache/original_backups/',
          })}
          className="flex-1"
        />
      </SettingRow>

      <SettingRow
        label={st('project.readOnly')}
        help={st('project.readOnlyHelp')}
        danger={readonly}
        status={readonly ? undefined : st('project.writeBackAllowed')}
      >
        <Toggle
          checked={readonly}
          onChange={(v) => void save({ allow_write_back: !v })}
          aria-label={st('project.readOnlyAria')}
        />
      </SettingRow>
      {/* 副作用一句话，常驻——它决定「写回原始文件」这条会碰磁盘的能力在不在 */}
      {readonly && <InlineWarning>{st('project.readOnlyHint')}</InlineWarning>}

      {error && <InlineWarning tone="danger">{error}</InlineWarning>}
    </SettingSection>
  )
}
