import { parseConfig } from '@/lib/config';

const ok = {
  apiBase: 'https://skim.example.run.app/',
  supabaseUrl: 'https://abc.supabase.co',
  supabaseAnonKey: 'sb_publishable_x',
  googleWebClientId: '1-abc.apps.googleusercontent.com',
};

test('a full config is accepted and trailing slashes are trimmed', () => {
  expect(parseConfig(ok)).toEqual({ config: { ...ok, apiBase: 'https://skim.example.run.app' } });
  expect(parseConfig({ ...ok, supabaseUrl: ' https://abc.supabase.co// ' })).toMatchObject({ config: { supabaseUrl: 'https://abc.supabase.co' } });
});

test('every missing value is named, and only by variable name', () => {
  const r = parseConfig({});
  expect('problems' in r && r.problems).toEqual([
    'EXPO_PUBLIC_API_BASE is not set',
    'EXPO_PUBLIC_SUPABASE_URL is not set',
    'EXPO_PUBLIC_SUPABASE_ANON_KEY is not set',
    'EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID is not set',
  ]);
});

test('blank strings count as missing', () => {
  const r = parseConfig({ ...ok, supabaseAnonKey: '   ' });
  expect('problems' in r && r.problems).toEqual(['EXPO_PUBLIC_SUPABASE_ANON_KEY is not set']);
});

test('the API and Supabase URLs must be https (a plain http one is only tolerated for local development hosts)', () => {
  const r = parseConfig({ ...ok, apiBase: 'http://evil.example', supabaseUrl: 'ftp://x' });
  expect('problems' in r && r.problems).toEqual(['EXPO_PUBLIC_API_BASE must start with https://', 'EXPO_PUBLIC_SUPABASE_URL must start with https://']);
  expect(parseConfig({ ...ok, apiBase: 'http://192.168.1.5:8080' })).toMatchObject({ config: { apiBase: 'http://192.168.1.5:8080' } }); // __DEV__ in jest
});

test('problems never echo a value', () => {
  const r = parseConfig({ ...ok, supabaseAnonKey: '', apiBase: 'http://secret-host.example' });
  expect(JSON.stringify(r)).not.toContain('secret-host');
});
