import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { JournalPicker, type JournalPickTarget } from '@/components/journal/JournalPicker'
import { Row } from '@/components/ui/Field'
import { fieldBoxTrigger } from '@/components/ui/fieldBox'
import { Select } from '@/components/ui/Select'
import { draftClearJournal, draftPickJournal, draftPickV2Only, specParts } from '@/lib/dataFigureSpec'
import { journalCatalogUi } from '@/lib/journalUi'
import { currentProjectId } from '@/lib/session'
import { previewOf } from '@/lib/dataFigureAdapter'
import { dataFigureEpoch, overviewKeyOf, useDataFigureStore, type ConfirmKey, type RoleKey } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import type { CellMode } from '@/lib/dataFigure'
import { diffSuggestion } from '@/lib/dataUnderstanding'

/**
 * 右栏「期刊」一行的写入目标：只改草稿自己的规范绑定（`dataFigureStore.setSpecBinding`），不碰任何画布的 `doc.profile`、不进撤销栈。
 * 读的都是**点选落定那一刻**的活值（选择器等下载期间草稿可能已经变了）；页宽只用来喂「不按期刊」的退路记录，草稿本身没有页面。
 */
function draftTarget(ref: JournalPickTarget['ref']): JournalPickTarget {
  const pageW = () => useDocumentStore.getState().doc.page.w
  const cur = () => useDataFigureStore.getState().specBinding
  const set = (b: ReturnType<typeof cur>) => useDataFigureStore.getState().setSpecBinding(b)
  return {
    ref,
    select: (id, v2) => {
      useJournalV2Store.getState().remember(id, v2)
      set(draftPickJournal(id, v2, cur(), pageW(), useJournalCatalogStore.getState().catalog))
    },
    selectV2Only: (entry, patch) => {
      useJournalV2Store.getState().remember(entry.id, patch)
      set(draftPickV2Only(entry, patch, cur(), pageW()))
    },
    clear: () => set(draftClearJournal(cur())),
    origin: () => `${currentProjectId()}\u0000${dataFigureEpoch()}`,
  }
}

/** 标签放不下就截断、悬停看全（英文标签比列宽长时不撑高行） */
const rowLabel = (text: string) => (
  <span className="block truncate" title={text}>
    {text}
  </span>
)

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
/** 「不要横轴 / 不分组」的取值：不会和任何列名撞（列名里不会有控制字符） */
const NONE = '\u0000none'

/** 标题行右端的小胶囊（「试试建议」） */
const CAPSULE = 'h-6 shrink-0 rounded-full border border-border bg-surface-hover px-2.5 text-xs font-medium text-ink outline-none hover:bg-border focus-visible:focus-ring'

/**
 * 右侧面板在临时标签激活时显示的这份数据：文件名、行列数；横轴 / 纵轴 / 分组三个下拉（值与可选的列来自响应的 `roles`，
 * 某个角色没有可选的列就不显示那一行；横轴多一项「不要横轴」，分组多一项「不分组」）；有非数字格时一行「N 格不是数字」+ 下拉
 * （不画这 N 行 / 只用里面的数字，多列时对每一列都带上同一个处理方式）；底部一行「原文件不会改。」。
 * 改任何一项都带着 `answers` 重新出候选；这些下拉始终挂在这里（重新出候选期间 `roles` / `mixed` 不清），不会闪。
 */
