# SVG and computational chart exports use one frozen source revision

PDF, PNG, TIFF, SVG and EPS consumers share the frozen model, binding, data, source, profile and ordered edits for an export revision. Static SVG, raster and original-size exports retain their source geometry and report unsupported format capabilities explicitly. PDF continues through the existing PDF backend and RenderCore.

## SVG assets can be exported with a layout or at their original size

SVG assets can now be included in PDF, PNG, TIFF and SVG layout exports, and exported at their own size with margins or annotations. Formats exported together use the same source version, so a file changing during export cannot silently produce different pictures. Each format reports SVG features it cannot render while other successful outputs remain available.

## Batch figure exports keep each job and filename separate

Starting a batch after another export no longer lets the previous result skip figures or bypass filename conflicts. Figures with the same output name keep the normal rename, replace and cancel choices. Runtime figures without a saved display name receive a normal default filename while keeping their original source identity.

## Original-image exports can find files hidden by a same-name PDF

A PNG already placed on a layout remains available for original-size export when the asset list prefers its same-name PDF. Exports preserve the image's pixels and declared density. Raster exports of a static PDF report its actual original page size instead of an outdated document size.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR-XXXX.
