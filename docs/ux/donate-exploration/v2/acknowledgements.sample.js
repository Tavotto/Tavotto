/*
 * LAYOUT TEST DATA — NOT REAL SUPPORTERS. Never deploy this file.
 * Loaded only in preview with ?ack=few or ?ack=many, under a visible "test data" banner.
 * Names are deliberately generic, of mixed scripts and lengths, to stress the layout.
 */
(function () {
  var few = [
    { name: 'Sample Name A', month: '2026-10' },
    { name: '示例 · 林', month: '2026-10' },
    { name: 'Test Supporter with a Longer Name', month: '2026-10' },
    { name: 'Sample B.', month: '2026-09' },
    { name: '测试用名', month: '2026-09' }
  ];
  var many = [];
  var latin = ['Sample', 'Test', 'Placeholder', 'Example', 'Layout', 'Fixture'];
  var tails = ['A.', 'B. C.', 'Long Display Name', 'D.', 'E. F. Gh.', 'Name'];
  var han = ['示例', '测试', '样例', '排版'];
  var months = ['2026-10', '2026-09', '2026-08', '2026-07'];
  for (var i = 0; i < 64; i++) {
    var n = i % 5 === 0 ? han[i % 4] + '用户 ' + (i + 1) : latin[i % 6] + ' ' + tails[(i * 7) % 6] + ' ' + (i + 1);
    many.push({ name: n, month: months[i % 4] });
  }
  many.push({ name: 'Sample A. 1' });
  window.TAVOTTO_ACKNOWLEDGEMENTS_SAMPLE = { few: few, many: many };
})();
