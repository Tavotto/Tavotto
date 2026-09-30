import { useTranslation } from 'react-i18next'
import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestGroup } from '@/lib/api'
import { groupTransformBlocked, resolveGroup, type GroupBlockReason } from '@/lib/elementGeom'
import { useExactPanelManifest, usePanelRender } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Section } from '../ui/Field'
import { ElementInspector, ScaleField } from './ElementInspector'
import { groupByGid } from './roles/hierarchy'
import { roleIcon } from './roles/roleIcons'
import { engineLabel } from './roles/registry'

/** 本文件的文案在 inspector:modelGroup.* 下 */
/** 组页布局一节按「不能整体变换」的原因说的话 */
const GROUP_BLOCKED_LAYOUT: Record<GroupBlockReason, string> = {
  not_resizable: 'layoutLocked',
  locked: 'layoutMemberLocked',
  incomplete: 'layoutIncomplete',
}

const gt = (key: string, values?: Record<string, unknown>) =>
  translate(`modelGroup.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 图内编辑态属性区的入口：单选一个组 → 组页；其余一切照旧交给 `ElementInspector`。
 * 组不在 `manifest.elements` 里，`ElementInspector` 按 gid 找不到它会退回「整张图」——
 * 那正是「选中组却改到整张图」的误触，所以分流在这一层做。
 */
export function PanelElementPage({ panel }: { panel: PanelObject }) {
  const selectedGids = useUiStore((s) => s.selectedGids)
  const manifest = usePanelRender(panel)?.manifest
  const group = selectedGids.length === 1 ? groupByGid(manifest, selectedGids[0]) : undefined
  return manifest && group ? (
    <GroupPage panel={panel} manifest={manifest} group={group} />
  ) : (
    <ElementInspector panel={panel} />
  )
}

/**
 * 选中一个**真实的组**（`Manifest.groups`，第一阶段只有共享色条）时的属性页。
 *
 * 组只有结构与布局两件事，所以这一页只回答三个问题：
 *
 * 1. **里面有谁**——成员逐个列出，点一下选中它（色条轴直接换成它的色条元素，与
 *    画布上点色条轴同一个处置）；
 * 2. **颜色从哪来**——色条的色图 / 上下限来自它的 mappable（`mappable_gid`），不是组。
 *    换色图要去色条或那张图上改，这里只给「选中它」的入口，不摆第二套色图控件；
 * 3. **整体缩放**——与多选几个子图的成组缩放同一个控件、同一个参照框（成员 position
 *    的并集），一次 commit 写成成员各自的 position。平移在画布上：拖组里任一成员。
 *
 * 几何写操作只认权威那一份（issue #131）：权威缺席时缩放置灰并说明。
 */
export function GroupPage({
  panel,
  manifest,
  group,
}: {
  panel: PanelObject
  manifest: Manifest
  group: ManifestGroup
}) {
  useTranslation('inspector')
  const exactManifest = useExactPanelManifest(panel)
  const layout = exactManifest ? resolveGroup(panel, exactManifest, [group.gid]) : null
  // 不能整体变换的原因：与拖动 / 组框手柄 / 方向键同一个判据。「成员不全」只按权威那一份判，
  // 权威没到时先说同步中（非权威的元素表可能正缺着成员）
  const reason = groupTransformBlocked(panel, exactManifest ?? manifest, group)
  const blocked = !exactManifest && reason === 'incomplete' ? null : reason
  const select = (gid: string) => useUiStore.getState().setSelectedGid(gid)
  const colorbar = manifest.elements.find((e) => e.gid === group.colorbar_gid)
  const mappable = group.mappable_gid
    ? manifest.elements.find((e) => e.gid === group.mappable_gid)
    : undefined

  const members = group.members
    .map((gid) => manifest.elements.find((e) => e.gid === gid))
    .filter((e): e is NonNullable<typeof e> => !!e)
    // 色条轴本身没什么可调的，成员列表里直接给它的色条（属性页换人的同一条规则）
    .map((e) =>
      e.is_colorbar && e.colorbar_gid
        ? (manifest.elements.find((c) => c.gid === e.colorbar_gid) ?? e)
        : e,
    )

  return (
    <div data-group-page={group.gid}>
      <Section plainTitle title={gt('membersTitle', { count: members.length })}>
        <ul className="flex flex-col gap-0.5">
          {members.map((m) => {
            const Icon = roleIcon(m.role)
            return (
              <li key={m.gid}>
                <Button
                  size="sm"
                  className="w-full justify-start px-1.5 text-ink-2"
                  data-group-member={m.gid}
                  onClick={() => select(m.gid)}
                >
                  <Icon size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-left">{engineLabel(m.label)}</span>
                  <ChevronRight size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
                </Button>
              </li>
            )
          })}
        </ul>
      </Section>

      {colorbar && (
        <Section plainTitle title={gt('colorSourceTitle')}>
          <p className="text-xs leading-relaxed text-ink-3">
            {mappable
              ? gt('colorSourceFrom', {
                  colorbar: engineLabel(colorbar.label),
                  source: engineLabel(mappable.label),
                })
              : gt('colorSourceUnknown', { colorbar: engineLabel(colorbar.label) })}
          </p>
          {mappable && (
            <Button
              size="sm"
              className="mt-1 -ml-1.5 px-1.5 text-ink-2"
              data-group-color-source={mappable.gid}
              onClick={() => select(mappable.gid)}
            >
              <span className="truncate">{gt('selectColorSource', { label: engineLabel(mappable.label) })}</span>
              <ChevronRight size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
            </Button>
          )}
        </Section>
      )}

      <Section plainTitle title={translate('group.layout', { ns: 'inspector' })}>
        {blocked ? (
          // 整组不能变换：缩放控件不摆，按原因说清楚
          <p className="text-xs leading-relaxed text-ink-3" data-group-blocked={blocked}>
            {gt(GROUP_BLOCKED_LAYOUT[blocked])}
          </p>
        ) : !layout ? (
          <p className="text-xs leading-relaxed text-ink-3">{gt('layoutSyncing')}</p>
        ) : (
          <>
            <ScaleField panel={panel} group={layout} />
            <p className="mt-2 text-xs leading-relaxed text-ink-3">{gt('layoutHint')}</p>
          </>
        )}
      </Section>
    </div>
  )
}
