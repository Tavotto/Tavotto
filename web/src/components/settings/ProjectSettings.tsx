import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { backendErrorText, patchProjectSettings } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { FolderOpen } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { Reveal } from '../ui/Field'
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
 *
 * Session 6 的形态：**值在标题列、动作在控件列**。「当前项目」「可编辑来源」的值
 * （目录名 / 脚本数）是这一行的现状，落在标题下的 `status`；「切换项目…」「管理来源…」
 * 是明确的 secondary 动作，独占控件列。两条目录行同一语法（2026-09-15 打磨批次 B）：
 *
 * ```text
 * 导出位置                                     [恢复默认] [更改…]
 * 实际位置  › export  复制
 * （点「更改…」才在这里展开输入框）
 * ```
 *
 * 写回开关**开着**时也说一句（`status`，低调的一行，不套框）：修改可直接写入原始
 * 脚本——这是这一页唯一会碰用户源文件的设置；关着时那句副作用照旧是 InlineWarning。
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

  // 三段（2026-09-13 审计 B33）：项目 → 位置 → 写回源图。写回是会碰原始文件的
  // 高影响能力，与导出位置同级并排时看不出它的分量；单独一段，说清会覆盖什么、
  // 备份去哪
  return (
    <>
    {/* 「只影响这个项目」删了（全面打磨 D37）：导航项与分区标题都已经叫「项目」，
        这句说明只是把同一个词换个说法再写一遍 */}
    <SettingSection title={st('project.sectionProject')}>
      <SettingRow
        label={st('project.current')}
        status={<PathValue path={project?.figures_dir} name={st('project.current')} />}
      >
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
      </SettingRow>

      {/* 「只有登记过的脚本，其产出的图才能进入图内编辑」是登记规则，属于
          注册表对话框自己的事。这一行只报结果：有几个可编辑来源 */}
      <SettingRow
        label={st('project.scripts')}
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
    </SettingSection>

    <SettingSection title={st('project.sectionLocations')}>
      <DirectoryRow
        id="setting-export-dir"
        label={st('project.exportDir')}
        value={exportDir}
        onValue={setExportDir}
        onCommit={(v) => void save({ export_dir: v })}
        effective={project?.export_dir}
      />
      <DirectoryRow
        id="setting-backup-dir"
        label={st('project.backupDir')}
        value={backupDir}
        onValue={setBackupDir}
        onCommit={(v) => void save({ backup_dir: v })}
        effective={project?.backup_dir}
      />
    </SettingSection>

    <SettingSection title={st('project.sectionWriteBack')}>
      {/* 副作用那句话（覆盖什么、备份去哪）从分区说明降到行的 `status`（全面打磨 D37，§13
          「开关开着时的低调提醒是 status」）：它只在写回真的开着时成立，常驻在分区标题下面
          的话，关掉写回的人也要读一遍一件不会发生的事 */}
      <SettingRow
        label={st('project.allowWriteBack')}
        status={allowWriteBack ? st('project.writeBackDesc') : undefined}
        controlId="setting-allow-write-back"
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
    </>
  )
}

/**
 * 一个目录设置（2026-09-15 打磨批次 B，行语法 L1 / L2）：值在标题列、动作在控件列——
 * 标题下面一行是**这一刻真正会用的位置**，控件列是「更改…」；点开才在标题列下方展开
 * 输入框（桌面版多一颗系统选择器），设过之后控件列多一颗「恢复默认」。此前两条目录
 * 各占一个整行宽的空输入框，只为了表达「留空 = 默认」。
 *
 * 输入框留着不是为了对称：浏览器模式没有原生选择器（`pickDirectory()` 在那里
 * 返回 null），手敲路径是那条路上唯一的改法。所以「选择…」只在桌面版渲染——
 * 一个点了什么都不发生的按钮比没有这个按钮更坏。
 */
function DirectoryRow({
  id,
  label,
  value,
  onValue,
  onCommit,
  effective,
}: {
  id: string
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
    <SettingRow
      label={label}
      controlId={id}
      status={
        <span className="flex min-w-0 flex-col">
          <span className="flex min-w-0 items-center gap-1.5">
            {st('project.effectivePath')}
            <PathValue path={effective} name={label} />
          </span>
          <Reveal open={editing}>
            <span id={editorId} className="flex min-w-0 items-center gap-1.5 pb-1 pt-1.5">
              <TextInput
                id={id}
                value={value}
                autoFocus
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
          </Reveal>
        </span>
      }
    >
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
      <Button
        variant="secondary"
        size="sm"
        aria-expanded={editing}
        aria-controls={editing ? editorId : undefined}
        onClick={() => setEditing((v) => !v)}
      >
        {st('project.change')}
      </Button>
    </SettingRow>
  )
}
