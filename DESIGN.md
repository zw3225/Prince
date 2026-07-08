# Design

## Theme

Restrained product interface for a daily market-intelligence desk. The screen should feel useful in a bright office during repeated work sessions: crisp white workspace, precise ink, deep indigo as the action and selection color, and a small set of semantic accents for market, status, and opportunity.

## Palette

```css
:root {
  --bg: oklch(1 0 0);
  --surface: oklch(0.975 0.006 270);
  --surface-strong: oklch(0.945 0.012 270);
  --ink: oklch(0.205 0.026 270);
  --muted: oklch(0.465 0.031 270);
  --primary: oklch(0.36 0.19 270);
  --primary-hover: oklch(0.315 0.195 270);
  --accent: oklch(0.62 0.17 168);
  --warning: oklch(0.66 0.16 64);
  --danger: oklch(0.58 0.18 24);
  --success: oklch(0.56 0.15 150);
  --line: oklch(0.88 0.012 270);
}
```

## Typography

Use a system sans stack for all UI, data, labels, and controls. Keep headings compact, labels direct, and data tables dense. Avoid display fonts and fluid type.

## Components

- App shell with a left context rail and a main analytics workspace.
- Search composer with query, market, and time-window controls.
- Tabs for content, creators, and opportunities.
- Dense ranking rows with platform, market, direct entry links, and verifiable fields.
- Detail side panel for evidence links, captured data, manual-confirmation items, and related entities.
- Source health panel showing access mode, public collection status, and last refresh state.

## Motion

Use short state transitions only for tab changes, panel open/close, hover, and loading skeletons. Respect reduced-motion preferences.
