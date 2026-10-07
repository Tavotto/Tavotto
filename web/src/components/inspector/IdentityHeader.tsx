import type { ComponentType, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronRight,
  Copy,
  Ellipsis,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Layers,
  Lock,
  LockOpen,
  MoveUpRight,
  Square,
  Trash2,
  Type as TypeIcon,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { openProblemAt } from '@/lib/issueFocus'
import { switchKindOf } from '@/lib/shapeSwitch'
import { cn, MOD } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import { deleteSelected, duplicateSelected, hideElement, updateObjects } from '@/store/actions'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { objectLabel, type CanvasObject, type PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'
import { TruncateMiddle } from '../ui/TruncateMiddle'
import { displayLabel, identityCrumbs, untruncatedLabel } from './identityCrumbs'
import { KIND_SWITCH_ICON } from './kindSwitchIcons'
import { ObjectKindSwitch } from './ObjectKindSwitch'
import { RestoreMenu } from './RestoreMenu'
import { ancestorsOf, containerGid, groupByGid, structuralParent } from './roles/hierarchy'
import { groupName, roleName } from './roles/registry'
import { roleIcon } from './roles/roleIcons'

const TYPE_ICON = {
  panel: ImageIcon,
  text: TypeIcon,
  arrow: MoveUpRight,
  shape: Square,
} as const

/**
 * 唯一的上下文头：现在改的是谁、它处于什么状态、对它还能做什么。
 *
 * **固定两行**（2026-10-07 设计审计 §9.2 P1）：上面 24px 一行是路径（祖先面包屑 / 对象类型）+ 状态 chip，
 * 下面 32px 一行是角色图标 + 名字 + ⋯。此前路径行只在有祖先 / 有修改时才出现，切换选择时整栏跳 24px。
 * 两种对象（画布对象 / 图内元素）**同一副右侧簇**：状态 chip（已修改 / 问题 / 已锁定 / 已隐藏）在路径行右端，
 * 命令收进名字行右端的 ⋯。头部下面不再画分隔线——一屏的发丝线只留节与节之间那一条。
 *
 * 图内编辑态的头上**没有退出按钮**：返回排版的唯一入口是画布上方上下文栏的
 * 「返回画布 Esc」（`WorkspaceContextBar`，它还顺手选中该面板）。
 */
export function IdentityHeader({ objs = [], panel }: { objs?: CanvasObject[]; panel?: PanelObject }) {
  return panel ? <ElementIdentity panel={panel} /> : <ObjectIdentity objs={objs} />
}

/** 两行骨架：路径行 24（左路径、右状态 chip）+ 名字行 32（图标、名字、⋯） */
function IdentityFrame({
  path,
  chips,
  icon,
  name,
  menu,
}: {
  path: ReactNode
  chips: ReactNode
  icon: ReactNode
  name: ReactNode
  menu: ReactNode
}) {
  return (
    <header data-identity className="mx-3 mb-1 flex shrink-0 flex-col pt-2">
      {/* 路径行：p 是稳定结构（用例按 `header p` 找面包屑），一行 24 高 */}
      <p data-identity-path className="flex h-6 min-w-0 items-center gap-1.5 text-sm text-ink-3">
        <span className="flex min-w-0 flex-1 items-center gap-1">{path}</span>
        {chips}
      </p>
      <div className="flex min-h-8 items-center gap-2">
        {/* 角色图标底座：圆角 md（8），与行 / 框同一档（此前 rounded-sm 与圆形图标钮不一致） */}
        <span
          data-identity-icon
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface-hover text-ink-2"
        >
          {icon}
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-1.5">{name}</div>
        {menu}
      </div>
    </header>
  )
}

/** 名字：type-heading，放不下折两行，完整值在 title 里 */
function NameHeading({ children, title }: { children: string; title?: string }) {
  return (
    <h2 title={title ?? children} className="type-heading line-clamp-2 min-w-0 break-words">
      {children}
    </h2>
  )
}

/** ⋯：两种对象同一颗 */
function MoreMenu({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Menu
      width={172}
      align="end"
      trigger={
        <Button size="icon-sm" data-identity-more className="shrink-0 text-ink-2" aria-label={label}>
          <Ellipsis size={ICON_SIZE.md} />
        </Button>
      }
    >
      {children}
    </Menu>
  )
}

/**
 * 状态 chip：小胶囊，点它就是撤销这个状态（已锁定 → 解锁、已隐藏 → 显示）。
 * 状态与它的反操作是同一个东西——不另起一颗钮。
 */
function StateChip({
  icon: Icon,
  label,
  action,
  onClick,
  ...data
}: {
  icon: ComponentType<{ size?: number; className?: string }>
  label: string
  action: string
  onClick: () => void
} & { [K: `data-${string}`]: string | boolean | undefined }) {
  return (
    <Tip label={action} side="bottom">
      <button
        type="button"
        {...data}
        aria-label={`${label} · ${action}`}
        onClick={onClick}
        className="flex h-5 shrink-0 items-center gap-1 rounded-full bg-surface-hover px-1.5 text-xs text-ink-2 outline-none transition-colors hover:bg-surface-active hover:text-ink focus-visible:focus-ring"
      >
        <Icon size={ICON_SIZE.xs} />
        {label}
      </button>
    </Tip>
  )
}

/**
 * 「2 个问题 ›」：这一选择在问题清单里还有几条错误 / 警告；点它直达问题面板里的第一条
 * （`openProblemAt`，与左栏样式面板的直达同一个动作，2026-10-07 设计审计 §9.4 P2）。
 * 判据只读 `validationStore.issues` 的 `objectRef`，不在这里另判。
 */
function ProblemsChip({ objectIds, gid, figureId }: { objectIds: string[]; gid?: string | null; figureId: string | null }) {
  const { t } = useTranslation('inspector')
  const all = useValidationStore((s) => s.issues)
  const mine = all.filter(
    (i) =>
      (i.severity === 'error' || i.severity === 'warn') &&
      i.objectRef.objectId != null &&
      objectIds.includes(i.objectRef.objectId) &&
      (gid == null || i.objectRef.gid === gid),
  )
  if (!mine.length) return null
  const errors = mine.filter((i) => i.severity === 'error')
  const first: ValidationIssue = errors[0] ?? mine[0]
  return (
    <button
      type="button"
      data-identity-problems={mine.length}
      onClick={() => openProblemAt(first, all, figureId)}
      className={cn(
        'flex h-5 shrink-0 items-center gap-0.5 rounded-full pl-1.5 pr-1 text-xs font-medium outline-none transition-colors focus-visible:focus-ring',
        errors.length
          ? 'bg-danger-surface text-danger-content hover:bg-danger-surface-hover'
          : 'bg-warn-surface text-warn-content hover:inset-ring hover:inset-ring-warn-border',
      )}
    >
      {t('identity.problems', { count: mine.length })}
      <ChevronRight size={ICON_SIZE.xs} aria-hidden />
    </button>
  )
}

function ElementIdentity({ panel }: { panel: PanelObject }) {
  const { t } = useTranslation('inspector')
  const selectedGids = useUiStore((s) => s.selectedGids)
  const manifest = usePanelDisplayManifest(panel)

  const gid = selectedGids.at(-1)
  // 在树里点「整张图」与什么都没选是同一个对象：头部只有一种写法（「整张图」徽标 +
  // 图名），不因为选中方式不同而换一副面孔（2026-09-13 审计 B54 / B56 的连续性）
  const picked = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
  const el = picked?.gid === 'figure' ? undefined : picked
  // 选中的是一个真实的组（`Manifest.groups`）：它不是元素，头部按组写
  const selGroup = gid && manifest ? groupByGid(manifest, gid) : undefined
  // gid 形如 axes_1.images_0：中段就是宿主子图，拼出「面板 / 子图 / 元素」
  const axesGid = gid?.includes('.') ? gid.split('.')[0] : undefined
  const axes0 = axesGid ? manifest?.elements.find((e) => e.gid === axesGid) : undefined
  // 真实祖先链（显式父级优先，`roles/hierarchy.structuralParent`）：组进面包屑；
  // 色条轴是色条的承载轴、属性页本来就把它换成色条，面包屑里不再单列这一级——
  // 单宿主色条的上一级是宿主子图，共享色条的上一级是组
  const chain = gid && manifest ? ancestorsOf(structuralParent(manifest), gid) : []
  const ancGroup = chain.map((g) => groupByGid(manifest, g)).find((g) => !!g)
  const axes = axes0?.is_colorbar
    ? manifest?.elements.find((e) => e.gid === axes0.parent_gid && (e.role === 'axes' || e.role === 'axes3d'))
    : axes0
  // 子图与元素之间那一级（图例 / X 轴刻度 / 柱形系列）：归属进面包屑
  const has = (g: string) => !!manifest?.elements.some((e) => e.gid === g)
  const containerOf = gid
    ? containerGid(gid, has, (g) => {
        const r = manifest?.elements.find((e) => e.gid === g)?.role
        return r === 'axes' || r === 'axes3d'
      })
    : null
  const container = containerOf ? manifest?.elements.find((e) => e.gid === containerOf) : undefined
  // 文字元素的 `text`、系列的 `label`：都是「名字里被引擎截断的那段用户文字」的全文
  const text = el?.editable.find((f) => f.prop === 'text' || f.prop === 'label')?.value
  const crumbs = identityCrumbs(
    panel.name ?? panel.fileId,
    axes && axes.gid !== gid ? axes.label : undefined,
    // 标题显示可读文本：mathtext 源码只在下面的「名称」框里（审计 B48）
    el
      ? displayLabel(untruncatedLabel(el.label, typeof text === 'string' ? text : undefined))
      : selGroup && manifest
        ? groupName(selGroup, manifest)
        : undefined,
    selectedGids.length,
    container?.label,
    ancGroup && manifest ? groupName(ancGroup, manifest) : undefined,
  )
  // 面包屑里每一级祖先都能点（2026-09-14 审计 A4）：往上走的路就是它本身。
  // 与 identityCrumbs 同一套条件，顺序一致：整张图 → 组 → 宿主子图 → 容器
  const crumbTargets = [
    'figure',
    ancGroup ? ancGroup.gid : null,
    axes && axes.gid !== gid ? axes.gid : null,
    container ? container.gid : null,
  ].filter((g): g is string => !!g)
  const hideable = el && el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')
  // 来源状态：选中元素时报它自己被改了几项，没选（整张图）时报面板总数。组没有自己的属性、
  // 也没有「恢复这个组」——恢复菜单在组页上不出现
  const modified = selGroup
    ? 0
    : el
      ? panel.overrides.filter((o) => o.gid === el.gid).length
      : panel.overrides.length
  const RoleIcon = roleIcon(el?.role ?? (selGroup ? 'group' : 'figure'))
  const name = crumbs.at(-1) ?? t('elementFallback')

  return (
    <IdentityFrame
      path={
        crumbs.length > 1 && (
          // 整条路径的全文在 title 上；每一级是 24px 高的按钮，长名字中间省略（保留有区分力的尾部）
          <span className="flex min-w-0 items-center gap-0.5" title={crumbs.join(' / ')}>
            {crumbs.slice(0, -1).map((c, i) => (
              <span key={`${i}-${c}`} className="flex min-w-0 items-center gap-0.5">
                {i > 0 && <ChevronRight size={ICON_SIZE.xs} aria-hidden className="shrink-0" />}
                <button
                  type="button"
                  data-crumb={crumbTargets[i]}
                  onClick={() => useUiStore.getState().setSelectedGid(crumbTargets[i])}
                  className="flex h-6 min-w-0 max-w-full items-center rounded-sm px-1 text-ink-3 outline-none hover:bg-surface-hover hover:text-ink focus-visible:focus-ring"
                >
                  <TruncateMiddle text={c} tail={4} />
                </button>
              </span>
            ))}
          </span>
        )
      }
      chips={
        <>
          <ProblemsChip
            objectIds={[panel.id]}
            gid={el?.gid ?? null}
            figureId={panel.id}
          />
          {/* 「n 项已修改」徽标本身就是恢复菜单（恢复此元素 / 恢复整张图） */}
          <RestoreMenu panel={panel} gid={el?.gid} count={modified} />
        </>
      }
      icon={<RoleIcon size={ICON_SIZE.md} aria-hidden />}
      name={
        <>
          {/* 没选元素时标题是面板名：标出「整张图」这一层，免得与画布上的面板混淆（审计 T01） */}
          {!el && !selGroup && (
            <span data-object-kind className="shrink-0 rounded-sm bg-surface-active px-1 text-xs text-ink-2">
              {roleName('figure')}
            </span>
          )}
          <NameHeading>{name}</NameHeading>
        </>
      }
      menu={
        hideable && el ? (
          <MoreMenu label={t('objectActions')}>
            <MenuItem
              icon={EyeOff}
              data-hide-element
              onSelect={() => {
                hideElement(panel.id, el.gid, el.label)
                useUiStore.getState().setSelectedGid(null)
              }}
            >
              {t('hideElement')}
            </MenuItem>
          </MoreMenu>
        ) : (
          // 没有命令也留着位置：两种对象的名字行同宽，切换选择时名字不左右跳
          <span aria-hidden className="h-7 w-7 shrink-0" />
        )
      }
    />
  )
}

function ObjectIdentity({ objs }: { objs: CanvasObject[] }) {
  const { t } = useTranslation('inspector')
  const one = objs.length === 1 ? objs[0] : null
  const kinds = [...new Set(objs.map((o) => o.type))]
  // 标注的图标按**它自己那一种**画（与 MarkerPicker 同一条纪律——形状是事实）；
  // 多选是一摞（Layers），不再借「复制」的图标
  const oneKind = one ? switchKindOf(one) : null
  const Icon = one
    ? oneKind
      ? KIND_SWITCH_ICON[oneKind]
      : TYPE_ICON[one.type]
    : kinds.length === 1
      ? TYPE_ICON[kinds[0]]
      : Layers
  /**
   * 标题 = **用户内容**。没起过名字的标注，`objectLabel` 的兜底正是类型名，
   * 而类型徽标已经在说它了——两格并排写着同一个词看起来像个 bug。这一格没有新话要说时
   * 类型直接当大标题，路径行只剩状态 chip（行高照旧，不跳）。
   */
  const title = one
    ? oneKind && !one.name
      ? null
      : objectLabel(one)
    : translate('count.selectedObjects', { count: objs.length })
  const locked = objs.length > 0 && objs.every((o) => o.locked)
  const hidden = objs.length > 0 && objs.every((o) => o.hidden)
  const ids = objs.map((o) => o.id)
  const setLocked = (v: boolean) =>
    updateObjects(ids, msg(v ? 'history.lockObject' : 'history.unlockObject', undefined, 'workspace'), (o) => {
      o.locked = v
    })
  const setHidden = (v: boolean) =>
    updateObjects(ids, msg(v ? 'history.hideObject' : 'history.showObject', undefined, 'workspace'), (o) => {
      o.hidden = v
    })

  return (
    <IdentityFrame
      path={
        title != null && (
          <>
            {/* 类型写在路径行（与图内元素的路径同一个位置）；它同时是类型切换的入口 */}
            <ObjectKindSwitch objs={objs} />
            {!one && <span className="min-w-0 truncate">{summarize(objs)}</span>}
          </>
        )
      }
      chips={
        <>
          <ProblemsChip objectIds={ids} figureId={null} />
          {locked && (
            <StateChip
              icon={Lock}
              label={t('locked')}
              action={t('unlock')}
              data-state-chip="locked"
              onClick={() => setLocked(false)}
            />
          )}
          {hidden && (
            <StateChip
              icon={EyeOff}
              label={t('hiddenState')}
              action={t('show')}
              data-state-chip="hidden"
              onClick={() => setHidden(false)}
            />
          )}
        </>
      }
      icon={<Icon size={ICON_SIZE.md} />}
      name={title != null ? <NameHeading>{title}</NameHeading> : <ObjectKindSwitch objs={objs} />}
      menu={
        <MoreMenu label={t('objectActions')}>
          <MenuItem shortcut={`${MOD}D`} onSelect={duplicateSelected} icon={Copy}>
            {translate('actions.copy')}
          </MenuItem>
          <MenuItem icon={hidden ? Eye : EyeOff} onSelect={() => setHidden(!hidden)}>
            {t(hidden ? 'show' : 'hide')}
          </MenuItem>
          <MenuItem icon={locked ? LockOpen : Lock} onSelect={() => setLocked(!locked)}>
            {t(locked ? 'unlock' : 'lock')}
          </MenuItem>
          <MenuSeparator />
          <MenuItem danger shortcut="⌫" onSelect={deleteSelected} icon={Trash2}>
            {translate('actions.delete')}
          </MenuItem>
        </MoreMenu>
      }
    />
  )
}

function summarize(objs: CanvasObject[]): string {
  const n = (type: CanvasObject['type']) => objs.filter((o) => o.type === type).length
  const parts: string[] = []
  if (n('panel')) parts.push(translate('summaryPanels', { ns: 'inspector', count: n('panel') }))
  if (n('text')) parts.push(translate('summaryTexts', { ns: 'inspector', count: n('text') }))
  const marks = n('arrow') + n('shape')
  if (marks) parts.push(translate('summaryMarks', { ns: 'inspector', count: marks }))
  return listJoin(parts)
}
