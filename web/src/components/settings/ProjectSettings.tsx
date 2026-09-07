import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { backendErrorText, patchProjectSettings } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { TextInput } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import { PathValue } from './PathValue'
import { InlineWarning, SettingRow, SettingSection, settingRowLabelId } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 项目与路径。
 *
 * 三处改动都来自审计 T40 / 说明文字专项补查：
 *
 * 1. **路径要能核实。** 改动前每条路径是一行 `truncate` 的绝对路径，被截掉的
 *    恰好是末尾——而末尾那一级才是人认得出的那个名字。现在默认只显示末级
 *    目录，完整路径按需展开、旁边可复制（`PathValue`）。
 * 2. **默认值由控件表达。** 「目录留空 = 使用默认位置」原本是问号里的一句话；
 *    现在输入框下面直接写着这一刻真正会写到哪儿，桌面版还给系统文件夹选择器，
 *    设过之后多一个「恢复默认」。
 * 3. **开关按结果命名。** 「项目只读」这个名字比它管的范围大得多——它只关掉
 *    「写回原始文件」那条路径，画布编辑与导出照常。而且它是个反向开关：关着
 *    的时候旁边写「允许写回原始文件」，用户得在脑子里做一次否定。现在开关就叫
 *    「允许写回原始文件」，关掉时那句副作用常驻。
 *
 * **后端字段名一个字没动**（仍是 `allow_write_back`）：这里改的是界面表达。
 * 关掉之后写回按钮是真的停用（`inspector/UpdateSourceButton` 读同一个字段），
 * `settingsCopy.test.tsx` 里有一条用例把设置页的开关与那两个按钮连起来判。
 */
export function ProjectSettings() {
  useTranslation('dialogs')
  const project = useProjectStore((s) => s.project)
  const [exportDir, setExportDir] = useState(project?.settings?.export_dir ?? '')
  const [backupDir, setBackupDir] = useState(project?.settings?.backup_dir ?? '')
  const [error, setError] = useState<string | null>(null)
  // 后端只在明确写了 false 时才是「不许写回」：字段缺失 = 允许（老项目）
  const allowWriteBack = project?.settings?.allow_write_back !== false

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
      <SettingRow label={st('project.current')}>
        <PathValue
          path={project?.figures_dir}
          name={st('project.current')}
          className="flex-1"
        />
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

      {/* 「只有登记过的脚本，其产出的图才能进入图内编辑」是登记规则，属于
          注册表对话框自己的事。这一行只报结果：有几个可编辑来源 */}
      <SettingRow label={st('project.scripts')}>
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

      <DirectoryRow
        id="setting-export-dir"
        label={st('project.exportDir')}
        description={st('project.exportDirScope')}
        value={exportDir}
        onValue={setExportDir}
        onCommit={(v) => void save({ export_dir: v })}
        effective={project?.export_dir}
      />
      <DirectoryRow
        id="setting-backup-dir"
        label={st('project.backupDir')}
        description={st('project.backupDirScope')}
        value={backupDir}
        onValue={setBackupDir}
        onCommit={(v) => void save({ backup_dir: v })}
        effective={project?.backup_dir}
      />

      <SettingRow
        label={st('project.allowWriteBack')}
        description={st('project.allowWriteBackScope')}
        controlId="setting-allow-write-back"
        danger={!allowWriteBack}
      >
        <Toggle
          aria-labelledby={settingRowLabelId('setting-allow-write-back')}
          id="setting-allow-write-back"
          checked={allowWriteBack}
          onChange={(v) => void save({ allow_write_back: v })}
        />
      </SettingRow>
      {/* 副作用一句话，常驻——它决定「写回原始文件」这条会碰磁盘的能力在不在 */}
      {!allowWriteBack && <InlineWarning>{st('project.writeBackOffHint')}</InlineWarning>}

      {error && <InlineWarning tone="danger">{error}</InlineWarning>}
    </SettingSection>
  )
}

/**
 * 一个目录设置：输入框 + 系统选择器（桌面版）+ 恢复默认，下面一行是**这一刻
 * 真正会用的位置**。
 *
 * 输入框留着不是为了对称：浏览器模式没有原生选择器（`pickDirectory()` 在那里
 * 返回 null），手敲路径是那条路上唯一的改法。所以「选择…」只在桌面版渲染——
 * 一个点了什么都不发生的按钮比没有这个按钮更坏。
 */
function DirectoryRow({
  id,
  label,
  description,
  value,
  onValue,
  onCommit,
  effective,
}: {
  id: string
  label: string
  description: string
  /** 用户设过的值（空 = 用默认） */
  value: string
  onValue: (v: string) => void
  onCommit: (v: string) => void
  /** 后端解析出来的、这一刻真正在用的绝对路径 */
  effective?: string
}) {
  return (
    <SettingRow label={label} description={description} controlId={id}>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <TextInput
            id={id}
            value={value}
            onChange={(e) => onValue(e.target.value)}
            onBlur={() => onCommit(value)}
            placeholder={st('project.dirPlaceholder')}
            className="min-w-0 flex-1"
          />
          {isDesktop() && (
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                const picked = await pickDirectory(label)
                if (picked == null) return // 取消不是错误
                onValue(picked)
                onCommit(picked)
              }}
            >
              {st('project.chooseFolder')}
            </Button>
          )}
          {value !== '' && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onValue('')
                onCommit('')
              }}
            >
              {st('project.useDefault')}
            </Button>
          )}
        </span>
        <span className="flex min-w-0 items-center gap-1 text-[11px] text-ink-3">
          {st('project.effectivePath')}
          <PathValue path={effective} name={label} />
        </span>
      </span>
    </SettingRow>
  )
}
