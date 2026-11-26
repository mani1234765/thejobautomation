# JobPilot Design System

Tokens live in `frontend/src/app/globals.css`; components in `frontend/src/components/ui/`.

## Principles

1. **Calm by default, loud only for decisions.** Neutral "paper & graphite" surfaces; a single accent
   ("runway" green) marks primary actions and good fit. Amber is reserved for things that need the user
   (approvals, unknown answers, pay to review); red only for failure or ineligibility.
2. **Density with hierarchy.** 13px base UI text (Linear-like density), 14–18px for page titles, numbers in
   tabular figures. One heading size per level; no oversized hero type inside the product.
3. **Explain, don't decorate.** Every score shows its factors; every "unknown" is drawn differently from
   "zero" (dashed bar, `—`, "Not scored").
4. **Every screen has four states.** Loading (skeletons shaped like the content), empty (what it is +
   the next action), error (message + retry), success (toast or inline confirmation).

## Tokens

| Group | Tokens | Notes |
|---|---|---|
| Surfaces | `bg`, `surface`, `surface-2`, `surface-3`, `border`, `border-strong` | OKLCH warm neutrals; dark mode is a separately tuned set, not an inversion |
| Text | `fg`, `fg-muted`, `fg-subtle` | Text never uses series or status colours |
| Accent | `accent`, `accent-hover`, `accent-fg`, `accent-soft`, `accent-soft-fg` | Primary buttons, positive fit, selection |
| Status | `positive`, `warning`, `danger`, `info` + `*-soft` | Always paired with a label or icon |
| Chart series | `series-1..3` (blue, orange, aqua) | Validated with the dataviz palette checker in both modes (CVD ΔE ≥ 9, normal-vision ΔE ≥ 26); light aqua < 3:1, so every chart ships a legend **and** a table view |
| Radius | 5 / 7 / 10 / 14 px | Controls 7px, cards 10px |
| Shadow | `sm`, `md`, `lg` | Cards `sm`, popovers `lg`; borders do most of the work |
| Type | Geist Sans / Geist Mono | `.tabular` for numbers |

Theme: light/dark/system, chosen in the user menu, applied before first paint (no flash).
Motion: 150–200ms colour/opacity transitions; one 180ms step transition in onboarding; everything
respects `prefers-reduced-motion`.

## Components

`Button` (primary · secondary · ghost · subtle · danger · link; sm/md/lg/icon; `loading`), `Input`,
`Textarea`, `NativeSelect`, `Field` (auto-binds its label to the control), `Badge` (tone + dot), `Card` /
`CardHeader`, `Skeleton`, `Progress`, `Kbd`, `Dialog` / `SheetContent` / `ConfirmDialog`, `Menu*`,
`Popover`, `Tooltip`, `Switch`, `Checkbox`, `Tabs`, `Segmented`, `ChipGroup`, `TagInput`, `ScoreRing`,
`FactorBar`, `PageHeader`, `EmptyState`, `ErrorState`, `Callout`, `ListSkeleton`, `TrendChart`, `BarList`,
`PipelineStrip`.

## Patterns

* **App shell**: 232px sidebar (main + workspace groups), sticky mobile top bar with drawer, ⌘K command
  palette, `G` then `O/J/A/R` navigation shortcuts, `/` focuses job search, skip-to-content link.
* **Lists over card grids**: jobs and applications are scannable rows; detail opens in a right sheet or
  its own page. The application board is the one kanban view.
* **Detail panels** keep the primary action first and destructive actions behind a confirm.
* **Forms** validate on submit with inline messages; server errors appear as a callout or toast with
  the API's own message.

## UI audit (Phase 9) — findings and fixes

Audited every screen in a real browser (Playwright, 1440×900 and 390×844, light and dark) plus axe
(WCAG 2 A/AA) on each product page.

| Finding | Fix |
|---|---|
| Job rows had uneven heights (skill badges and "9 months ago" wrapping) | Single-line badge rail capped at 3, no-wrap timestamps |
| "Review pay" badge on every USD role dominated the list | Moved to a quiet amber wallet icon with an explanatory tooltip; the full reason stays in the detail panel |
| A posting with no skills/experience scored 93 from title + location alone | Scoring now shrinks low-confidence scores toward neutral and labels them "Low confidence" |
| "Close a skill gap: Communication" — generic advice from unrelated roles | Gaps are computed only from jobs that match you and exclude soft skills ("LLMs — asked for in 9 of your 30 matching jobs") |
| Timestamps showed "6 hours ago" for new events (naive UTC read as local time) | All datetimes are timezone-aware end to end (`UTCDateTime`) |
| Chart y-axis labels clipped; pipeline stage names truncated | Axis width fixed; stage labels wrap to two lines |
| axe: `aria-label` on a non-interactive span (notifications) | Dot is decorative; unread state is announced with screen-reader text |
| axe: 13 settings inputs without labels | `Field` now binds its label to its control automatically, app-wide |
| Next dev badge overlapped the user menu | Moved to bottom-right (dev only) |

Generic-AI patterns deliberately avoided: gradient-heavy cards, colourful stat tiles, emoji, oversized
headings, fake sample numbers. The landing page's single illustration is explicitly labelled "Example".
