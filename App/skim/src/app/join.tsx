import { useQueryClient } from '@tanstack/react-query';
import { router } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, StyleSheet, TextInput, View } from 'react-native';

import { colors, radius, solid, space } from '@/constants/skim-theme';
import { useAuth } from '@/lib/auth';
import { alreadyJoinedTeam, canSubmit, joinErrorMessage, normalizeCode, type TeamInfo } from '@/lib/join';
import { usePush } from '@/lib/push-runtime';
import { queryKeys } from '@/lib/query';
import { useSubscriptions } from '@/lib/queries';
import { AppText } from '@/ui/app-text';
import { PrimaryButton, TextButton } from '@/ui/buttons';
import { Icon } from '@/ui/icon';
import { Screen } from '@/ui/screen';

export default function Join() {
  const { api } = useAuth();
  const qc = useQueryClient();
  const push = usePush();
  const subs = useSubscriptions();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [joined, setJoined] = useState<{ team: TeamInfo; already: boolean } | null>(null);
  const hasTeams = (subs.data?.length ?? 0) > 0;

  async function submit() {
    if (busy || !canSubmit(code)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ team: TeamInfo }>('/subscriptions', { code: normalizeCode(code) });
      setJoined({ team: r.team, already: false });
    } catch (e) {
      const team = alreadyJoinedTeam(e);
      if (team) setJoined({ team, already: true });
      else setError(joinErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function done() {
    const firstTeam = !hasTeams;
    await Promise.all([qc.invalidateQueries({ queryKey: queryKeys.subscriptions }), qc.invalidateQueries({ queryKey: queryKeys.deck })]);
    router.replace('/');
    // The notification permission is asked for right after the first successful join, never at launch (PHASE4.md 7.3).
    if (firstTeam) void push.registerAfterJoin();
  }

  if (joined) {
    return (
      <Screen>
        <View style={styles.center}>
          <Icon name="check-circle-outline" size={56} color={colors.primary} />
          <AppText variant="headlineLg">{joined.already ? 'You’re already in' : 'You’ve joined'}</AppText>
          <AppText variant="headlineMd" color={colors.accentSoft}>{joined.team.name ?? 'your course'}</AppText>
          <AppText variant="body" color={colors.onSurfaceVariant}>
            {joined.already ? 'Nothing changed.' : 'The last 7 days of posts and anything about an upcoming date are in your deck.'}
          </AppText>
        </View>
        <View style={styles.actions}>
          <PrimaryButton label="Go to my deck" onPress={done} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <View style={styles.center}>
          <AppText variant="headlineLg">Join a team</AppText>
          <AppText variant="body" color={colors.onSurfaceVariant}>Type the code your professor’s Skim bot posted in the class team.</AppText>
          <TextInput
            accessibilityLabel="Team code"
            value={code}
            onChangeText={(t) => { setCode(normalizeCode(t)); setError(null); }}
            placeholder="R6BVE4"
            placeholderTextColor={colors.onSurfaceMuted}
            autoCapitalize="characters"
            autoCorrect={false}
            autoComplete="off"
            spellCheck={false}
            maxLength={14}
            returnKeyType="go"
            onSubmitEditing={submit}
            editable={!busy}
            style={styles.input}
          />
          {error ? <AppText variant="body" color={colors.error} accessibilityLiveRegion="polite">{error}</AppText> : null}
        </View>
        <View style={styles.actions}>
          <PrimaryButton label="Join team" busy={busy} disabled={!canSubmit(code)} onPress={submit} />
          {hasTeams ? <TextButton label="Cancel" onPress={() => router.back()} /> : null}
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  center: { flex: 1, justifyContent: 'center', gap: space.md },
  input: {
    height: 64, borderRadius: radius.md, borderWidth: 1, borderColor: solid.cardBorder, backgroundColor: solid.card,
    color: colors.onSurface, fontFamily: 'Manrope_700Bold', fontSize: 28, letterSpacing: 6, textAlign: 'center', paddingHorizontal: space.md,
  },
  actions: { gap: space.sm, paddingBottom: space.md, alignItems: 'stretch' },
});
