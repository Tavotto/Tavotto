import { createContext, useContext } from 'react'

/** 坐在 `FieldGroup` 里的标记（独立成文件：组件文件只导出组件，fast refresh 才生效） */
export const InFieldGroup = createContext(false)

/** 行组件（`SettingRow`）用它知道自己坐在 FieldGroup 里：内边距交给组，自己不再加上下留白 */
export const useInFieldGroup = () => useContext(InFieldGroup)
