import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Copy,
  Crop,
  Eye,
  EyeOff,
  Group,
  Layers,
  Link2,
  Lock,
  LockOpen,
  Minimize2,
  Pencil,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  Trash2,
  Ungroup,
} from 'lucide-react'
import { t as translate } from '@/i18n'
import { emitActivity } from '@/lib/activity'
import { MOD } from '@/lib/utils'
import {
  ALIGN_BUTTONS,
  DISTRIBUTE_BUTTONS,
  SIZE_BUTTONS,
  type ArrangeButton,
} from '@/components/inspector/arrangeButtons'
import { MenuHeading, MenuItem, MenuSeparator, MenuSub, PointMenu } from '@/components/ui/Menu'
import {
  alignModeLabel,
  alignRefLabel,
  alignSelectedTo,
  changeZOrder,
  deleteSelected,
  duplicateSelected,
  enterElementEdit,
  fitPanels,
  groupSelected,
  rebuildPanel,
  resetOverridesConfirmed,
  selectionHasGroupIn,
  setObjectsHidden,
  setObjectsLocked,
  toggleHidden,
  toggleLocked,
  triStateOf,
  ungroupSelected,
  type ZMove,
} from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { openArrangeInInspector } from './context-bar/openArrange'
import type { CanvasObject, PanelObject } from '@/types/document'
import { objectLabel, panelRotation } from '@/types/document'

/**
 * 画布对象的右键菜单（Prompt 18）。
 *
 * **按目标与选区给内容**：单个可编辑面板 / 单个仅排版面板 / 文字 / 箭头与形状 /
 * 两个以上对象，五份清单，顶层只放高频项，层级收进子菜单。每一项**只发意图**——
 * 落地全部走 `store/actions`（与属性页、浮动栏同一个函数、同一条历史标签），
 * 这里不算几何、不判就绪度、不碰 override 数组：
 *
 *   排列 / 成组   alignSelectedTo / groupSelected / ungroupSelected（ADR 0036，T-91）
 *   重新构建      rebuildPanel（作废热会话 + 按当前 overrides 重画；不进历史）
 *   恢复图内修改  resetOverridesConfirmed（同属性页「重置到脚本原始」；先问一句）
 *   为什么不能编辑 / 连接源脚本   projectReadinessStore.focusPanel（接入中心才是动作面）
 *   锁定 / 隐藏   单个走既有 toggle；多选走批量 action（一条历史）
 *
 * 菜单出现之前 `ObjectView.onContextMenu` 已经保证右键的对象在选区里（不在就换成
 * 它 / 它的组），所以这里「作用范围 = 当前选区」这句话对每一项都成立。
 */

/** 本菜单的文案（workspace:quickEdit.*） */
const qe = (key: string, values?: Record<string, unknown>) =>
  translate(`quickEdit.${key}`, { ns: 'workspace', ...(values ?? {}) })
const ins = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'inspector', ...(values ?? {}) })

/** 菜单形态：Prompt 21 的引导按它挂钩（`data-quick-menu`） */
export type ObjectMenuKind = 'panel' | 'panel-layout-only' | 'text' | 'mark' | 'multi'

const Z_MOVES: readonly { move: ZMove; key: string; shortcut: string; icon: typeof ChevronUp }[] = [
  { move: 'top', key: 'zTop', shortcut: `⇧${MOD}]`, icon: ArrowUpToLine },
  { move: 'up', key: 'zUp', shortcut: `${MOD}]`, icon: ChevronUp },
  { move: 'down', key: 'zDown', shortcut: `${MOD}[`, icon: ChevronDown },
  { move: 'bottom', key: 'zBottom', shortcut: `⇧${MOD}[`, icon: ArrowDownToLine },
]

