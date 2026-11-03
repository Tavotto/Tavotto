# Editing reference: what you can change, and how your script stays the source

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## Edit inside the figure

Double-click a panel and Tavotto runs your script once, keeping the matplotlib
`Figure` in memory. From then on you are editing that figure directly: pick the
title, an axis label, a tick, a curve, the legend, an arrow your script drew — from
the tree or by clicking it on the canvas — and change its size, colour, weight, dash
pattern, or just drag it somewhere else.

**Dragging and dialling are instant.** The figure follows the cursor frame by frame;
matplotlib runs once, when you let go, to make the change official. Hit-testing
follows the drawn geometry rather than bounding boxes, so clicking a curve selects
the curve and not the empty rectangle around it.

## Build the page

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/layout.png" width="100%"
       alt="The same window in layout mode: the asset library on the left showing the three source PDFs with their physical sizes, the composed page in the middle with one panel selected, and its position, size in millimetres and scale on the right.">
</p>

Panels land on a page measured in millimetres, at the size their script drew them.
Drag them, snap them to each other, align and distribute a selection, group what
belongs together, or bind panels into a row, column or grid that reflows when a size
changes. Panel labels (a)(b)(c) come from one command. Annotations — text, arrows,
shapes, at any angle — sit on top, with presets for the usual research furniture:
reversible-reaction arrows, scale bars, zoom boxes.

## Your script is still the source

Editing a figure never touches your `.py` file. Every change is stored as an override
in the layout and replayed onto a fresh run of your script whenever the figure is
opened again — which is also why undo, version history and re-rendering at export
quality all work on the same footing. (The one exception is the optional assistant
below, which you have to ask for by name.)

If you *do* want a change baked into the figure file on disk, "write back to the
original file" is an explicit action: it re-runs your script from scratch to prove the
result matches what you were looking at, and it can be locked off per project.

## What you can edit inside a figure

| | |
|---|---|
| **Text** | Title, axis labels, tick labels, legend entries, annotations — content, size, colour, weight, style, rotation, opacity, visibility. Draggable. |
| **Data series** | Line width, dash pattern, colour, markers (scatter markers can be swapped wholesale), legend entry order |
| **Arrows** | Arrows your script drew (`FancyArrowPatch`): drag the whole arrow or either endpoint, change arrow style, line style, width, head size, colour. Arrows attached to `annotate()` keep their data anchors — style only. |
| **Axes** | Tick locators and formatters (how many ticks, where, written how), tick marks, grid, spines individually or all four, limits, scales, aspect. Drag a subplot and what belongs to it travels along — a label you had moved, its colourbar, a twin axis. |
| **Colourbars** | Orientation, both-ended extend triangles, colour map, range, tick and label styling — rebuilt in place, so undo, write-back and re-export stay consistent |
| **3D axes** | Viewing angle (elev / azim / roll), projection, axis lines, panes, grid, per-axis tick groups, optional axis arrows |
| **Figure** | Overall size in millimetres (the layout reflows), background |

What Tavotto does *not* do is invent plot content. It changes the properties of things
your script already drew; new curves, new panels and different data still come from
the script — which is the point.

## Assistant (optional)

The assistant panel can hand a request to the **Codex or Claude CLI** on your machine
to edit the script itself — "move the legend to the top left and make it 7 pt". Your
script is snapshotted first; afterwards Tavotto re-reads the project (the same refresh
the Codex plugin and the file watcher use), you see the diff, the figure re-renders, and
one click reverts it. If the refresh fails, the status line says so instead of pretending
the whole edit succeeded. This is the one path that touches your source, and you have to
ask for it. Everything else works without those tools installed.
