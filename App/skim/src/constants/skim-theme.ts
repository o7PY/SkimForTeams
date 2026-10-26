// Design tokens from Bookkeeping/Design.md ("Midnight Glass"). Looks live there; this file only mirrors the values.
export const colors = {
  primary: '#38BDF8',
  onPrimary: '#0F172A',
  surface: '#0F172A',
  surfaceAlt: '#222A3A',
  surfaceGlass: 'rgba(255,255,255,0.10)',
  surfaceGlassStrong: 'rgba(56,189,248,0.20)',
  onSurface: '#FFFFFF',
  onSurfaceVariant: 'rgba(255,255,255,0.60)',
  onSurfaceMuted: 'rgba(255,255,255,0.40)',
  borderGlass: 'rgba(255,255,255,0.20)',
  borderGlassFaint: 'rgba(255,255,255,0.10)',
  borderAccent: 'rgba(56,189,248,0.30)',
  accentSoft: '#BAE6FD',
  atmosBlue: '#2563EB',
  atmosCyan: '#06B6D4',
  error: '#EF4444',
} as const;

export const radius = { sm: 8, md: 16, lg: 24, xl: 32, full: 9999 } as const;
export const space = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, gutter: 24, card: 20 } as const;

// Categorical accents live only in icons and small marks (Design.md). `important` red is the design's own error red.
export const accents = { yellow: '#FACC15', orange: '#FB923C', green: '#4ADE80', violet: '#C084FC', cyan: '#67E8F9' } as const;
export const important = { border: 'rgba(239,68,68,0.55)', fill: 'rgba(239,68,68,0.12)', text: '#FCA5A5', solid: '#EF4444' } as const;

// OPAQUE card surfaces. Design.md's translucent glass looks right on a flat page but not on a stack: the cards behind the top card
// showed through it and the text could not be read. These are the same colours the glass fills produce over the slate background
// (white 10% over #0F172A, red 12% over #0F172A), written as solid hex so nothing behind a card can ever show through.
export const solid = { card: '#272E3F', cardHot: '#2B1D2E', cardBorder: '#3A4254', block: '#1F4E66' } as const;
