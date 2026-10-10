/**
 * 准备引导卡（T13b）「每个项目只自动弹一次」的本机标记：按后端的项目 id（`ProjectScan.project_id`，与接入状态横幅的
 * 关闭记录同一种键）记下「这个项目的卡已经自动弹过」。只是呈现偏好——不进项目文件、不跨机器；读不到 / 写不了
 * localStorage 时按「没弹过」处理，不抛（隐私窗口里最多多弹一次）。
 */
const KEY = 'tavotto.guideCard.autoShown'
/** 记几个项目就够了：再旧的项目重新打开时多弹一次无害 */
const LIMIT = 200

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const list = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function wasAutoShown(projectId: string): boolean {
  return read().includes(projectId)
}

export function markAutoShown(projectId: string): void {
  try {
    const list = read().filter((x) => x !== projectId)
    list.push(projectId)
    localStorage.setItem(KEY, JSON.stringify(list.slice(-LIMIT)))
  } catch {
    /* 写不了就算了：下次打开再弹一次 */
  }
}
