import { GoogleSignin, isErrorWithCode, isSuccessResponse, statusCodes } from '@react-native-google-signin/google-signin';
import type { Session } from '@supabase/supabase-js';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { createApi, type Api } from './api';
import { loaded } from './config';
import { getSupabase } from './supabase';

export type SignInResult = { ok: true } | { ok: false; cancelled?: boolean; message?: string };

type AuthState = {
  status: 'loading' | 'signedOut' | 'signedIn';
  session: Session | null;
  api: Api;
  signInWithGoogle(): Promise<SignInResult>;
  signOut(): Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

/** A human message for a Google sign-in failure. Never includes tokens. */
export function googleErrorMessage(e: unknown): string {
  if (isErrorWithCode(e)) {
    if (e.code === statusCodes.PLAY_SERVICES_NOT_AVAILABLE) return 'Google Play services are missing or out of date on this phone.';
    if (e.code === statusCodes.IN_PROGRESS) return 'Sign-in is already in progress.';
    if (String(e.code).includes('DEVELOPER_ERROR') || String(e.message).includes('DEVELOPER_ERROR') || String(e.code) === '10') {
      return 'Google sign-in is not set up for this build yet (the app\'s SHA-1 is missing in Google Cloud).';
    }
    return `Google sign-in failed (${e.code}).`;
  }
  return 'Google sign-in failed. Please try again.';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const supabase = useMemo(() => getSupabase(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthState['status']>('loading');

  useEffect(() => {
    if (!('config' in loaded)) return;
    GoogleSignin.configure({ webClientId: loaded.config.googleWebClientId });
    let alive = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!alive) return;
      setSession(data.session);
      setStatus(data.session ? 'signedIn' : 'signedOut');
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      setStatus(s ? 'signedIn' : 'signedOut');
    });
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, [supabase]);

  const signOut = useCallback(async () => {
    try {
      await GoogleSignin.signOut();
    } catch {
      // not signed in with Google on this device: nothing to clear
    }
    await supabase.auth.signOut({ scope: 'local' });
  }, [supabase]);

  const api = useMemo(
    () =>
      createApi({
        baseUrl: 'config' in loaded ? loaded.config.apiBase : '',
        tokens: {
          getToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
          refresh: async () => (await supabase.auth.refreshSession()).data.session?.access_token ?? null,
          onSignedOut: () => {
            void supabase.auth.signOut({ scope: 'local' });
          },
        },
      }),
    [supabase],
  );

  const signInWithGoogle = useCallback(async (): Promise<SignInResult> => {
    try {
      await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
      const response = await GoogleSignin.signIn();
      if (!isSuccessResponse(response)) return { ok: false, cancelled: true };
      const idToken = response.data.idToken;
      if (!idToken) return { ok: false, message: 'Google did not return an ID token. Check the web client ID.' };
      const { error } = await supabase.auth.signInWithIdToken({ provider: 'google', token: idToken });
      if (error) return { ok: false, message: 'Sign-in with Supabase failed. Is the Google provider enabled for this project?' };
      return { ok: true };
    } catch (e) {
      return { ok: false, message: googleErrorMessage(e) };
    }
  }, [supabase]);

  const value = useMemo<AuthState>(() => ({ status, session, api, signInWithGoogle, signOut }), [status, session, api, signInWithGoogle, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(AuthContext);
  if (!v) throw new Error('useAuth must be used inside <AuthProvider>');
  return v;
}