export function ObjectContextMenu({
  id,
  at,
  close,
}: {
  id: string
  at: { x: number; y: number }
  close: () => void
}) {
  useTranslation('workspace')
  useTranslation('inspector')
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  const obj = objects.find((o) => o.id === id)
  // 顺序 = 选择顺序（末位主选），与浮动栏 / OverlaySvg 同一份判据
  const selected = ids
    .map((sid) => objects.find((o) => o.id === sid))
    .filter((o): o is CanvasObject => o != null)
  // 目标没了（撤销 / 删除）就关掉：菜单指着一个不存在的对象比关掉更让人摸不着头脑
  useEffect(() => {
    if (!obj) close()
  }, [obj, close])

  const multi = selected.length >= 2 && selected.some((o) => o.id === id)
  const kind: ObjectMenuKind | null = !obj
    ? null
    : multi
      ? 'multi'
      : obj.type === 'panel'
        ? obj.script
          ? 'panel'
          : 'panel-layout-only'
        : obj.type === 'text'
          ? 'text'
          : 'mark'
  // 菜单真的打开了（组件挂上 = 用户右键成功落在一个对象上）才发一声本地信号。
  // 挂在早退之前：Hook 顺序不能随 `obj` 变
  useEffect(() => {
    if (kind) emitActivity({ kind: 'menu.opened', menu: kind })
    // 只在挂载那一刻发一次；菜单种类在一次打开里不会变
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (!obj || !kind) return null
  /**
   * 同步动作：先做再关。动作抛异常时菜单也必须关掉（卡在屏幕上的菜单比错误本身
   * 更让人摸不着头脑），异常继续往外抛（该进 Console / ErrorBoundary 的还得进）。
   */
  const run = (fn: () => void) => () => {
    try {
      fn()
    } finally {
      close()
    }
  }
  /** 异步动作（要等后端 / 等用户点头的）：先关菜单再做，结果由 action 自己说 */
  const runAsync = (fn: () => Promise<unknown>) => () => {
    close()
    void fn()
  }

  return (
    <PointMenu
      open
      onOpenChange={(open) => {
        if (!open) close()
      }}
      at={at}
      ariaLabel={qe('menuAria')}
      data-quick-menu={kind}
      data-quick-menu-count={selected.length}
    >
      <MenuHeading data-quick-heading>
        {multi
          ? translate('count.selectedObjects', { count: selected.length })
          : objectLabel(obj)}
      </MenuHeading>

      {kind === 'panel' && <EditablePanelItems panel={obj as PanelObject} run={run} runAsync={runAsync} />}
      {kind === 'panel-layout-only' && <LayoutOnlyPanelItems panel={obj as PanelObject} run={run} />}
      {kind === 'text' && (
        <MenuItem
          icon={Pencil}
          data-quick-item="edit-text"
          onSelect={run(() => useUiStore.getState().setEditingText(obj.id))}
        >
          {qe('editText')}
        </MenuItem>
      )}
      {kind === 'multi' && <MultiItems selected={selected} run={run} />}

      {kind !== 'multi' && <OpenInspectorItem run={run} />}

      <MenuSeparator />
      <CommonTail selected={selected} obj={obj} multi={multi} run={run} />
    </PointMenu>
  )
}

/* -------------------------------------------------------------------------- */
/*  面板                                                                       */
/* -------------------------------------------------------------------------- */

function EditablePanelItems({
  panel,
  run,
  runAsync,
}: {
  panel: PanelObject
  run: (fn: () => void) => () => void
  runAsync: (fn: () => Promise<unknown>) => () => void
}) {
  const rotated = panelRotation(panel) !== 0
  const edits = panel.overrides.length
  return (
    <>
      <MenuItem icon={Pencil} data-quick-item="edit-elements" onSelect={run(() => enterElementEdit(panel.id))}>
        {qe('editElements')}
      </MenuItem>
      <MenuItem icon={RefreshCw} data-quick-item="rebuild" onSelect={runAsync(() => rebuildPanel(panel.id))}>
        {qe('rebuild')}
      </MenuItem>
      <CropItem panelId={panel.id} rotated={rotated} run={run} />
      <FitItem panelId={panel.id} run={run} />
      {edits > 0 && (
        <MenuItem
          icon={RotateCcw}
          data-quick-item="reset-overrides"
          onSelect={runAsync(() => resetOverridesConfirmed(panel.id))}
        >
          {qe('resetOverridesCount', { count: edits })}
        </MenuItem>
      )}
    </>
  )
}

/**
 * 仅排版面板：解释入口 + 排版动作。
 *
 * 「为什么不能编辑？」与「连接源脚本」都只是**入口**——打开接入中心并滚到这张图，
 * 选择一个字不动、脚本一行不跑、不进裁剪态。试运行 / 手工关联在接入中心里。
 * 两个条件缺一不可（与浮动栏同一判据）：`!script` = 文档记着它此刻没有图内编辑
 * 入口；`capability.status !== 'editable'` = 项目里它确实还没连上。`capability`
 * 缺席 = 这一轮还不知道，**什么都不说**（5b 合同）。
 */
function LayoutOnlyPanelItems({
  panel,
  run,
}: {
  panel: PanelObject
  run: (fn: () => void) => () => void
}) {
  const cap = useAssetStore((s) => s.byId[panel.fileId]?.capability)
  const explainable = !panel.script && !!cap && cap.status !== 'editable'
  const connectable = explainable && (cap.can_probe || cap.can_manual_link)
  const focus = run(() => useProjectReadinessStore.getState().focusPanel(panel.fileId, 'quickedit'))
  const rotated = panelRotation(panel) !== 0
  return (
    <>
      {explainable && (
        <MenuItem icon={CircleHelp} data-quick-item="why-not-editable" onSelect={focus}>
          {translate('readiness.whyNotEditable', { ns: 'workspace' })}
        </MenuItem>
      )}
      {connectable && (
        <MenuItem icon={Link2} data-quick-item="connect-source" onSelect={focus}>
          {qe('connectSource')}
        </MenuItem>
      )}
      <CropItem panelId={panel.id} rotated={rotated} run={run} />
      <FitItem panelId={panel.id} run={run} />
    </>
  )
}

/** 裁剪：与双击面板同一条规则——旋转过的面板不进裁剪态（裁剪框方向与画布对不上） */
function CropItem({
  panelId,
  rotated,
  run,
}: {
  panelId: string
  rotated: boolean
  run: (fn: () => void) => () => void
}) {
  return (
    <MenuItem
      icon={Crop}
      data-quick-item="crop"
      disabled={rotated}
      reason={rotated ? qe('cropRotatedReason') : undefined}
      onSelect={run(() => useUiStore.getState().setCropTarget(panelId))}
    >
      {ins('panel.crop')}
    </MenuItem>
  )
}

function FitItem({ panelId, run }: { panelId: string; run: (fn: () => void) => () => void }) {
  return (
    <MenuItem icon={Minimize2} data-quick-item="fit" onSelect={run(() => fitPanels([panelId]))}>
      {ins('panel.fit')}
    </MenuItem>
  )
}

/** 打开全部属性：选区不动，切到属性页（窄屏下 `setRightTab` 本来就会把抽屉铺开） */
function OpenInspectorItem({ run }: { run: (fn: () => void) => () => void }) {
  return (
    <MenuItem
      icon={SlidersHorizontal}
      data-quick-item="open-inspector"
      onSelect={run(() => useUiStore.getState().setRightTab('properties'))}
    >
      {qe('openInspector')}
    </MenuItem>
  )
}

/* -------------------------------------------------------------------------- */
/*  多选                                                                       */
/* -------------------------------------------------------------------------- */

function MultiItems({
  selected,
  run,
}: {
  selected: CanvasObject[]
  run: (fn: () => void) => () => void
}) {
  const ref = useArrangeStore((s) => s.alignRef)
  const count = selected.length
  const grouped = selectionHasGroupIn(selected)
  // 选区恰好是**一整个组**：再「成组」一次只会换个组 id、多一条历史，没有意义；
  // 含组的混合选区两个动作都给（把它们并成一组 / 把里面的组拆开），不替用户猜
  const wholeGroup =
    grouped && selected.every((o) => !!o.groupId && o.groupId === selected[0].groupId)
  return (
    <>
      <MenuSub label={qe('arrange')} data-quick-item="arrange">
        <MenuHeading data-quick-arrange-ref={ref}>{qe('arrangeRef', { ref: alignRefLabel(ref) })}</MenuHeading>
        <AlignItems buttons={ALIGN_BUTTONS} count={count} run={run} refName={ref} />
        <MenuSeparator />
        <AlignItems buttons={DISTRIBUTE_BUTTONS} count={count} run={run} refName={ref} />
      </MenuSub>
      <AlignItems buttons={SIZE_BUTTONS} count={count} run={run} refName={ref} />
      {!wholeGroup && (
        <MenuItem icon={Group} data-quick-item="group" onSelect={run(groupSelected)}>
          {ins('arrange.group')}
        </MenuItem>
      )}
      {grouped && (
        <MenuItem icon={Ungroup} data-quick-item="ungroup" onSelect={run(ungroupSelected)}>
          {ins('arrange.ungroup')}
        </MenuItem>
      )}
      <MenuItem
        icon={SlidersHorizontal}
        data-quick-item="open-arrange"
        onSelect={run(openArrangeInInspector)}
      >
        {qe('openArrange')}
      </MenuItem>
    </>
  )
}

/**
 * 一组排列项：按钮表（图标 / 顺序 / 最少对象数）与浮动栏、属性页同一份。
 * 不够数的项 `disabled` + 常驻原因（分布要三个）；点了不动。
 */
function AlignItems({
  buttons,
  count,
  refName,
  run,
}: {
  buttons: readonly ArrangeButton[]
  count: number
  refName: 'selection' | 'page' | 'primary'
  run: (fn: () => void) => () => void
}) {
  return (
    <>
      {buttons.map(({ mode, icon, min }) => {
        const blocked = count < min
        return (
          <MenuItem
            key={mode}
            icon={icon}
            data-quick-item={`align-${mode}`}
            data-align-mode={mode}
            disabled={blocked}
            reason={blocked ? qe('needObjects', { count: min }) : undefined}
            onSelect={run(() => alignSelectedTo(mode, refName))}
          >
            {alignModeLabel(mode)}
          </MenuItem>
        )
      })}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/*  公共尾巴：创建副本 / 锁定 / 隐藏 / 层级 / 删除                                */
/* -------------------------------------------------------------------------- */

function CommonTail({
  selected,
  obj,
  multi,
  run,
}: {
  selected: CanvasObject[]
  obj: CanvasObject
  multi: boolean
  run: (fn: () => void) => () => void
}) {
  const ids = selected.map((o) => o.id)
  const count = selected.length
  const locked = triStateOf(selected, (o) => !!o.locked)
  return (
    <>
      <MenuItem icon={Copy} data-quick-item="duplicate" shortcut={`${MOD}D`} onSelect={run(duplicateSelected)}>
        {qe('duplicate')}
      </MenuItem>
      {!multi ? (
        <MenuItem
          icon={obj.locked ? LockOpen : Lock}
          data-quick-item={obj.locked ? 'unlock' : 'lock'}
          onSelect={run(() => toggleLocked(obj.id))}
        >
          {qe(obj.locked ? 'unlock' : 'lock')}
        </MenuItem>
      ) : (
        <>
          {locked !== 'all' && (
            <MenuItem
              icon={Lock}
              data-quick-item="lock"
              onSelect={run(() => setObjectsLocked(ids, true))}
            >
              {qe(locked === 'mixed' ? 'lockAll' : 'lockCount', { count })}
            </MenuItem>
          )}
          {locked !== 'none' && (
            <MenuItem
              icon={LockOpen}
              data-quick-item="unlock"
              onSelect={run(() => setObjectsLocked(ids, false))}
            >
              {qe(locked === 'mixed' ? 'unlockAll' : 'unlockCount', { count })}
            </MenuItem>
          )}
        </>
      )}
      {!multi ? (
        <MenuItem
          icon={obj.hidden ? Eye : EyeOff}
          data-quick-item={obj.hidden ? 'show' : 'hide'}
          onSelect={run(() => toggleHidden(obj.id))}
        >
          {qe(obj.hidden ? 'show' : 'hideObject')}
        </MenuItem>
      ) : (
        <MenuItem icon={EyeOff} data-quick-item="hide" onSelect={run(() => setObjectsHidden(ids, true))}>
          {qe('hideCount', { count })}
        </MenuItem>
      )}
      <MenuSub label={qe('zOrder')} icon={Layers} data-quick-item="z-order">
        {Z_MOVES.map(({ move, key, shortcut, icon }) => (
          <MenuItem
            key={move}
            icon={icon}
            data-quick-item={`z-${move}`}
            shortcut={shortcut}
            onSelect={run(() => changeZOrder(move))}
          >
            {qe(key)}
          </MenuItem>
        ))}
      </MenuSub>
      <MenuSeparator />
      <MenuItem icon={Trash2} danger data-quick-item="delete" shortcut="Delete" onSelect={run(deleteSelected)}>
        {multi ? qe('deleteCount', { count }) : translate('actions.delete')}
      </MenuItem>
    </>
  )
}
