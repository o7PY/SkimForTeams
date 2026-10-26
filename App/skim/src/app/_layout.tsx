import { Manrope_300Light, Manrope_500Medium, Manrope_600SemiBold, Manrope_700Bold, useFonts } from '@expo-google-fonts/manrope';
import { DarkTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

import { colors, space } from '@/constants/skim-theme';
import { AuthProvider, useAuth } from '@/lib/auth';
import { loaded } from '@/lib/config';
import { NotificationsRuntime } from '@/lib/notifications-runtime';
import { OfflineSync } from '@/lib/offline-runtime';
import '@/lib/background'; // defines the background task at module scope, as expo-task-manager requires
import { SessionProviders } from '@/lib/session';
import { AppText } from '@/ui/app-text';

SplashScreen.preventAutoHideAsync();

const theme = { ...DarkTheme, colors: { ...DarkTheme.colors, background: colors.surface, card: colors.surface, primary: colors.primary } };

function Routes({ ready }: { ready: boolean }) {
  const { status } = useAuth();
  useEffect(() => {
    if (status !== 'loading' && ready) SplashScreen.hideAsync();
  }, [status, ready]);
  if (status === 'loading' || !ready) return null;
  return (
    <>
      <NotificationsRuntime />
      <OfflineSync />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surface }, animation: 'fade_from_bottom' }}>
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
  const ready = fontsLoaded || !!fontError; // a font that fails to load must not keep the app on the splash screen

  if (!('config' in loaded)) {
    // A build without its public values: say so plainly instead of crashing at launch.
    SplashScreen.hideAsync();
    return (
      <View style={styles.problem}>
        <AppText variant="headlineMd">This build is missing its settings</AppText>
        {loaded.problems.map((p) => (
          <AppText key={p} variant="body" color={colors.onSurfaceVariant}>{p}</AppText>
        ))}
      </View>
    );
  }
  return (
    <GestureHandlerRootView style={styles.root}>
      <ThemeProvider value={theme}>
        <StatusBar style="light" />
        <AuthProvider>
          <SessionProviders>
            <Routes ready={ready} />
          </SessionProviders>
        </AuthProvider>
      </ThemeProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  problem: { flex: 1, backgroundColor: colors.surface, justifyContent: 'center', padding: space.gutter, gap: space.sm },
});
