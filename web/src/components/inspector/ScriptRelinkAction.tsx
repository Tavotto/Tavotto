import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '@/components/ui/Button'
import { Menu, MenuItem, MenuSeparator, MenuSub } from '@/components/ui/Menu'
import { t as translate } from '@/i18n'
import { fetchRelinkCandidates, type RelinkCandidates } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'
import { relinkScript, relinkTarget } from '@/store/scriptRelink'
import type { PanelObject } from '@/types/document'

const tr = (key: string, params?: Record<string, unknown>) => translate(key, { ns: 'inspector', ...params })

/**
 * 「找不到这个脚本的文件」那张错误卡里接回脚本的出口（ADR 0211 第一期 6）——与「指认数据」「选运行目录」同一处：
 * 消息下面一行（不挤主按钮位的宽：长路径会把消息压成一条窄列），不加横条。
 *
 * * 项目里恰好一份脚本与登记时的内容指纹字节相同 → 一颗「接回 scripts/」（改了名是「接回 new.py」，见 `relinkTarget`）；
 * * 没有指纹（老登记）/ 对不上 / 不止一份 → 「选一个脚本接上」，名单 = 字节相同的 + 静态扫描认领同一张图的候选
 *   （就绪度事实 `capability.candidates`，不按文件名或修改时间猜），另有「其他脚本」可以手动指认项目里任何一份；
 * * 问不到 / 不是这种情况（旧后端、脚本其实在）→ 不出现（卡上照旧只有「重试渲染」）。
 *
 * 接回走 `relinkScript`：跑成了才改登记，没接上原卡留着。
 */
export function ScriptRelinkAction({ panel, script }: { panel: PanelObject; script: string }) {
  useTranslation('inspector')
  const [found, setFound] = useState<RelinkCandidates | null>(null)
  const [busy, setBusy] = useState(false)
  const candidates = useAssetStore((s) => s.byId[panel.fileId]?.capability?.candidates)
  useEffect(() => {
    let live = true
    setFound(null)
    void fetchRelinkCandidates(script, currentProjectId()).then((r) => {
      if (live) setFound(r)
    })
    return () => {
      live = false
    }
  }, [script])

  if (!found?.missing) return null

  const link = (target: string) => {
    setBusy(true)
    void relinkScript(panel, script, target).finally(() => setBusy(false))
  }
  if (found.matches.length === 1) {
    const target = found.matches[0]
    const shown = relinkTarget(script, target)
    return (
      <div className="mt-1.5 flex">
        {/* 常态与正文左对齐（不左内缩）：按钮的左内边距用负外边距抵掉，悬停底色向左伸出去 */}
        <Button
          size="sm"
          className="-ml-2.5 whitespace-nowrap"
          disabled={busy}
          onClick={() => link(target)}
          title={target}
          data-script-relink={target}
        >
          <Link2 size={ICON_SIZE.xs} />
          {shown.kind === 'root' ? tr('relink.toRoot') : tr('relink.to', { target: shown.text })}
        </Button>
      </div>
    )
  }
  const listed = [...new Set([...found.matches, ...(candidates ?? []).filter((c) => c !== script)])]
  const others = found.scripts.filter((s) => !listed.includes(s))
  return (
    <div className="mt-1.5 flex min-w-0">
      <Menu
        align="start"
        width={240}
        trigger={
          <Button size="sm" className="-ml-2.5" disabled={busy} data-script-relink-choose>
            <Link2 size={ICON_SIZE.xs} />
            {tr('relink.choose')}
          </Button>
        }
      >
        {listed.map((s) => (
          <MenuItem key={s} data-script-relink-option={s} onSelect={() => link(s)}>
            {s}
          </MenuItem>
        ))}
        {listed.length > 0 && others.length > 0 && <MenuSeparator />}
        {others.length > 0 && (
          <MenuSub label={tr('relink.otherScripts')} width={240} data-script-relink-others>
            {others.map((s) => (
              <MenuItem key={s} data-script-relink-option={s} onSelect={() => link(s)}>
                {s}
              </MenuItem>
            ))}
          </MenuSub>
        )}
      </Menu>
    </div>
  )
}
