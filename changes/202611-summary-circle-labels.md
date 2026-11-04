# Summary page: fit counts inside the status circles

## Overview

The summary page (`/`, `/summary/`) draws a circle per status (Active, Aging,
Stale, Dormant) for developers and for repositories. The diameter is
`percentage + 40` px, but every count was printed in 18px bold, so on a large
estate a four-digit count in a 3% circle (~43px) ran past the edge.

Counts are now compacted to at most four characters (`999`, `2.8k`, `66k`,
`1.3M`), and the font is sized from the diameter (18px max, 12px floor). The
exact count is in the circle's tooltip, and it is also printed under the status
label (e.g. "Dormant / 66,362 / 89%"). Circle sizes and percentages are
unchanged.

## Files changed

- `src/kospex_web.py` - `compact_count()`, `circle_label_px()`,
  `status_circles()` and the `SUMMARY_STATUSES` table (key, label, colour).
- `src/kweb2.py` - `summary()` passes `dev_circles` / `repo_circles` and totals
  instead of building percentage/size dicts inline.
- `src/templates/_status_circles_macro.html` - new `status_row()` macro.
- `src/templates/summary.html` - the eight copied circle blocks replaced by two
  macro calls.
- `tests/test_summary_circles.py` - formatting, font fit and circle shape.

## Implementation notes

- Font fit assumes a bold digit is ~0.62em wide and lets the label span 85% of
  the diameter. With a four-character cap, the 12px floor never has to apply at
  the 40px minimum diameter.
- `line-height: 1` is inline because `leading-none` is not in the compiled
  Tailwind CSS.

## Not changed (open question)

Diameter is linear in percentage with a +40px floor, so 3% vs 89% draws as
43px vs 129px: 3x the width for 30x the value. An area-true encoding
(`d = D_max * sqrt(p)`) would be more honest but makes small circles smaller,
so it needs a floor and an outside-label fallback. Left as a separate decision.
