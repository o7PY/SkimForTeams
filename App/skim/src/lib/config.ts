// Public build-time values (PHASE4.md section 4). Each one must be read as a literal `process.env.EXPO_PUBLIC_X`: Expo inlines
// those at bundle time, a dynamic lookup would stay undefined in the APK.
export type AppConfig = {
  apiBase: string;
  supabaseUrl: string;
  supabaseAnonKey: string;
  googleWebClientId: string;
};

type Raw = Partial<Record<keyof AppConfig, string | undefined>>;

const LABELS: Record<keyof AppConfig, string> = {
  apiBase: 'EXPO_PUBLIC_API_BASE',
  supabaseUrl: 'EXPO_PUBLIC_SUPABASE_URL',
  supabaseAnonKey: 'EXPO_PUBLIC_SUPABASE_ANON_KEY',
  googleWebClientId: 'EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID',
};

/** Validate raw values; returns the config or the list of problems (names only, never values). */
export function parseConfig(raw: Raw): { config: AppConfig } | { problems: string[] } {
  const problems: string[] = [];
  const clean = (k: keyof AppConfig) => (raw[k] ?? '').trim();
  for (const k of Object.keys(LABELS) as (keyof AppConfig)[]) {
    if (!clean(k)) problems.push(`${LABELS[k]} is not set`);
  }
  for (const k of ['apiBase', 'supabaseUrl'] as const) {
    const v = clean(k);
    if (v && !/^https:\/\/[^\s/]+/.test(v) && !(__DEV__ && /^http:\/\/(localhost|10\.|192\.168\.)/.test(v))) {
      problems.push(`${LABELS[k]} must start with https://`);
    }
  }
  if (problems.length) return { problems };
  return {
    config: {
      apiBase: clean('apiBase').replace(/\/+$/, ''),
      supabaseUrl: clean('supabaseUrl').replace(/\/+$/, ''),
      supabaseAnonKey: clean('supabaseAnonKey'),
      googleWebClientId: clean('googleWebClientId'),
    },
  };
}

export const loaded = parseConfig({
  apiBase: process.env.EXPO_PUBLIC_API_BASE,
  supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL,
  supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY,
  googleWebClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
});
