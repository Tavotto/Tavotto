import { useTranslation } from 'react-i18next'
import { CircleQuestionMark, FileCodeCorner, RotateCcw } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import { overrideCounts } from '@/lib/overrideCounts'
import { clearOverrides, resetOverrides } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { IconButton } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'
import { Popover } from '../ui/Popover'

const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 属性栏身份头下面那一行：**脚本在场**。
 *
 * 产品原则第一条是「脚本是源，界面从不遮掩这一点」，而 2026-09-12 的 critique 实测
 * 整张页面的文字里没有任何 `.py`——用户改完字号不知道改动存在哪、脚本有没有变，
 * 最自然的下一步是回 Codex 再改一遍脚本，于是同一处修改存了两层。aha 时刻的后半句
 * 「脚本一个字没动」得有人说出口，就是这一行：
 *
 *     ⌘ fig1_kinetics.py · 脚本未改动                     [↺▾] [?]
 *
 * 右端两颗钮：
 *   * ↺ 恢复菜单——「恢复此元素 · n 项」「恢复整张图 · m 项」。它们原来住在
 *     「源文件与高级」折叠区两层之下（高手会直接 ⌘Z，恢复入口对他等于不存在）。
 *     搬到这里之后，折叠区里只剩**会动磁盘**的动作（写回 / 历史 / 同步），
 *     「只改文档」与「会动磁盘」的边界从一个组标题变成了两个位置。
 *     没有任何修改时这颗钮不出现——与字段级 ↺ 同一条纪律。
 *   * ? 「修改保存在哪里」——原来是折叠区底部的一行文字钮，现在跟着脚本名走。
 *
 * 只在面板有脚本时渲染：没有脚本的 PDF / 位图素材谈不上「脚本未改动」。
 */
export function SourceRow({ panel, gid }: { panel: PanelObject; gid?: string | null }) {
  useTranslation('inspector')
  if (!panel.script) return null
  // 两颗恢复项各说各的对象与数量，数字来自同一份判据（审计 T32）
  const counts = overrideCounts(panel.overrides, gid)
  const script = panel.script.split('/').pop() ?? panel.script

  return (
    <div className="mt-1 flex h-7 items-center gap-1.5" data-source-row>
      <FileCodeCorner size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
      <span className="flex min-w-0 items-baseline gap-1 text-xs">
        <span className="min-w-0 truncate font-mono text-ink-2" title={panel.script}>
          {script}
        </span>
        <span className="shrink-0 text-ink-3" aria-hidden>
          ·
        </span>
        <span className="shrink-0 text-ink-3">{el('sourceRow.scriptUntouched')}</span>
      </span>
      <span className="ml-auto flex shrink-0 items-center">
        {counts.figure > 0 && (
          <Menu
            width={196}
            align="end"
            trigger={
              <IconButton
                iconSize="sm"
                label={el('sourceRow.restoreMenu')}
                tip={el('sourceRow.restoreMenuTip', { count: counts.figure })}
                data-source-restore
              >
                <RotateCcw size={ICON_SIZE.sm} className="text-ink-3" />
              </IconButton>
            }
          >
            {gid && counts.element > 0 && (
              <MenuItem
                onSelect={() =>
                  clearOverrides(
                    panel.id,
                    msg('element.resetElement', undefined, 'inspector'),
                    panel.overrides
                      .filter((o) => o.gid === gid)
                      .map((o) => ({ gid: o.gid, prop: o.prop })),
                  )
                }
              >
                {el('resetElementCount', { count: counts.element })}
              </MenuItem>
            )}
            <MenuItem onSelect={() => resetOverrides(panel.id)}>
              {el('resetToScriptCount', { count: counts.figure })}
            </MenuItem>
          </Menu>
        )}
        <HowItWorks />
      </span>
    </div>
  )
}

/** 「修改保存在哪里？」——四段说明：图内修改 / 写回 / 改图助手 / 两者相遇时 */
function HowItWorks() {
  useTranslation('inspector')
  return (
    <Popover
      width={268}
      align="end"
      trigger={
        <IconButton iconSize="sm" label={el('howItWorksTrigger')} className="-mr-1">
          <CircleQuestionMark size={ICON_SIZE.sm} className="text-ink-3" />
        </IconButton>
      }
    >
      <div className="flex flex-col gap-2 text-xs leading-relaxed text-ink-2">
        {(['howOverride', 'howWriteBack', 'howAi', 'howBoth'] as const).map((key, i) => (
          <div key={key}>
            {i > 0 && <div className="mb-2 h-px bg-border" />}
            <p className="font-medium text-ink">{el(`${key}Title`)}</p>
            <p className="mt-0.5">{el(`${key}Body`)}</p>
          </div>
        ))}
      </div>
    </Popover>
  )
}
