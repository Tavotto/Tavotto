import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AiCapabilities } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '../../ui/Button'
import { FieldGroup, FormSection } from '../../ui/FormSection'
import { CompatibleEndpoint } from './CompatibleEndpoint'
import { cx } from './copy'
import { ConnectionManage } from './ConnectionManage'
import { connectionOf, connectionsOf, isCnFirst, listedVendors } from './vendors'
import { VendorRow } from './VendorRow'

const HAIRLINE = 'relative before:absolute before:inset-x-4 before:top-0 before:h-px before:bg-border'

/**
 * 设置 → 改图助手 的主体：厂商同级列表，分「国内 / 海外」两组（中文界面国内在前），每行一个动作；
 * 底部「其他兼容接口…」。Codex / Claude Code 不再是入口——它们就是这里的「ChatGPT」「Claude」两行（订阅登录）。
 */
export function ConnectionsList({ caps }: { caps: AiCapabilities }) {
  useTranslation('dialogs')
  const [manual, setManual] = useState(false)
  const vendors = listedVendors(caps)
  const groups = (isCnFirst() ? (['cn', 'global'] as const) : (['global', 'cn'] as const))
    .map((region) => ({ region, items: vendors.filter((v) => v.region === region) }))
    .filter((g) => g.items.length > 0)

  return (
    <div data-connect-list className="contents">
      {groups.map((g) => (
        <FormSection key={g.region} title={g.region === 'cn' ? cx('regionCn') : cx('regionGlobal')} data-connect-group={g.region}>
          <FieldGroup>
            <ul className="flex flex-col p-0">
              {g.items.map((v, i) => (
                <VendorRow
                  key={v.id}
                  vendor={v}
                  className={cn(i > 0 && HAIRLINE)}
                  connection={connectionOf(caps, v)}
                  connections={connectionsOf(caps, v)}
                  onConnected={() => undefined}
                  manage={(c) => <ConnectionManage vendor={v} connection={c} />}
                />
              ))}
            </ul>
          </FieldGroup>
        </FormSection>
      ))}
      {(
        <div>
          <Button variant="ghost" size="sm" data-connect-other onClick={() => setManual(true)}>
            {cx('other')}
          </Button>
        </div>
      )}
      {manual && <CompatibleEndpoint onClose={() => setManual(false)} onSaved={() => setManual(false)} />}
    </div>
  )
}
