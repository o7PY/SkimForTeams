// THE one place colours live (P5-D-104/105; palette approved from Bookkeeping/design-palette.md). No other file under src may hold a
// colour literal: __tests__/theme.test.ts fails the build if one appears. Light and dark are both complete; every text/background
// pair is checked for WCAG AA in that test too.
//
// Rules of the palette: vibrant accents (chips, dots, stripes, icons) on calm backgrounds; red is for "important" only (errors and
// destructive actions use `warning` + an icon instead); colour is never the only signal (chips always carry a label).
import { StyleSheet, useColorScheme, type ImageStyle, type TextStyle, type ViewStyle } from 'react-native';

export const radius = { sm: 8, md: 16, lg: 24, xl: 32, full: 9999 } as const;
export const space = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, gutter: 24, card: 20 } as const;

export const EVENT_TYPE_IDS = ['quiz', 'exam', 'assignment', 'project', 'lab', 'class', 'office_hours', 'presentation', 'other'] as const;
export type EventTypeId = (typeof EVENT_TYPE_IDS)[number];

export type Theme = {
  scheme: 'dark' | 'light';
  background: string;
  surface: string; // list-row and page-level panels
  surfaceRaised: string; // cards, sheets, the tab bar
  inset: string; // a quieter well inside a card (file chips, option rows, inputs)
  textPrimary: string;
  textSecondary: string;
  border: string;
  primary: string;
  onPrimary: string;
  primarySoft: string; // selected rows and the "change" block
  primaryBorder: string;
  update: string; // every change card: one shared colour
  success: string;
  warning: string; // also errors and destructive actions: red is reserved for "important"
  offline: string;
  important: { fill: string; border: string; text: string; solid: string; onSolid: string };
  scrim: string; // dims the screen behind a sheet
  shadow: string;
  switchOn: string;
  switchThumbOff: string;
  bloom: { a: string; b: string; c: string; top: string }; // the calm field behind every screen
  types: Record<EventTypeId, string>;
  teams: readonly string[]; // 8 soft accents for the team dot, assigned by team id
};

const TYPES_DARK: Theme['types'] = {
  quiz: '#FACC15', exam: '#E879F9', assignment: '#7DB4FB', project: '#CE9CFD', lab: '#A3E635', class: '#38BDF8',
  office_hours: '#2DD4BF', presentation: '#4ADE80', other: '#A3B1C6',
};
const TYPES_LIGHT: Theme['types'] = {
  quiz: '#854D0E', exam: '#A21CAF', assignment: '#1D4ED8', project: '#7E22CE', lab: '#3F6212', class: '#0369A1',
  office_hours: '#0F766E', presentation: '#166534', other: '#475569',
};

export const themes: { dark: Theme; light: Theme } = {
  dark: {
    scheme: 'dark',
    background: '#0F172A', surface: '#1B2235', surfaceRaised: '#272E3F', inset: '#323A4B',
    textPrimary: '#F8FAFC', textSecondary: '#B6C2D4', border: '#3A4254',
    primary: '#38BDF8', onPrimary: '#0F172A', primarySoft: '#18425A', primaryBorder: 'rgba(56,189,248,0.30)',
    update: '#A5B4FC', success: '#4ADE80', warning: '#FBBF24', offline: '#A3B1C6',
    important: { fill: '#2B1D2E', border: '#EF4444', text: '#FCA5A5', solid: '#DC2626', onSolid: '#FFFFFF' },
    scrim: 'rgba(2,6,23,0.72)', shadow: 'rgba(56,189,248,0.40)', switchOn: '#0E7490', switchThumbOff: '#CBD5E1',
    bloom: { a: 'rgba(37,99,235,0.30)', b: 'rgba(6,182,212,0.20)', c: 'rgba(99,102,241,0.22)', top: 'rgba(30,41,59,0.85)' },
    types: TYPES_DARK,
    teams: ['#5EEAD4', '#93C5FD', '#C4B5FD', '#F0ABFC', '#FDE68A', '#BEF264', '#67E8F9', '#E7D3B7'],
  },
  light: {
    scheme: 'light',
    background: '#F3F6FB', surface: '#FFFFFF', surfaceRaised: '#FFFFFF', inset: '#EEF2F8',
    textPrimary: '#0F172A', textSecondary: '#475569', border: '#D3DBE8',
    primary: '#0369A1', onPrimary: '#FFFFFF', primarySoft: '#E0F2FE', primaryBorder: '#7DD3FC',
    update: '#4338CA', success: '#166534', warning: '#7C4A03', offline: '#475569',
    important: { fill: '#FEF2F2', border: '#DC2626', text: '#B91C1C', solid: '#DC2626', onSolid: '#FFFFFF' },
    scrim: 'rgba(15,23,42,0.45)', shadow: 'rgba(3,105,161,0.30)', switchOn: '#7DD3FC', switchThumbOff: '#FFFFFF',
    bloom: { a: 'rgba(56,189,248,0.14)', b: 'rgba(99,102,241,0.08)', c: 'rgba(45,212,191,0.10)', top: 'rgba(255,255,255,0.7)' },
    types: TYPES_LIGHT,
    teams: ['#0F766E', '#1D4ED8', '#6D28D9', '#A21CAF', '#854D0E', '#4D7C0F', '#0E7490', '#7A4A12'],
  },
};

/** The active theme: follows the phone's light/dark setting. */
export function useTheme(): Theme {
  return useColorScheme() === 'light' ? themes.light : themes.dark;
}

/** Styles that depend on the theme: `const useStyles = makeStyles((t) => ({...}))`, then `const styles = useStyles()`. Built once per theme. */
type Styles = Record<string, ViewStyle | TextStyle | ImageStyle>;
export function makeStyles<S extends Styles>(build: (t: Theme) => S): () => S {
  const cache: { dark?: S; light?: S } = {};
  return () => {
    const t = useTheme();
    return (cache[t.scheme] ??= StyleSheet.create(build(t)));
  };
}

const channel = (hex: string, i: number) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16);
/** `fg` blended over `bg` at `alpha` (both #RRGGBB): the tint behind a chip. */
export function tint(fg: string, bg: string, alpha: number): string {
  const c = (i: number) => Math.round(channel(fg, i) * alpha + channel(bg, i) * (1 - alpha));
  return `#${[0, 1, 2].map((i) => c(i).toString(16).padStart(2, '0')).join('').toUpperCase()}`;
}
export const CHIP_ALPHA = 0.12;

/** Chip colours for an event type (unknown types fall back to "other"). */
export function typeTone(t: Theme, type: string | null | undefined): { fg: string; bg: string } {
  const fg = t.types[(EVENT_TYPE_IDS as readonly string[]).includes(type ?? '') ? (type as EventTypeId) : 'other'];
  return { fg, bg: tint(fg, t.surfaceRaised, CHIP_ALPHA) };
}
/** Chip colours for a semantic colour (update, warning, success, offline, primary, important text). */
export function toneOf(t: Theme, fg: string): { fg: string; bg: string } {
  return { fg, bg: tint(fg, t.surfaceRaised, CHIP_ALPHA) };
}

/** A team's accent dot colour: stable per team id (a hash, so no state to keep in sync). */
export function teamAccent(t: Theme, teamId: string): string {
  let h = 0;
  for (let i = 0; i < teamId.length; i++) h = (h * 31 + teamId.charCodeAt(i)) >>> 0;
  return t.teams[h % t.teams.length];
}

/** The notification LED / channel colour (not a screen, so no hook): the brand sky. */
export const NOTIFICATION_COLOR = themes.dark.primary;
