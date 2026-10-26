// The palette contract (P5-D-104/105, acceptance B2/B3): every colour lives in constants/theme.ts, every text pair passes WCAG AA in
// both themes, and red belongs to "important" alone.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { CHIP_ALPHA, EVENT_TYPE_IDS, teamAccent, themes, tint, typeTone, type Theme } from '@/constants/theme';

const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const hue = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  if (d === 0) return 0;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
};
const both: [string, Theme][] = [['dark', themes.dark], ['light', themes.light]];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? (n === '__tests__' ? [] : sourceFiles(p)) : /\.(ts|tsx)$/.test(n) ? [p] : [];
  });
}

test('no colour literal exists outside constants/theme.ts', () => {
  const root = join(__dirname, '..');
  const offenders = sourceFiles(root)
    .filter((f) => !f.endsWith(join('constants', 'theme.ts')))
    .filter((f) => /#[0-9A-Fa-f]{3,8}\b|\brgba?\(|\bhsla?\(/.test(readFileSync(f, 'utf8').replace(/\/\/.*$/gm, '')));
  expect(offenders).toEqual([]);
});

describe.each(both)('%s theme: contrast (WCAG AA)', (_name, t) => {
  test('body text on every surface is at least 4.5:1', () => {
    for (const bg of [t.background, t.surface, t.surfaceRaised, t.inset, t.primarySoft, t.important.fill])
      for (const fg of [t.textPrimary, t.textSecondary]) expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
  test('the primary button and primary text read', () => {
    expect(ratio(t.onPrimary, t.primary)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t.primary, t.background)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t.primary, t.primarySoft)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t.important.onSolid, t.important.solid)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t.important.text, t.important.fill)).toBeGreaterThanOrEqual(4.5);
  });
  test('every event-type chip, and the semantic chips, read (text on a 12% tint of itself over the card)', () => {
    for (const id of EVENT_TYPE_IDS) {
      const { fg, bg } = typeTone(t, id);
      expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(fg, t.surfaceRaised)).toBeGreaterThanOrEqual(3); // the icon and the checklist dot against the card
      expect(ratio(fg, t.background)).toBeGreaterThanOrEqual(4.5);
    }
    for (const fg of [t.update, t.success, t.warning, t.offline, t.important.text]) expect(ratio(fg, tint(fg, t.surfaceRaised, CHIP_ALPHA))).toBeGreaterThanOrEqual(4.5);
  });
  test('team dots are visible on a card (3:1)', () => {
    expect(t.teams).toHaveLength(8);
    for (const c of t.teams) expect(ratio(c, t.surfaceRaised)).toBeGreaterThanOrEqual(3);
  });
});

describe.each(both)('%s theme: red is reserved for "important"', (_name, t) => {
  const nearRed = (hex: string) => {
    const h = hue(hex);
    return Math.min(h, 360 - h) < 25;
  };
  test('no event type, team, update, success, warning or primary colour is red-ish', () => {
    for (const c of [...Object.values(t.types), ...t.teams, t.update, t.success, t.warning, t.offline, t.primary]) expect(nearRed(c)).toBe(false);
  });
  test('the important colours are red', () => {
    for (const c of [t.important.border, t.important.text, t.important.solid]) expect(nearRed(c)).toBe(true);
  });
});

test('a type colour is the same wherever it is asked for, and unknown types look like "other"', () => {
  expect(typeTone(themes.dark, 'quiz')).toEqual(typeTone(themes.dark, 'quiz'));
  expect(typeTone(themes.dark, 'mystery')).toEqual(typeTone(themes.dark, 'other'));
  expect(new Set(Object.values(themes.dark.types)).size).toBe(EVENT_TYPE_IDS.length); // 9 distinct hues
});

test('a team keeps its accent, and accents spread over the palette', () => {
  expect(teamAccent(themes.dark, 'team-1')).toBe(teamAccent(themes.dark, 'team-1'));
  const used = new Set(Array.from({ length: 40 }, (_, i) => teamAccent(themes.dark, `19:channel-${i}@thread.tacv2`)));
  expect(used.size).toBeGreaterThanOrEqual(5);
});
