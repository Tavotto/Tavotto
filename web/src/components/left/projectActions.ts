/** 带 pj 的地址在新标签页里认下那个项目（项目绑在标签页上，lib/session.ts） */
export function openInNewTab(id: string) {
  window.open(`${location.pathname}?pj=${encodeURIComponent(id)}`, '_blank', 'noopener')
}
