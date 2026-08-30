/**
 * 「一条 profile 在界面上叫什么」的**唯一实现**（与 `lib/readinessText.ts`
 * 同一条纪律：措辞只有一处，别在每个界面各写一遍）。
 *
 * 两条规则：
 *
 * 1. **内置跟界面语言走**（`name_key` → i18n），用户自己起的名字**不翻译**。
 * 2. **默认界面不出现内部 id 与版本号**。`lab-publication-v1 · v1.0.0` 这种
 *    东西对用户没有任何意义，而它出现在主界面上会让人以为那是要记住的东西。
 *    id / 版本留给技术详情、导入冲突和迁移——`technicalDetail()` 是它们的
 *    唯一出口。
 */
import { t } from '@/i18n'
import type { ProfileRecord } from './api'

/** 名字。内置查 i18n（查不到退回后端给的那个），用户的原样返回。 */
export function profileName(record: {
  name_key?: string
  display_name: string
}): string {
  const key = record.name_key
  if (key) return t(`profiles.${key}`, { ns: 'dialogs', defaultValue: record.display_name })
  return record.display_name
}

/**
 * 技术详情那一行：`id · v版本`。**只在"技术详情"、导入冲突、迁移报告里出现**，
 * 不进列表、不进下拉、不进导出面板的常规视图。
 */
export function profileTechnicalDetail(record: { id: string; version: string }): string {
  return record.version ? `${record.id} · v${record.version}` : record.id
}

/** 一条 profile 的来源标签：内置 / 自定义（列表里区分只读的那几条）。 */
export function profileOriginLabel(record: { built_in: boolean }): string {
  return t(record.built_in ? 'profiles.origin.builtin' : 'profiles.origin.user', { ns: 'dialogs' })
}

/**
 * 迁移 / 导入时没能映射的字段，翻成一句人话。
 * warning 的形状是 `unmapped_field:<键名>`——**结构化值，不是句子**：
 * 存句子的话换一次语言就成了历史遗留的外语。
 */
export function profileWarningText(warning: string): string {
  const [code, ...rest] = warning.split(':')
  const detail = rest.join(':')
  if (code === 'unmapped_field') {
    return t('profiles.warning.unmappedField', { ns: 'dialogs', field: detail })
  }
  if (code === 'legacy_unnamed') return t('profiles.warning.legacyUnnamed', { ns: 'dialogs' })
  return t('profiles.warning.other', { ns: 'dialogs', code: warning })
}

/** 一条记录能不能改（内置一律不能；改内置的正确出口是"复制一份"）。 */
export const isEditable = (record: Pick<ProfileRecord, 'read_only'>) => !record.read_only
