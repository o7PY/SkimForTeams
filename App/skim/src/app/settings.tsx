// Settings (PHASE4.md 7.2 item 8): Teams, Notifications, Reminders, History size, Account.
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Linking, ScrollView, StyleSheet, TextInput, View } from 'react-native';

import { colors, radius, solid, space } from '@/constants/skim-theme';
import { toIstIso } from '@/lib/actions';
import { formatTime } from '@/lib/format';
import { exactAlarmStatus, requestExactAlarm, scheduleTestReminder, type ExactAlarm } from '@/lib/notif-adapter';
import { permissionAction } from '@/lib/notif-permission';
import { useOutbox } from '@/lib/outbox-runtime';
import { usePush } from '@/lib/push-runtime';
import { useSubscriptions } from '@/lib/queries';
import { formatHours, HISTORY_CHOICES, HISTORY_RANGE, HOURS_RANGE, quietWindow, stepHistory, stepHours, stepTime } from '@/lib/settings';
import { useSignOut } from '@/lib/sign-out';
import { confirmsDelete, DELETE_WORD } from '@/lib/confirm';
import { useDeleteAccount, useTeamActions } from '@/lib/teams';
import { useSettings } from '@/lib/use-settings';
import { useNotificationPermission } from '@/lib/use-permission';
import { AppText } from '@/ui/app-text';
import { IconButton, PrimaryButton, SecondaryButton } from '@/ui/buttons';
import { Pill } from '@/ui/deck-card';
import { Screen } from '@/ui/screen';
import { SectionTitle, StepperRow, SwitchRow } from '@/ui/rows';
import { OptionRow, Sheet } from '@/ui/sheet';

const TIME_STEP = 15; // minutes per tap on a time of day

