/**
 * 导出对话框「输出 › 位置」：导出之前就看得见文件会落到哪（至少两级、过长从左省略、家目录缩成 ~，
 * 全路径在 title），点「更改…」**直接弹选择文件夹**——桌面版是系统原生选择器（`pickDirectory`），
 * 浏览器版是服务器端目录浏览器（`DirBrowser`）。
 *
 * 选完改的是**项目的导出文件夹**（`project.settings.export_dir`，与设置页 › 项目 › 导出位置同一个字段，
 * 同一条 `applyProjectSettingsPatch`）：后端导出就读它，没有「只这一次」的第二个通道；设置页里
 * 「恢复默认」照旧能退回去。取消什么都不变。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { abbreviateHome } from '@/lib/pathDisplay'
import { applyProjectSettingsPatch, useProjectSettingsPending } from '@/lib/projectSettingsPatch'
import { t as translate } from '@/i18n'
import { useProjectStore } from '@/store/projectStore'
import { DirBrowser } from './DirBrowser'
import { FormRow } from './FormRow'
import { Button } from './ui/Button'

/** 目录的上一级（去掉末级）；根 / 盘符根 / 没有上一级时 undefined */
export function parentDir(dir: string): string | undefined {
  const trimmed = dir.replace(/[\\/]+$/, '')
  const i = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))
  if (i < 0) return undefined
  const parent = trimmed.slice(0, i)
  if (parent === '') return '/'
  return /^[A-Za-z]:$/.test(parent) ? parent + dir[i] : parent
}

const ex = (key: string) => translate(`export.${key}`, { ns: 'dialogs' })

export function ExportLocationRow({ disabled = false }: { disabled?: boolean }) {
  useTranslation('dialogs')
  const exportDir = useProjectStore((s) => s.project?.export_dir ?? null)
  const homeDir = useProjectStore((s) => s.project?.home_dir ?? null)
  // 写入没落地前位置还是旧的：说「正在更新…」，导出键由对话框同一个信号挡住（否则会写到旧目录）
  const updating = useProjectSettingsPending()
  const [browsing, setBrowsing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const choose = async (dir: string) => {
    setError(null)
    try {
      await applyProjectSettingsPatch({ export_dir: dir })
    } catch (e) {
      setError(backendErrorText(e))
    }
  }
  const change = async () => {
    if (isDesktop()) {
      const picked = await pickDirectory(ex('locationPickTitle'))
      if (picked != null) await choose(picked) // 取消不是错误，什么都不变
    } else {
      setBrowsing(true)
    }
  }
  return (
    <>
      <FormRow label={ex('locationLabel')}>
        {/* dir=rtl：放不下时从左边省略、末尾两级（`…/项目/export`）始终在；两端的 U+200E 把路径钉在从左到右 */}
        <span
          data-export-location
          data-updating={updating ? '' : undefined}
          dir="rtl"
          className="min-w-0 flex-1 truncate text-left text-sm text-ink-2"
          title={exportDir ?? undefined}
        >
          {updating ? ex('locationUpdating') : exportDir ? '‎' + abbreviateHome(exportDir, homeDir) + '‎' : ex('locationDefault')}
        </span>
        <Button variant="ghost" size="sm" data-export-location-change disabled={disabled || updating} onClick={() => void change()}>
          {ex('locationChange')}
        </Button>
      </FormRow>
      {error && (
        <FormRow>
          <p data-export-location-error className="text-sm text-danger-content">
            {error}
          </p>
        </FormRow>
      )}
      {browsing && (
        <DirBrowser
          mode="open"
          title={ex('locationPickTitle')}
          // 从当前导出目录的上一级打开、把它高亮——一眼看到同级；上一级打不开就回落到它自己
          initialPath={(exportDir && parentDir(exportDir)) || exportDir || undefined}
          fallbackPath={exportDir ?? undefined}
          highlight={exportDir ?? undefined}
          onClose={() => setBrowsing(false)}
          onPick={(path) => {
            setBrowsing(false)
            void choose(path)
          }}
        />
      )}
    </>
  )
}
