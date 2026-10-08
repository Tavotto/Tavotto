import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { backendErrorText, patchProjectSettings } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { dirTail } from '@/lib/pathDisplay'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { EngineEnvironmentCard } from '../EngineEnvironmentCard'
import { Copy, Ellipsis, Eye, FolderOpen, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { TextInput } from '../ui/Input'
import { Menu, MenuItem } from '../ui/Menu'
import { Toggle } from '../ui/Toggle'
import { GroupNotice, SettingRow, settingRowLabelId } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 菜单里的「复制完整路径」：菜单项没地方改口说「已复制」，回执走状态条 */
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    useUiStore.getState().setStatus(msg('settings.copied', undefined, 'dialogs'), 'done')
  } catch {
    /* 剪贴板不可用：什么都不说，不谎报已复制 */
  }
}

/**
 * 项目与路径。四组：项目 → Python 与运行 → 位置 → 写回源图。
 *
 * 几条来自审计 T40 / 说明文字专项补查 / 2026-10-07 设计审计 §9.1：
 *
 * 1. **路径要能核实，但现状只放文字。** 默认只显示末级目录（人认得出的那一级，`dirTail`，悬停给全文）；
 *    完整路径与复制收进行尾 ⋯，「显示完整路径」在这一行下面展开一条整行宽的 fill 行。此前现状槽里是一颗
 *    28px 的展开钮 + 一颗复制钮，两条目录行打开编辑器时整行跳 36px。
 * 2. **默认值由控件表达。** 「目录留空 = 使用默认位置」原本是问号里的一句话；现在现状直接写着这一刻真正会
 *    写到哪儿，点「更改…」才在 fill 行展开输入框（桌面版多一颗系统选择器），「恢复默认」在 ⋯ 里——没设过时
 *    停用并说为什么。标签不再是指向未挂载输入框的 `<label htmlFor>`。
 * 3. **开关按结果命名。** 开关就叫「允许写回原始文件」；它管什么（覆盖原始文件、先备份）是**常驻说明**
 *    ——开着关着都成立的事实，不随开关忽隐忽现；关掉时另有一条 warn Notice 说清「写回已停用」。
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
    <>
      {/* 「只影响这个项目」删了（全面打磨 D37）：页名与分区标题都已经叫「项目」 */}
      <FormSection title={st('project.sectionProject')}>
        <FieldGroup>
          <PathRow
            label={st('project.current')}
            path={project?.figures_dir}
            anchor="project.current"
            action={
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  useUiStore.getState().setSettingsOpen(false)
                  useProjectStore.getState().showPicker() // Picker 接管；可从最近项目回来
                }}
              >
                {st('project.switch')}
              </Button>
            }
          />
          {/* 「只有登记过的脚本，其产出的图才能进入图内编辑」是登记规则，属于
              注册表对话框自己的事。这一行只报结果：有几个可编辑来源 */}
          <SettingRow
            label={st('project.scripts')}
            data-settings-anchor="project.scripts"
            status={
              <>
                {st('project.scriptCount', { count: project?.scripts ?? 0 })}
                {(project?.scripts ?? 0) === 0 && st('project.noScriptsSuffix')}
              </>
            }
          >
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                useUiStore.getState().setSettingsOpen(false)
                useUiStore.getState().setRegistryOpen(true)
              }}
            >
              {st('project.registry')}
            </Button>
          </SettingRow>
        </FieldGroup>
      </FormSection>

      {/* 运行设置（用哪个 Python、项目环境、脚本运行目录、记住的数据位置、脚本备份）：一组行，
          由 `EngineEnvironmentCard` 渲染（「渲染环境」对话框用的是同一份） */}
      <FormSection title={st('project.sectionRuntime')} data-settings-anchor="project.runtime">
        <EngineEnvironmentCard />
      </FormSection>

      <FormSection title={st('project.sectionLocations')}>
        <FieldGroup>
          <DirectoryRow
            id="setting-export-dir"
            anchor="project.exportDir"
            label={st('project.exportDir')}
            value={exportDir}
            onValue={setExportDir}
            onCommit={(v) => void save({ export_dir: v })}
            effective={project?.export_dir}
          />
          <DirectoryRow
            id="setting-backup-dir"
            anchor="project.backupDir"
            label={st('project.backupDir')}
            value={backupDir}
            onValue={setBackupDir}
            onCommit={(v) => void save({ backup_dir: v })}
            effective={project?.backup_dir}
          />
        </FieldGroup>
      </FormSection>

      <FormSection title={st('project.sectionWriteBack')}>
        <FieldGroup>
          {/* 它管什么（覆盖原始文件、先备份到上面的位置）是常驻说明：开着关着都成立 */}
          <SettingRow
            label={st('project.allowWriteBack')}
            description={st('project.writeBackDesc')}
            controlId="setting-allow-write-back"
            data-settings-anchor="project.writeBack"
          >
            <Toggle
              aria-labelledby={settingRowLabelId('setting-allow-write-back')}
              id="setting-allow-write-back"
              checked={allowWriteBack}
              onChange={(v) => void save({ allow_write_back: v })}
            />
          </SettingRow>
          {/* 关着时那句副作用常驻——它决定「写回原始文件」这条会碰磁盘的能力在不在 */}
          {!allowWriteBack && (
            <GroupNotice tone="warn" data-write-back-off>
              {st('project.writeBackOffHint')}
            </GroupNotice>
          )}
          {error && <GroupNotice tone="danger">{error}</GroupNotice>}
        </FieldGroup>
      </FormSection>
    </>
  )
}