export default function Settings() {
  const subs = useSubscriptions();
  const outbox = useOutbox();
  const push = usePush();
  const signOut = useSignOut();
  const deleteAccount = useDeleteAccount();
  const teams = useTeamActions();
  const { settings, ready, update } = useSettings();
  const perm = useNotificationPermission();
  const action = permissionAction(perm.state);
  const allowed = perm.state ? perm.state.granted : null;

  const [busy, setBusy] = useState<null | 'signout' | 'delete'>(null);
  const [exact, setExact] = useState<ExactAlarm>('unsupported');
  const [exactTick, setExactTick] = useState(0);
  const [testNote, setTestNote] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [typed, setTyped] = useState('');
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void exactAlarmStatus().then((ex) => alive && setExact(ex));
    return () => {
      alive = false;
    };
  }, [exactTick, perm.state]);

  const q = settings.quiet_hours;
  const r = settings.reminders;
  const confirmLeave = (id: string) => {
    const team = subs.data?.find((t) => t.id === id);
    if (!team) return;
    Alert.alert(`Leave ${team.name ?? 'this team'}?`, 'Its cards, your history, stars and reminders for it will be removed from this phone and your account.', [
      { text: 'Stay', style: 'cancel' },
      { text: 'Leave', style: 'destructive', onPress: async () => { const res = await teams.leave(team); if (!res.ok) Alert.alert('Couldn’t leave', res.message); } },
    ]);
  };

  return (
    <Screen>
      <View style={styles.header}>
        <IconButton icon="arrow-left" label="Back" onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} />
        <AppText variant="headlineSm">Settings</AppText>
        <View style={styles.spacer} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        {!ready ? <AppText variant="bodySm" color={colors.onSurfaceMuted}>Showing saved settings; changes sync when you’re online.</AppText> : null}

        <SectionTitle>Teams</SectionTitle>
        {!subs.data ? <AppText variant="body" color={colors.error}>Can’t load your teams right now. Check your connection.</AppText> : null}
        {subs.data?.map((t) => (
          <View key={t.id} style={styles.team}>
            <View style={styles.teamHead}>
              <AppText variant="headlineSm" style={styles.flex} numberOfLines={2}>{t.name ?? 'Course'}</AppText>
              {t.ended ? <Pill tone="muted" text="Ended" /> : null}
            </View>
            {t.ended ? <AppText variant="caption" color={colors.onSurfaceMuted}>The bot was removed, so no new cards will arrive. Your history and stars stay.</AppText> : null}
            <SwitchRow label="Mute alerts" detail="Cards and reminders still come; only push alerts stop" value={t.muted} onChange={(v) => void teams.setMuted(t, v)} />
            <SecondaryButton label="Leave this team" icon="logout-variant" danger onPress={() => confirmLeave(t.id)} />
          </View>
        ))}
        <SecondaryButton label="Join another team" icon="plus" disabled={busy !== null} onPress={() => router.push('/join')} />

        <SectionTitle>Notifications</SectionTitle>
        <OptionRow
          icon={allowed ? 'bell-check-outline' : 'bell-off-outline'}
          label={allowed === null ? 'Checking…' : allowed ? 'Notifications are on' : 'Notifications are off'}
          detail={allowed ? 'Reminders and date-change alerts can reach you' : action === 'ask' ? 'Tap to turn them on' : 'Tap to open your phone’s settings'}
          onPress={async () => {
            if (action === 'ask') await push.enable();
            else if (action === 'settings') await Linking.openSettings();
            perm.refresh();
          }}
        />
        {exact === 'denied' ? (
          <OptionRow icon="alarm" label="Make reminders arrive on time" detail="Allow “Alarms & reminders” for Skim, or they can be a few minutes late"
            onPress={async () => { setExact(await requestExactAlarm()); setExactTick((n) => n + 1); }} />
        ) : exact === 'granted' ? (
          <OptionRow icon="alarm-check" label="Reminders arrive on time" detail="Alarms & reminders is allowed" onPress={() => undefined} />
        ) : null}
        <SwitchRow label="Urgent only" detail="Alerts only for the kinds you marked Important" value={settings.urgent_only} onChange={(v) => void update({ urgent_only: v })} />
        <SwitchRow label="Quiet hours" detail={q.enabled ? `No alerts from ${quietWindow(q)}; they wait until morning` : 'Alerts can arrive at any hour'} value={q.enabled} onChange={(v) => void update({ quiet_hours: { enabled: v } })} />
        {q.enabled ? (
          <>
            <StepperRow label="Quiet from" value={q.start} onMinus={() => void update({ quiet_hours: { start: stepTime(q.start, -TIME_STEP) } })} onPlus={() => void update({ quiet_hours: { start: stepTime(q.start, TIME_STEP) } })} />
            <StepperRow label="Quiet until" value={q.end} onMinus={() => void update({ quiet_hours: { end: stepTime(q.end, -TIME_STEP) } })} onPlus={() => void update({ quiet_hours: { end: stepTime(q.end, TIME_STEP) } })} />
          </>
        ) : null}
        <OptionRow icon="bell-ring-outline" label="Send me a test reminder" detail={testNote ?? 'Arrives in about a minute'} disabled={!allowed}
          onPress={async () => {
            try {
              const at = await scheduleTestReminder();
              setTestNote(`Scheduled for ${formatTime(toIstIso(at.getTime())) ?? 'soon'} (India time)`);
            } catch {
              setTestNote('Couldn’t schedule the test');
            }
          }} />

        <SectionTitle>Reminders</SectionTitle>
        <SwitchRow label="Remind me automatically" detail="For the kinds you marked Important" value={r.auto_for_important} onChange={(v) => void update({ reminders: { auto_for_important: v } })} />
        <StepperRow label="Evening before" detail="The reminder the day before" value={r.day_before_time}
          onMinus={() => void update({ reminders: { day_before_time: stepTime(r.day_before_time, -TIME_STEP) } })} onPlus={() => void update({ reminders: { day_before_time: stepTime(r.day_before_time, TIME_STEP) } })} />
        <StepperRow label="Hours before" detail="The reminder on the day" value={formatHours(r.same_day_hours)}
          minusDisabled={r.same_day_hours <= HOURS_RANGE.min} plusDisabled={r.same_day_hours >= HOURS_RANGE.max}
          onMinus={() => void update({ reminders: { same_day_hours: stepHours(r.same_day_hours, -1) } })} onPlus={() => void update({ reminders: { same_day_hours: stepHours(r.same_day_hours, 1) } })} />
        <StepperRow label="Not before" detail="Earliest time for the reminder on the day" value={r.earliest}
          onMinus={() => void update({ reminders: { earliest: stepTime(r.earliest, -TIME_STEP) } })} onPlus={() => void update({ reminders: { earliest: stepTime(r.earliest, TIME_STEP) } })} />

        <SectionTitle>History</SectionTitle>
        <StepperRow label="Cards kept" detail={`Between ${HISTORY_RANGE.min} and ${HISTORY_RANGE.max}`} value={String(settings.history_limit)}
          minusDisabled={settings.history_limit <= HISTORY_CHOICES[0]} plusDisabled={settings.history_limit >= HISTORY_RANGE.max}
          onMinus={() => void update({ history_limit: stepHistory(settings.history_limit, -1) })} onPlus={() => void update({ history_limit: stepHistory(settings.history_limit, 1) })} />

        {outbox.pending > 0 ? <AppText variant="body" color={colors.onSurfaceVariant}>{outbox.pending} change{outbox.pending === 1 ? '' : 's'} waiting to sync</AppText> : null}
        {outbox.notice ? <AppText variant="bodySm" color={colors.accentSoft} onPress={outbox.dismissNotice}>{outbox.notice} (tap to dismiss)</AppText> : null}
        {push.notice ? <AppText variant="bodySm" color={colors.accentSoft} onPress={push.dismissNotice}>{push.notice} (tap to dismiss)</AppText> : null}

        <SectionTitle>Account</SectionTitle>
        <SecondaryButton label={busy === 'signout' ? 'Signing out…' : 'Sign out'} icon="logout" disabled={busy !== null}
          onPress={async () => { setBusy('signout'); try { await signOut(); } finally { setBusy(null); } }} />
        <SecondaryButton label="Delete my account" icon="delete-outline" danger disabled={busy !== null} onPress={() => { setTyped(''); setDeleteError(null); setDeleting(true); }} />
      </ScrollView>

      <Sheet visible={deleting} title="Delete your account?" onClose={() => (busy === 'delete' ? undefined : setDeleting(false))}>
        <AppText variant="body" color={colors.onSurfaceVariant}>
          This removes your teams, history, stars, reminders and devices from Skim, and your sign-in. It can’t be undone. Your professor and the Teams posts are not affected.
        </AppText>
        <AppText variant="label">Type {DELETE_WORD} to confirm</AppText>
        <TextInput accessibilityLabel={`Type ${DELETE_WORD} to confirm`} value={typed} onChangeText={(t) => { setTyped(t); setDeleteError(null); }} autoCapitalize="characters" autoCorrect={false} autoComplete="off"
          spellCheck={false} placeholder={DELETE_WORD} placeholderTextColor={colors.onSurfaceMuted} editable={busy !== 'delete'} style={styles.input} />
        {deleteError ? <AppText variant="body" color={colors.error} accessibilityLiveRegion="polite">{deleteError}</AppText> : null}
        <PrimaryButton label="Delete my account" busy={busy === 'delete'} disabled={!confirmsDelete(typed)}
          onPress={async () => {
            setBusy('delete');
            const res = await deleteAccount();
            setBusy(null);
            if (!res.ok) setDeleteError(res.message);
          }} />
      </Sheet>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: space.sm, paddingBottom: space.md },
  spacer: { width: 48 },
  flex: { flex: 1 },
  scroll: { gap: space.sm + 2, paddingBottom: space.xl * 2 },
  team: { gap: space.sm, padding: space.md, borderRadius: radius.lg, backgroundColor: solid.card, borderWidth: 1, borderColor: solid.cardBorder },
  teamHead: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  input: { height: 56, borderRadius: radius.md, borderWidth: 1, borderColor: solid.cardBorder, backgroundColor: '#323A4B', color: colors.onSurface, fontFamily: 'Manrope_700Bold', fontSize: 18, letterSpacing: 4, textAlign: 'center' },
});