export function DataPanel() {
  const { t } = useTranslation('ai')
  const selected = useDataFigureStore((s) => s.selected)
  const group = useDataFigureStore((s) => s.group)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const mixed = useDataFigureStore((s) => s.mixed)
  const roles = useDataFigureStore((s) => s.roles)
  const busy = useDataFigureStore((s) => s.drawingId != null)
  const understanding = useDataFigureStore((s) => s.understanding)
  const suggestion = useDataFigureStore((s) => s.suggestion)
  const journalRef = useDataFigureStore((s) => s.specBinding?.journalRef)
  const figureStyle = useDataFigureStore((s) => s.figureStyle)
  const target = useMemo(() => draftTarget(journalRef), [journalRef])
  const candidates = useDataFigureStore((s) => s.candidates)
  const thumbs = useDataFigureStore((s) => s.thumbs)
  const previewKey = useDataFigureStore((s) => s.previewKey)
  const errorConfirmed = useDataFigureStore((s) => s.errorConfirmed)
  const designAsked = useDataFigureStore((s) => s.designAsked)
  const purpose = useDataFigureStore((s) => s.purpose)
  // 点选即显示所选：等新一批候选回来之前下拉先显示用户点的，回来（`purpose` 换成新响应）就以响应为准
  const [pendingPurpose, setPendingPurpose] = useState<string | null>(null)
  useEffect(() => setPendingPurpose(null), [purpose])
  const answers = useDataFigureStore((s) => s.answers)
  if (!selected) return null
  const ov = overviews[overviewKeyOf(selected, headerRows)]?.data ?? null
  // 合画：概览只有第一份的，行列数不代表整组，不显示
  const rows = !group && ov?.status === 'ok' ? ov.rows : null
  const cols = !group && ov?.status === 'ok' ? ov.columns : null
  const count = mixed ? mixed.columns.reduce((n, c) => n + c.nonNumeric, 0) : 0
  const pick = (key: RoleKey) => (v: string) => useDataFigureStore.getState().setRole(key, v === NONE ? 'none' : v)
  // 模型的建议和此刻不同、但用户已经动过下拉：标题行右侧一枚胶囊，点了才套用（建议与此刻相同时 diff 为空，什么都不显示）
  const diff = suggestion ? diffSuggestion(suggestion, roles, useDataFigureStore.getState().headerRowUsed, headerRows[selected.path] != null, useDataFigureStore.getState().answers) : null
  const diffTitle = diff
    ? (['x', 'y', 'group'] as const)
        .filter((k) => diff.picks[k] !== undefined)
        .map((k) => {
          const v = diff.picks[k]!
          return `${t(`dataFigureV2.panel.${k}`)} ${Array.isArray(v) ? v.join('、') : v === 'none' ? t(k === 'x' ? 'dataFigureV2.panel.noX' : 'dataFigureV2.panel.noGroup') : v}`
        })
        .join(' · ')
    : ''
  // 「需要你确认」：事实在后端（候选带 `error`、响应带 `error_confirmed`、`defaults` 里有 `pick_design`），这里只翻译成行。
  // 误差线看的是**此刻预览的那一张**；重复测量一旦问过就留着（确认后后端不再问，但那一行要能再改）
  const shownCand = previewOf(candidates, thumbs, previewKey)
  const errKind = shownCand?.error ?? null
  // 多个 y 各配各的误差列：各列都由列名自己说明才算「说明了」（单个 y 看 `error_stated`）；各列种类不一致时顶层没有 `error`
  const colFacts = shownCand?.error_columns && shownCand.error_columns.length > 1 ? shownCand.error_columns : null
  const statedAll = colFacts ? colFacts.every((c) => c.stated) : shownCand?.error_stated === true
  const kindsDiffer = errKind == null && colFacts != null
  // 各列不同、但其中有列名没说明是哪一种的（`unknown` / 没 `stated`）：那一列的含义仍未知，要留在「需要你确认」里
  const mixedUnsure = kindsDiffer && colFacts!.some((c) => c.kind === 'unknown' || !c.stated)
  // 误差线的种类：点选过（`error_confirmed` / `answers.error`）或列名自己说明了（`error_stated`）都算已知，放在列角色下面；
  // 现成的误差列没说明是哪一种（`unknown`）且没点选 = 需要你确认，当前值写「还不确定」
  const errorDone = errKind != null && (errorConfirmed || isErrorKind(answers.error) || (statedAll && errKind !== 'unknown'))
  const designDone = answers.design === 'independent' || answers.design === 'repeated'
  const pendingError = (errKind != null && !errorDone) || mixedUnsure
  const pendingDesign = designAsked && !designDone
  const confirm = (key: ConfirmKey) => (v: string) => useDataFigureStore.getState().setConfirm(key, v)
  const optLabel = (v: string) => t(`dataFigureV2.confirm.option.${v}`)
  /** 一行确认项：待确认时控件是空值 + 把当前取值写在触发器上（选任何一项，包括仍选当前这个，才算确认） */
  const confirmRow = (key: ConfirmKey, kinds: readonly string[], now: string, done: boolean) => (
    <div key={key} data-data-role={key} className="contents">
      <Row label={rowLabel(t(`dataFigureV2.confirm.${key}`))} labelWidth={LABEL_W}>
        <Select
          value={done ? now : ''}
          disabled={busy}
          ariaLabel={t(`dataFigureV2.confirm.${key}`)}
          onChange={confirm(key)}
          options={kinds.map((k) => ({ value: k, label: optLabel(k) }))}
          title={optLabel(now)}
          // 待确认时值是空的（选「当前这个」也要算一次选择），当前取值写在占位上
          placeholder={done ? undefined : optLabel(now)}
        />
      </Row>
    </div>
  )
  // 「目的」：选项与当前值都以响应为准（`source` 是 data 还是 user 这一行上不区分）；没有 / 选项不足两个 / code 为 null 不出这一行；
  // 当前值万一不在选项里（后端不该这样）补进去，免得下拉显示一个选不到的值
  const purposeOptions = purpose && purpose.code && purpose.options.length >= 2 ? (purpose.options.includes(purpose.code) ? purpose.options : [purpose.code, ...purpose.options]) : null
  const asOptions = (list: string[], none?: string) => [...(none ? [{ value: NONE, label: none }] : []), ...list.map((c) => ({ value: c, label: c }))]
  return (
    <div data-data-panel className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3 pt-2">
      <header className="flex flex-col">
        <p className="flex h-6 items-center justify-between gap-2 type-caption">
          <span>{t('dataFigureV2.list.heading')}</span>
          {diff && !busy ? (
            <button
              type="button"
              data-data-suggest
              title={diffTitle || undefined}
              onClick={() => useDataFigureStore.getState().applySuggestion()}
              className={CAPSULE}
            >
              {t('dataFigureV2.suggest.apply')}
            </button>
          ) : understanding ? (
            <span data-data-reading className="text-xs font-normal text-ink-3">
              {t('dataFigureV2.suggest.reading')}
            </span>
          ) : null}
        </p>
        <h2 className="type-heading min-w-0 break-words py-1" title={group ? undefined : selected.path}>
          {group ? (
            t('dataFigureV2.batch.groupTitle', { count: group.length })
          ) : (
            // 文件名 / 工作表名是用户自己的字符串：原样显示，不套标题类的中西文自动留白（`[data-literal]`，见 index.css）
            <span data-literal>{baseName(selected.path)}</span>
          )}
          {!group && selected.sheet ? (
            <span className="font-normal text-ink-3">
              {' · '}
              <span data-literal>{selected.sheet}</span>
            </span>
          ) : null}
        </h2>
        {rows != null && cols != null && (
          <p data-data-panel-meta className="mb-3 text-sm text-ink-2 tabular-nums">
            {t('dataFigureV2.list.fileMeta', { rows, columns: cols })}
          </p>
        )}
      </header>
      {journalCatalogUi() && (
        <div data-data-role="journal" className="mb-3 flex flex-col gap-1">
          <Row label={rowLabel(t('journal.title', { ns: 'dialogs' }))} labelWidth={LABEL_W}>
            <JournalPicker target={target} triggerClassName={fieldBoxTrigger} disabled={busy} />
          </Row>
          {figureStyle && (
            <p data-data-spec className="pl-[calc(56px+1rem)] text-xs text-balance text-ink-3 tabular-nums">
              {t('dataFigureV2.spec.line', {
                parts: specParts(figureStyle)
                  .map((p) => t(`dataFigureV2.spec.${p.key}`, p.values))
                  .join(t('dataFigureV2.spec.join')),
              })}
            </p>
          )}
        </div>
      )}
      {roles && (
        <div data-data-roles className="flex flex-col gap-1">
          {roles.options.x.length > 0 && (
            <div data-data-role="x" className="contents">
              <Row label={rowLabel(t('dataFigureV2.panel.x'))} labelWidth={LABEL_W}>
                <Select
                  value={roles.x ?? NONE}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.x')}
                  onChange={pick('x')}
                  options={asOptions(roles.options.x, t('dataFigureV2.panel.noX'))}
                />
              </Row>
            </div>
          )}
          {roles.options.y.length > 0 && (
            <div data-data-role="y" className="contents">
              <Row label={rowLabel(t('dataFigureV2.panel.y'))} labelWidth={LABEL_W}>
                <Select
                  value={roles.y ?? ''}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.y')}
                  onChange={pick('y')}
                  options={asOptions(roles.options.y)}
                  // 纵轴多列（每列一条线 / 把列当分组）：收起时把几列并列写出来；下拉里仍是单选，选了就收回一列
                  triggerLabel={roles.yAll.length > 1 ? roles.yAll.join('、') : undefined}
                />
              </Row>
            </div>
          )}
          {roles.options.group.length > 0 && (
            <div data-data-role="group" className="contents">
              <Row label={rowLabel(t('dataFigureV2.panel.group'))} labelWidth={LABEL_W}>
                <Select
                  value={roles.group ?? NONE}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.group')}
                  onChange={pick('group')}
                  options={asOptions(roles.options.group, t('dataFigureV2.panel.noGroup'))}
                />
              </Row>
            </div>
          )}
          {purpose && purposeOptions && (
            <div data-data-role="purpose" className="contents">
              <Row label={rowLabel(t('dataFigureV2.purpose.label'))} labelWidth={LABEL_W}>
                <Select
                  value={pendingPurpose ?? purpose.code!}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.purpose.label')}
                  onChange={(v) => {
                    // 点的就是此刻显示的也算用户确认过的目的（`answers.purpose`），后端据此把「假设」变成「用户点的」
                    setPendingPurpose(v)
                    useDataFigureStore.getState().setConfirm('purpose', v)
                  }}
                  options={purposeOptions.map((c) => ({ value: c, label: t(`dataFigureV2.purpose.${c}`) }))}
                />
              </Row>
            </div>
          )}
          {/* 各列种类不同（没有顶层 error）：放在列角色下面、不进「需要你确认」，当前值「各列不同」；选一种 = 所有列一起换 */}
          {kindsDiffer && !mixedUnsure && confirmRow('error', ERROR_KINDS, 'mixed', false)}
          {errorDone && confirmRow('error', ERROR_KINDS, isErrorKind(answers.error) ? answers.error : errKind!, true)}
          {designAsked && designDone && confirmRow('design', DESIGNS, String(answers.design), true)}
        </div>
      )}
      {(pendingError || pendingDesign) && (
        <div data-data-confirm className="mt-3 flex flex-col gap-1">
          <p data-data-confirm-title className="flex h-6 items-center type-caption">
            {t('dataFigureV2.confirm.title')}
          </p>
          {pendingError && confirmRow('error', ERROR_KINDS, mixedUnsure ? 'mixed' : errKind === 'unknown' ? 'unsure' : errKind!, false)}
          {pendingDesign && confirmRow('design', DESIGNS, 'unsure', false)}
        </div>
      )}
      {mixed && count > 0 && (
        <>
          <div className="my-2 h-px bg-border" />
          <Row label={t('dataFigureV2.panel.mixed', { count })} labelWidth="auto">
            <Select<CellMode>
              value={mixed.mode}
              disabled={busy}
              ariaLabel={t('dataFigureV2.panel.mixed', { count })}
              onChange={(m) => useDataFigureStore.getState().setMixedMode(m)}
              options={[
                { value: 'skip', label: t('dataFigureV2.option.skip', { count }) },
                { value: 'extract', label: t('dataFigureV2.option.extract') },
              ]}
            />
          </Row>
        </>
      )}
      <p className="mt-auto pt-4 text-xs text-ink-3">{t('dataFigureV2.panel.note')}</p>
    </div>
  )
}

/** 标签列宽：放得下「重复测量」四个字（12px 字约 48px）；所有行同一个宽度，控件左缘对齐 */
const LABEL_W = 56

const ERROR_KINDS = ['sd', 'sem', 'ci95'] as const
const DESIGNS = ['unsure', 'independent', 'repeated'] as const
const isErrorKind = (v: unknown): v is (typeof ERROR_KINDS)[number] => typeof v === 'string' && (ERROR_KINDS as readonly string[]).includes(v)
