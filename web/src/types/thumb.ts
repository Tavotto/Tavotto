/**
 * 缩略图画得出来所需的**最小事实**。
 *
 * 两种输入的公共形状：内存里的文档对象（`CanvasObject`，结构上直接满足它）
 * 与版本列表端点发来的草图（`app.py` 的 `_version_sketch`，字段名逐字相同）。
 * 所以它**不是**文档模型的别名——草图里没有 overrides、没有脚本、没有几何
 * 之外的任何东西，而缩略图本来也只用得上这几个字段。
 *
 * 单独一个模块是为了让 `lib/api.ts`（草图的类型）与 `components/CanvasThumb.tsx`
 * （画它的组件）都能引用它而互不依赖。
 */
export interface ThumbObject {
  /** 缺席时按下标做 key（草图不发 id：那几个字节买不到任何东西） */
  id?: string
  type: string
  x: number
  y: number
  w: number
  h: number
  hidden?: boolean
  /** 面板：素材身份（预览图挂的就是它） */
  fileId?: string
  fileKind?: string
  /** 文字：正文 */
  text?: string
  /** 形状：种类 */
  shape?: string
}

/** 缩略图的页面比例（mm）。 */
export interface ThumbPage {
  w: number
  h: number
}
