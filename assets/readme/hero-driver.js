// Hook for assets/readme/hero-window.mjs, which adds it to the built frontend for one run:
// dismisses the first-run hint toast and selects panel (a), the way a user would.
;(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  // The layout was built and saved in a browser session; a WKWebView has its own storage, so the
  // keys that say "this layout is saved in the project" (and the properties column's width, the
  // hints already seen) are copied over once, and the page reloaded to start from them.
  if (!sessionStorage.getItem('hero-ls')) {
    sessionStorage.setItem('hero-ls', '1')
    if (window.__HERO_LS) {
      for (const [k, v] of Object.entries(window.__HERO_LS)) localStorage.setItem(k, v)
      location.reload()
      return
    }
  }
  for (let i = 0; i < 240; i++) {
    if (document.querySelectorAll('[data-object-id^="p_"]').length >= 3) break
    await sleep(500)
  }
  await sleep(Number(new URLSearchParams(location.search).get('hero_wait') || 4000))
  const toastClose = [...document.querySelectorAll('button')].find((b) => {
    const r = b.getBoundingClientRect()
    return r.top > innerHeight * 0.7 && r.width < 40 && /close|dismiss|关闭/i.test(b.getAttribute('aria-label') || b.title || '')
  })
  toastClose?.click()
  await sleep(800)
  const el = document.querySelectorAll('[data-object-id^="p_"]')[0]
  const r = el.getBoundingClientRect()
  const o = { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1 }
  el.dispatchEvent(new PointerEvent('pointerdown', o))
  el.dispatchEvent(new MouseEvent('mousedown', o))
  el.dispatchEvent(new PointerEvent('pointerup', { ...o, buttons: 0 }))
  el.dispatchEvent(new MouseEvent('mouseup', { ...o, buttons: 0 }))
  el.dispatchEvent(new MouseEvent('click', { ...o, buttons: 0 }))
})()
