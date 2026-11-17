/** 预览正文的归属与采纳资格；只存在于内存，不进入文档/请求正文。 */
export interface PreviewOwner {
  readonly pj: string | null
  readonly key: string
  /** 活动预览失去归属后不发送、不写状态或缓存；明确离开快照不用此活动门。 */
  readonly still: () => boolean
  /** 只有同一项目代次、同一已装入正文可续期恢复门；不重新认领旧正文。 */
  readonly refresh: () => PreviewOwner
}
