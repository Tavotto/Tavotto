import { useSyncExternalStore } from 'react'
import { patchProjectSettings } from '@/lib/api'
import { useProjectStore } from '@/store/projectStore'

/** 项目身份：后端给的短 id，老后端没有时退回项目目录 */
const identityOf = () => {
  const p = useProjectStore.getState().project
  return p ? (p.id ?? p.figures_dir ?? '') : null
}

/** 在飞的写入个数：UI 据此在「正在更新…」期间挡住依赖这些设置的动作（导出键） */
let pending = 0
const listeners = new Set<() => void>()
const bump = (d: number) => {
  pending += d
  listeners.forEach((fn) => fn())
}
/** 项目设置正在写入（导出位置的写入没落地前，导出会写到旧目录） */
export function useProjectSettingsPending(): boolean {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => pending > 0,
    () => false,
  )
}

/**
 * 改项目设置（导出目录 / 备份目录 / 写回权限）并把后端回的结果写进 `projectStore`。
 * 设置页的 `ProjectSettings` 与导出对话框的「位置 › 更改…」共用这一条：两处改的是同一个
 * 项目级字段（`project.settings.export_dir`），不是两份。失败原样抛出，由调用方说人话。
 *
 * **响应回来时项目已经换了（请求在飞时切到别的项目）就丢弃**：请求是带着发起时的项目身份发给 A 的，
 * 把 A 的回执套到此刻的 `project`（B）上，B 就显示 A 的导出 / 备份目录。
 */
export async function applyProjectSettingsPatch(patch: Parameters<typeof patchProjectSettings>[0]) {
  const origin = identityOf()
  bump(1)
  try {
    const res = await patchProjectSettings(patch)
    if (identityOf() !== origin) return
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
  } finally {
    bump(-1)
  }
}
