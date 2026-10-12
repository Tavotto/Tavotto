/* Tavotto · Support page V2. No framework, no payment logic: the page links out to Ko-fi and
   renders only data that support.config.js and acknowledgements.js say may be public. */
(function () {
  'use strict';
  var page = document.querySelector('.page');
  if (!page || page.dataset.booted) return;
  page.dataset.booted = '1';
  page.classList.add('js');

  var CFG = window.TAVOTTO_SUPPORT_CONFIG || {};
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  var params = new URLSearchParams(location.search);
  if (CFG.preview) page.classList.add('is-preview');

  function $(sel, root) { return (root || page).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || page).querySelectorAll(sel)); }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function bi(en, zh) { var f = document.createDocumentFragment(); var a = el('span', 'en', en); var b = el('span', 'zh', zh); b.lang = 'zh-Hans'; f.appendChild(a); f.appendChild(b); return f; }

  /* ── language ── */
  var lang = 'en';
  try {
    lang = params.get('lang') || localStorage.getItem('tavotto-support-lang') ||
      ((navigator.language || '').toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en');
  } catch (e) {}
  function setLang(l) {
    lang = l === 'zh' ? 'zh' : 'en';
    page.dataset.lang = lang;
    document.documentElement.lang = lang === 'zh' ? 'zh-Hans' : 'en';
    document.title = lang === 'zh' ? '支持 Tavotto — 不曾中断的线' : 'Support Tavotto — The Unbroken Line';
    $$('[data-set-lang]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.setLang === lang)); });
    try { localStorage.setItem('tavotto-support-lang', lang); } catch (e) {}
    renderAcknowledgements();
  }
  $$('[data-set-lang]').forEach(function (b) { b.addEventListener('click', function () { setLang(b.dataset.setLang); }); });

  /* ── Act I: draw after the first painted frame ── */
  requestAnimationFrame(function () { requestAnimationFrame(function () { page.classList.add('is-ready'); }); });

  /* ── where the page scrolls (the window, or a phone mock-up frame) ── */
  var scroller = null;
  for (var p = page.parentElement; p && p !== document.body; p = p.parentElement) {
    var oy = getComputedStyle(p).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight + 40) { scroller = p; break; }
  }
  var scrollable = !!scroller || document.documentElement.scrollHeight > window.innerHeight + 40;

  /* ── reveals: one-shot; a frame that cannot scroll shows everything at once ── */
  var io = scrollable && 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
  }, { root: scroller, rootMargin: '0px 0px -12% 0px' }) : null;
  function reveal(node) { if (io) io.observe(node); else node.classList.add('in'); }
  $$('.rv').forEach(reveal);

  /* ── Act III: one figure, three edits ──
     Desktop: plays once when it comes into view. Phone: the reader chooses. Reduced motion: no autoplay. */
  var demo = $('#demo');
  var stage = $('#stage');
  var buttons = $$('.st', demo);
  var STEP_MS = 2200;
  var timer = null;
  function setStep(n) {
    stage.dataset.step = String(n);
    stage.classList.toggle('has-legend', n >= 1);
    stage.classList.toggle('has-type', n >= 2);
    stage.classList.toggle('has-align', n >= 3);
    buttons.forEach(function (b) { b.setAttribute('aria-pressed', String(Number(b.dataset.go) === n)); });
  }
  function stopPlay() { clearTimeout(timer); timer = null; demo.classList.remove('playing'); }
  function play() {
    stopPlay();
    if (reduce.matches) { setStep(3); return; }
    demo.style.setProperty('--step-ms', STEP_MS + 'ms');
    setStep(0);
    var n = 0;
    (function next() {
      timer = setTimeout(function () {
        n += 1; setStep(n);
        demo.classList.remove('playing'); void demo.offsetWidth; // restart the progress hairline
        if (n < 3) { demo.classList.add('playing'); next(); } else { timer = null; }
      }, n === 0 ? 700 : STEP_MS);
    })();
    demo.classList.add('playing');
  }
  buttons.forEach(function (b) { b.addEventListener('click', function () { stopPlay(); setStep(Number(b.dataset.go)); }); });
  $('[data-replay]', demo).addEventListener('click', play);
  var narrow = page.clientWidth <= 760;
  if (scrollable && !reduce.matches) {
    if (narrow) { setStep(0); }
    else {
      setStep(0);
      var dio = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) { play(); dio.disconnect(); } });
      }, { root: scroller, threshold: 0.55 });
      dio.observe(stage);
    }
  } else { setStep(3); }

  /* ── links from config (one place to change them) ── */
  var links = CFG.links || {};
  $$('[data-link]').forEach(function (a) { var u = links[a.dataset.link]; if (u) a.href = u; });

  /* ── Act V: maintainer byline and recent work ── */
  var m = CFG.maintainer || {};
  if (m.name) { var nm = $('[data-maintainer-name]'); nm.textContent = m.name; }
  if (m.role) { var rl = $('[data-maintainer-role]'); rl.textContent = ''; rl.appendChild(bi(m.role.en || '', m.role.zh || m.role.en || '')); }
  if (m.noteConfirmed && m.name) $('[data-draft-note]').remove();

  var recent = $('[data-recent]');
  (CFG.recentWork || []).forEach(function (r) {
    if (!r || !r.version || !r.href) return;
    var row = el('div', 'rw');
    row.appendChild(el('span', 'v', 'v' + r.version));
    var pr = el('p'); pr.appendChild(bi(r.en, r.zh || r.en)); row.appendChild(pr);
    var a = el('a'); a.href = r.href; a.target = '_blank'; a.rel = 'noopener';
    a.appendChild(bi('Release notes ↗', '发布说明 ↗')); row.appendChild(a);
    recent.appendChild(row);
  });

  /* ── Act VI: Ko-fi. Only a real ko-fi.com URL makes the button live; nothing is ever simulated. ── */
  if (CFG.fundingConfirmed) { var fn = $('[data-funding-note]'); if (fn) fn.remove(); }
  var kofi = $('[data-kofi]');
  var KOFI = /^https:\/\/ko-fi\.com\/[A-Za-z0-9_]{2,}\/?$/;
  if (CFG.kofiUrl && KOFI.test(CFG.kofiUrl)) {
    kofi.href = CFG.kofiUrl;
  } else {
    kofi.removeAttribute('href');
    kofi.classList.add('is-off');
    kofi.setAttribute('role', 'link');
    kofi.setAttribute('aria-disabled', 'true');
    kofi.removeAttribute('target');
    $('[data-kofi-unconf]').hidden = false;
  }

  /* ── Act VII: acknowledgements ── */
  var SAFE_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
  var collator = (function () {
    try { return new Intl.Collator(['zh-Hans-u-co-pinyin', 'en'], { sensitivity: 'base', numeric: true }); }
    catch (e) { return { compare: function (a, b) { return a < b ? -1 : a > b ? 1 : 0; } }; }
  })();
  /* Keep only the two public fields, normalise, drop duplicates. Anything else in the file is ignored. */
  function cleanList(raw) {
    var seen = Object.create(null), out = [];
    (Array.isArray(raw) ? raw : []).forEach(function (r) {
      var name = r && typeof r.name === 'string' ? r.name.normalize('NFKC').replace(/\s+/g, ' ').trim() : '';
      if (!name || name.length > 60 || /@/.test(name)) return;
      var key = name.toLocaleLowerCase();
      if (seen[key]) return;
      seen[key] = 1;
      out.push({ name: name, month: r && SAFE_MONTH.test(r.month) ? r.month : '' });
    });
    return out;
  }
  function monthLabel(ym) {
    var d = new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1);
    var en = d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
    var zh = ym.slice(0, 4) + ' 年 ' + Number(ym.slice(5, 7)) + ' 月';
    return [en, zh];
  }
  var ackSource = window.TAVOTTO_ACKNOWLEDGEMENTS;
  var ackIsTest = false;
  var ackList = $('[data-ack-list]');
  function renderAcknowledgements() {
    var list = cleanList(ackSource);
    ackList.textContent = '';
    $('[data-ack-test]').hidden = !ackIsTest;
    var groups = {}, order = [];
    list.forEach(function (r) { var k = r.month || '~'; if (!groups[k]) { groups[k] = []; order.push(k); } groups[k].push(r); });
    order.sort(function (a, b) { return a === '~' ? 1 : b === '~' ? -1 : (a < b ? 1 : -1); });
    var dated = order.some(function (k) { return k !== '~'; });
    order.forEach(function (k, gi) {
      var g = el('section', 'ack-group'); g.style.setProperty('--i', gi);
      if (dated && k !== '~') {
        var lab = monthLabel(k); var h = el('h3', 'ack-mo'); h.appendChild(bi(lab[0], lab[1])); g.appendChild(h);
      } else if (dated) {
        var h2 = el('h3', 'ack-mo'); h2.appendChild(bi('Earlier', '更早')); g.appendChild(h2);
      }
      var ul = el('ul', 'ack-names');
      groups[k].sort(function (a, b) { return collator.compare(a.name, b.name); }).forEach(function (r) { var li = el('li', null, r.name); li.lang = /[㐀-鿿]/.test(r.name) ? 'zh-Hans' : 'und'; ul.appendChild(li); });
      g.appendChild(ul); ackList.appendChild(g);
    });
    var any = list.length > 0;
    $$('[data-ack-anon]').forEach(function (n) { n.hidden = !(window.TAVOTTO_ACKNOWLEDGE_ANONYMOUS || ackIsTest); });
    $('[data-ack-consent]').hidden = !(any || CFG.preview);
    if (any) { ackList.classList.remove('in'); reveal(ackList); }
  }
  var contact = CFG.acknowledgementsContact || '';
  if (/^(mailto:|https:\/\/)/.test(contact)) {
    $$('[data-ack-contact]').forEach(function (a) { a.href = contact; });
    var cn = $('[data-ack-contact-note]'); if (cn) cn.remove();
  } else {
    $$('[data-ack-contact]').forEach(function (a) { a.removeAttribute('href'); });
  }
  /* Preview only: ?ack=few|many loads clearly marked layout test data from a separate file. */
  var ackMode = params.get('ack');
  if (CFG.preview && (ackMode === 'few' || ackMode === 'many')) {
    var s = document.createElement('script');
    s.src = 'acknowledgements.sample.js';
    s.onload = function () { ackSource = (window.TAVOTTO_ACKNOWLEDGEMENTS_SAMPLE || {})[ackMode] || []; ackIsTest = true; renderAcknowledgements(); };
    document.head.appendChild(s);
  }

  /* ── share ── */
  var copied = $('[data-copied]');
  $('[data-copy]').addEventListener('click', function () {
    var done = function () { copied.textContent = lang === 'zh' ? '链接已复制' : 'Link copied'; setTimeout(function () { copied.textContent = ''; }, 2400); };
    try { navigator.clipboard.writeText(location.href.split(/[?#]/)[0]).then(done, done); } catch (e) { done(); }
  });

  setLang(lang);
})();