/**
 * 「路径 + 动作」一行的公共部分：现状 = 末级目录（文字，悬停给全文），行尾 ⋯ 里是「显示完整路径」与「复制
 * 完整路径」（+ 调用方的额外项），完整路径展开在 fill 行。
 */
function PathRow({
  label,
  path,
  anchor,
  action,
  extraMenu,
  status,
  below,
}: {
  label: string
  path?: string | null
  anchor: string
  /** 控件列里 ⋯ 之前的那一颗（「切换项目…」「更改…」） */
  action?: ReactNode
  extraMenu?: ReactNode
  /** 现状前缀（「实际位置」）；缺省只写目录名 */
  status?: ReactNode
  below?: ReactNode
}) {
  const [showFull, setShowFull] = useState(false)
  const tail = path ? dirTail(path) : '—'
  return (
    <SettingRow
      label={label}
      data-settings-anchor={anchor}
      status={
        <span className="flex min-w-0 items-center gap-1.5">
          {status}
          <span data-path-tail className="min-w-0 truncate font-mono" title={path ?? undefined}>
            {tail}
          </span>
        </span>
      }
      below={
        showFull || below ? (
          <div className="flex min-w-0 flex-col gap-2">
            {showFull && path && (
              <p data-path-full className="break-all font-mono text-xs leading-snug text-ink-3">
                {path}
              </p>
            )}
            {below}
          </div>
        ) : undefined
      }
    >
      {action}
      <Menu
        align="end"
        width={220}
        trigger={
          <IconButton label={st('project.pathActions', { name: label })} iconSize="sm" data-path-menu>
            <Ellipsis size={ICON_SIZE.sm} aria-hidden />
          </IconButton>
        }
      >
        <MenuItem icon={Eye} disabled={!path} onSelect={() => setShowFull((v) => !v)}>
          {showFull ? st('project.hideFullPath') : st('project.showFullPath', { name: label })}
        </MenuItem>
        <MenuItem icon={Copy} disabled={!path} onSelect={() => path && void copyText(path)}>
          {st('project.copyPath', { name: label })}
        </MenuItem>
        {extraMenu}
      </Menu>
    </SettingRow>
  )
}

/**
 * 一个目录设置（行语法 L1 / L2）：现状是**这一刻真正会用的位置**，控件列「更改…」+ ⋯（恢复默认 / 显示完整路径 /
 * 复制）；点「更改…」才在 fill 行展开输入框（桌面版多一颗系统选择器）。
 *
 * 输入框留着不是为了对称：浏览器模式没有原生选择器（`pickDirectory()` 在那里
 * 返回 null），手敲路径是那条路上唯一的改法。所以「选择…」只在桌面版渲染——
 * 一个点了什么都不发生的按钮比没有这个按钮更坏。
 */
function DirectoryRow({
  id,
  anchor,
  label,
  value,
  onValue,
  onCommit,
  effective,
}: {
  id: string
  anchor: string
  label: string
  /** 用户设过的值（空 = 用默认） */
  value: string
  onValue: (v: string) => void
  onCommit: (v: string) => void
  /** 后端解析出来的、这一刻真正在用的绝对路径 */
  effective?: string
}) {
  const [editing, setEditing] = useState(false)
  const editorId = `${id}-editor`
  return (
    <PathRow
      label={label}
      path={effective}
      anchor={anchor}
      status={<span className="shrink-0">{st('project.effectivePath')}</span>}
      action={
        <Button
          variant="secondary"
          size="sm"
          aria-expanded={editing}
          aria-controls={editing ? editorId : undefined}
          onClick={() => setEditing((v) => !v)}
        >
          {st('project.change')}
        </Button>
      }
      extraMenu={
        <MenuItem
          icon={RotateCcw}
          disabled={value === ''}
          reason={value === '' ? st('project.alreadyDefault') : undefined}
          onSelect={() => {
            onValue('')
            onCommit('')
          }}
        >
          {st('project.useDefault')}
        </MenuItem>
      }
      below={
        editing ? (
          <span id={editorId} className="flex min-w-0 items-center gap-1.5">
            <TextInput
              id={id}
              value={value}
              autoFocus
              aria-label={label}
              onChange={(e) => onValue(e.target.value)}
              onBlur={() => onCommit(value)}
              placeholder={st('project.dirPlaceholder')}
              className="min-w-0 flex-1"
            />
            {isDesktop() && (
              <IconButton
                variant="secondary"
                label={st('project.chooseFolder')}
                onClick={async () => {
                  const picked = await pickDirectory(label)
                  if (picked == null) return // 取消不是错误
                  onValue(picked)
                  onCommit(picked)
                }}
              >
                <FolderOpen size={ICON_SIZE.md} aria-hidden />
              </IconButton>
            )}
          </span>
        ) : undefined
      }
    />
  )
}
