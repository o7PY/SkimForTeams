// Every screen the root layout names must exist as a route. A name that does not (e.g. "index" after the deck moved into the
// "(tabs)" group) only shows up as a warning on the phone and leaves the real screen outside the sign-in guard.
import { existsSync, readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const APP = join(__dirname, '..', 'app');
/** "join" -> app/join.tsx; "(tabs)" -> the folder app/(tabs); "item/[id]" -> app/item/[id].tsx */
const routeExists = (name: string) => ['.tsx', '.ts'].some((ext) => existsSync(join(APP, name + ext))) || (existsSync(join(APP, name)) && statSync(join(APP, name)).isDirectory());
const named = (file: string) => [...readFileSync(file, 'utf8').matchAll(/<(?:Stack|Tabs)\.Screen\s+name="([^"]+)"/g)].map((m) => m[1]);

test('every screen named in the root layout is a real route', () => {
  const names = named(join(APP, '_layout.tsx'));
  expect(names.length).toBeGreaterThan(4);
  for (const n of names) expect([n, routeExists(n)]).toEqual([n, true]);
});

test('the deck is reached through (tabs), and nothing still names the old top-level "index"', () => {
  const names = named(join(APP, '_layout.tsx'));
  expect(names).toContain('(tabs)');
  expect(names).not.toContain('index');
  expect(existsSync(join(APP, 'index.tsx'))).toBe(false);
});

test('every tab named in the tabs layout is a real file in the group', () => {
  const dir = join(APP, '(tabs)');
  const files = readdirSync(dir).map((f: string) => f.replace(/\.tsx?$/, ''));
  const tabs = [...readFileSync(join(dir, '_layout.tsx'), 'utf8').matchAll(/name:\s*'([^']+)'/g)].map((m) => m[1]);
  expect(tabs).toEqual(['index', 'history', 'starred', 'important']);
  for (const t of tabs) expect([t, files.includes(t)]).toEqual([t, true]);
});

test('every screen in the root layout that needs a signed-in user sits inside the signed-in guard', () => {
  const src = readFileSync(join(APP, '_layout.tsx'), 'utf8');
  const guarded = src.slice(src.indexOf("guard={status === 'signedIn'}"), src.indexOf("guard={status === 'signedOut'}"));
  for (const n of ['(tabs)', 'join', 'settings', 'item/[id]']) expect(guarded).toContain(`name="${n}"`);
  expect(src.slice(src.indexOf("guard={status === 'signedOut'}"))).toContain('name="sign-in"');
});
