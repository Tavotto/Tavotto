import { patchProjectSettings } from '@/lib/api'
import { useProjectStore } from '@/store/projectStore'

/**
 * 改项目设置（导出目录 / 备份目录 / 写回权限）并把后端回的结果写进 `projectStore`。
 * 设置页的 `ProjectSettings` 与导出对话框的「位置 › 更改…」共用这一条：两处改的是同一个
 * 项目级字段（`project.settings.export_dir`），不是两份。失败原样抛出，由调用方说人话。
 */
export async function applyProjectSettingsPatch(patch: Parameters<typeof patchProjectSettings>[0]) {
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
}
