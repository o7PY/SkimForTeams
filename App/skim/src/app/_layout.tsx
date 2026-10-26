import { Manrope_300Light, Manrope_500Medium, Manrope_600SemiBold, Manrope_700Bold, useFonts } from '@expo-google-fonts/manrope';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

import { makeStyles, space, useTheme, type Theme } from '@/constants/theme';
import { AuthProvider, useAuth } from '@/lib/auth';
import { loaded } from '@/lib/config';
import { NotificationsRuntime } from '@/lib/notifications-runtime';
import { OfflineSync } from '@/lib/offline-runtime';
import '@/lib/background'; // defines the background task at module scope, as expo-task-manager requires
import { SessionProviders } from '@/lib/session';
import { AppText } from '@/ui/app-text';

SplashScreen.preventAutoHideAsync();

const navTheme = (t: Theme) => {
  const base = t.scheme === 'dark' ? DarkTheme : DefaultTheme;
  return { ...base, colors: { ...base.colors, background: t.background, card: t.background, primary: t.primary } };
};

function Routes({ ready }: { ready: boolean }) {
  const { status } = useAuth();
  const t = useTheme();
  useEffect(() => {
    if (status !== 'loading' && ready) SplashScreen.hideAsync();
  }, [status, ready]);
  if (status === 'loading' || !ready) return null;
  return (
    <>
      <NotificationsRuntime />
      <OfflineSync />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.background }, animation: 'fade_from_bottom' }}>
        <Stack.Protected guard={status === 'signedIn'}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="join" />
          <Stack.Screen name="settings" />
          <Stack.Screen name="item/[id]" />
        </Stack.Protected>
        <Stack.Protected guard={status === 'signedOut'}>
          <Stack.Screen name="sign-in" />
        </Stack.Protected>
      </Stack>
    </>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({ Manrope_300Light, Manrope_500Medium, Manrope_600SemiBold, Manrope_700Bold });
  const t = useTheme();
  const styles = useStyles();
  const ready = fontsLoaded || !!fontError; // a font that fails to load must not keep the app on the splash screen

  if (!('config' in loaded)) {
    // A build without its public values: say so plainly instead of crashing at launch.
    SplashScreen.hideAsync();
    return (
      <View style={styles.problem}>
        <AppText variant="headlineMd">This build is missing its settings</AppText>
        {loaded.problems.map((p) => (
          <AppText key={p} variant="body" color={t.textSecondary}>{p}</AppText>
        ))}
      </View>
    );
  }
  return (
    <GestureHandlerRootView style={styles.root}>
      <ThemeProvider value={navTheme(t)}>
        <StatusBar style={t.scheme === 'dark' ? 'light' : 'dark'} />
        <AuthProvider>
          <SessionProviders>
            <Routes ready={ready} />
          </SessionProviders>
        </AuthProvider>
      </ThemeProvider>
    </GestureHandlerRootView>
  );
}

const useStyles = makeStyles((t) => ({
  root: { flex: 1, backgroundColor: t.background },
  problem: { flex: 1, backgroundColor: t.background, justifyContent: 'center', padding: space.gutter, gap: space.sm },
}));
