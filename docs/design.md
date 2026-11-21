# Visual design

The GUI is an operations console: people come to it to finish a task (attach a segment, read a
patch, approve a deploy), often under time pressure. Everything lives in `gui/src/styles.css` as
tokens; components never use a literal colour.

## World

Asset labels: the printed labels on racks, switches and patch panels. Black ink on white
label stock (white print on carbon stock in the dark theme), flat boxes with soft rounded corners and hairline rules, sentence-case headings, one signal colour taken from fibre (aqua), and colour-coded label stock for
wayfinding, as racks are labelled by zone.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f1f2ef` | `#121311` | label stock: the working area |
| `--surface` / `--surface-2` | `#fbfbfa` / `#e6e8e3` | `#1a1b18` / `#23241f` | panels, tables, inputs / header bands and rows |
| `--text` | `#1a1b18` | `#ecebe3` | ink |
| `--rule` | `#aaaea5` | `#4a4b43` | the edge of a panel or a cell |
| `--signal` | `#19b5a8` | `#3cc4b6` | fibre aqua as a fill, with ink on it: the action to take, the current step |
| `--accent` | `#00756e` | `#52d3c5` | fibre aqua as ink: links, focus, selection, strokes on the map |
| `--good` / `--warn` / `--bad` | `#1a6d38` / `#924600` / `#b3261e` | `#66cc84` / `#f0a54a` / `#ff8b7c` | state only |
| zones | Overview `#19b5a8`, Network `#6aa6f5`, Switches `#5cc06a`, Changes `#f2c230`, Fabric `#f39a52`, Admin `#b9a0f5` | same | each menu section's label stock |
| `--hazard` | `#f2c230` | same | changes waiting to be deployed |

Zone colours are fills that always carry ink (at least 6.7:1), the same in both themes: the
section's chip in the menu and before the page title, the tab underline, a tint (22 %, 13 % in
the dark theme) on panel header bands and the top edge of figures. Healthy, warning and failing figures take a green,
yellow or red edge instead. The pending bar is tinted hazard
yellow, a draft for a window violet, each with a solid edge.
The signal colour never decorates. Status colours always come with a word or an icon. Text on
every surface it sits on is at least 4.5:1. Segments take cable jacket colours (`--s1` to
`--s6`); the drawn front panel has its own fills for what a port carries (`--p-*`), bright enough
to read in a black cage.

## Type

Archivo, one family with a width axis: body at 14 px and normal width; headings, table headers,
tags, tabs and figures slightly narrow (`--narrow`, 88 %); titles in sentence case, only small
labels (figure labels, table headers) in capitals. Red Hat
Mono only for data: config, addresses, port and switch names. Figures are tabular. Both are
bundled, never fetched.

## Structure

- Flat and soft: panels and cells are edged by a 1 px hairline with a barely visible shadow; a
  panel's title sits on a tinted band. Overlays (tooltips, popovers, the walk-through) cast a real
  shadow (`--overlay-shadow`).
- Rounded corners: 5-6 px on controls and tags, 10 px on panels and figures.
- One page head: a chip of the section's colour, the page's title, then the fabric, over a
  hairline. Pages and in-page tabs are underlined in the section's colour; segmented toggles raise
  the chosen option.
- The overview's figures are a row of cards; a figure turns warning or red only when
  its value is a problem.
- Motion only answers a person's action or shows a state that is changing, 150-400 ms with an
  exponential ease-out and no overshoot, and stops under `prefers-reduced-motion`.
