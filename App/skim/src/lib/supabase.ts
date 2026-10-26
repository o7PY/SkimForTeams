// Supabase is used for sign-in only (P4-D-105): the app never queries tables, all data goes through /api/v1.
import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import * as SecureStore from 'expo-secure-store';
import { AppState } from 'react-native';

import { loaded } from './config';
import { chunkedStorage } from './secure-session-storage';

let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (client) return client;
  if (!('config' in loaded)) throw new Error('App configuration is incomplete');
  client = createClient(loaded.config.supabaseUrl, loaded.config.supabaseAnonKey, {
    auth: {
      storage: chunkedStorage(SecureStore),
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: false, // no browser redirects: native Google sign-in only (P4-D-103)
    },
  });
  // Supabase's React Native guide: refresh tokens only while the app is in the foreground.
  AppState.addEventListener('change', (s) => {
    if (s === 'active') client?.auth.startAutoRefresh();
    else client?.auth.stopAutoRefresh();
  });
  client.auth.startAutoRefresh();
  return client;
}
