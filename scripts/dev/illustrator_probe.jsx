// Illustrator 只读探针（ADR 0208 §七之三）：打开一个 SVG → 读组树 / 名字 / 剪切组 / 文字与字体 / 渐变 → 不保存关闭。
// 用法：osascript -e 'tell application "Adobe Illustrator" to do javascript file "<本文件>" with arguments {"<svg 绝对路径>"}'
// 不发按键、不改任何文件；返回一段 JSON 文本。
(function (args) {
  var path = args && args.length ? args[0] : null;
  if (!path) return '{"error":"no path"}';
  var prev = app.userInteractionLevel;
  app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
  var doc = null;
  function esc(s) {
    s = String(s);
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var c = s.charAt(i), k = s.charCodeAt(i);
      if (c === '"' || c === "\\") out += "\\" + c;
      else if (k < 32) out += " ";
      else out += c;
    }
    return '"' + out + '"';
  }
  function kind(it) { return it.typename; }
  function walk(it, depth) {
    var o = '{"type":' + esc(kind(it)) + ',"name":' + esc(it.name || "");
    if (it.typename === "GroupItem") {
      o += ',"clipped":' + (it.clipped ? "true" : "false");
      var kids = [];
      for (var i = 0; i < it.pageItems.length; i++) {
        if (it.pageItems[i].parent === it) kids.push(walk(it.pageItems[i], depth + 1));
      }
      o += ',"children":[' + kids.join(",") + "]";
    } else if (it.typename === "TextFrame") {
      var fonts = {};
      var runs = it.textRanges;
      var sup = 0;
      for (var r = 0; r < runs.length; r++) {
        try { fonts[runs[r].characterAttributes.textFont.name] = 1; } catch (e) {}
        try { if (runs[r].characterAttributes.baselinePosition !== FontBaselineOption.NORMALBASELINE) sup++; } catch (e2) {}
      }
      var fl = [];
      for (var f in fonts) fl.push(esc(f));
      o += ',"contents":' + esc(it.contents) + ',"fonts":[' + fl.join(",") + '],"shifted_runs":' + sup;
    } else if (it.typename === "PathItem" || it.typename === "CompoundPathItem") {
      try {
        var p = it.typename === "PathItem" ? it : it.pathItems[0];
        o += ',"filled":' + (p.filled ? "true" : "false") + ',"fill":' + esc(p.filled ? p.fillColor.typename : "none");
        o += ',"stroked":' + (p.stroked ? "true" : "false") + ',"strokeWidth":' + (p.stroked ? p.strokeWidth : 0);
        o += ',"points":' + (it.typename === "PathItem" ? it.pathPoints.length : -1);
      } catch (e3) {}
    } else if (it.typename === "NonNativeItem" || it.typename === "PlacedItem" || it.typename === "RasterItem") {
      o += ',"warning":"non-editable"';
    }
    return o + "}";
  }
  try {
    doc = app.open(new File(path));
    var layers = [];
    for (var l = 0; l < doc.layers.length; l++) {
      var L = doc.layers[l], items = [];
      for (var i = 0; i < L.pageItems.length; i++) {
        if (L.pageItems[i].parent === L) items.push(walk(L.pageItems[i], 0));
      }
      layers.push('{"name":' + esc(L.name) + ',"items":[' + items.join(",") + "]}");
    }
    var out = '{"layers":[' + layers.join(",") + '],"gradients":' + doc.gradients.length + ',"nonNative":' + doc.nonNativeItems.length + "}";
    return out;
  } catch (err) {
    return '{"error":' + esc(err) + "}";
  } finally {
    if (doc) doc.close(SaveOptions.DONOTSAVECHANGES);
    app.userInteractionLevel = prev;
  }
})(arguments);
