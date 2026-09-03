import type { ManifestElement } from '@/lib/api'
import { hasTextStyleBar } from '@/components/inspector/TextStyleBar'

/** 这些角色有专属快捷动作；其余元素不出工具条（右栏与右键仍可达一切） */
const ELEMENT_QUICK_ROLES = new Set(['line', 'linecoll', 'legend'])
export const elementHasQuick = (el: ManifestElement) =>
  hasTextStyleBar(el) || ELEMENT_QUICK_ROLES.has(el.role)
