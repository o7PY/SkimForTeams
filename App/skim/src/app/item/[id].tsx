// A card opened by swiping right (PHASE4.md 7.2 item 4): the full content, the history of its events, the earlier posts it replaces,
// and every action. Right-swiping already recorded "open", so closing needs nothing more (P4-DIFF-009).
import { useQuery } from '@tanstack/react-query';
import { router, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, View } from 'react-native';

import { colors, radius, solid, space } from '@/constants/skim-theme';
import {
  absolutePresets, calendarEventFor, composeAbsolute, defaultPickerTime, fileUrl, offsetPresets, safeUrl, snoozeOptions, starLabel, tempStarOptions, upcomingEvent, type TempStar,
} from '@/lib/actions';
import { useAuth } from '@/lib/auth';
import { changeSummary, formatWhen, relativeTime } from '@/lib/format';
import { isPending, recommendedAlreadySet } from '@/lib/item-cache';
import { useItemActions, type ActionResult } from '@/lib/item-actions';
import { itemOptions } from '@/lib/query-options';
import type { Card } from '@/lib/types';
import { AppText } from '@/ui/app-text';
import { IconButton, PrimaryButton, SecondaryButton } from '@/ui/buttons';
import { DeckCard } from '@/ui/deck-card';
import { Icon } from '@/ui/icon';
import { Screen } from '@/ui/screen';
import { OptionRow, SectionLabel, Sheet } from '@/ui/sheet';
import { TimePicker, type PickerValue } from '@/ui/time-picker';

type SheetName = 'remind' | 'star' | 'snooze' | null;

export default function Item() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { api } = useAuth();
  const q = useQuery({ ...itemOptions(api, id ?? ''), enabled: /^\d+$/.test(id ?? '') });
  const card = q.data;
  const actions = useItemActions(card);
  const [sheet, setSheet] = useState<SheetName>(null);
  const [toast, setToast] = useState<{ text: string; bad: boolean } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => void (toastTimer.current && clearTimeout(toastTimer.current)), []);

  const say = useCallback((r: ActionResult) => {
    setToast({ text: r.message, bad: !r.ok });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  }, []);
  const run = useCallback(async (p: Promise<ActionResult> | undefined) => (p ? say(await p) : undefined), [say]);

  const close = () => (router.canGoBack() ? router.back() : router.replace('/'));
  const now = useMemo(() => new Date(), [sheet]); // eslint-disable-line react-hooks/exhaustive-deps -- re-read the clock whenever a sheet opens

  if (!card || !actions) {
    return (
      <Screen>
        <View style={styles.top}><IconButton icon="close" label="Close" onPress={close} /></View>
        <View style={styles.center}>
          {q.isPending && !q.data ? <ActivityIndicator color={colors.primary} /> : <AppText variant="body" color={colors.onSurfaceVariant}>This card isn’t available.</AppText>}
        </View>
      </Screen>
    );
  }

  const event = upcomingEvent(card, now);
  const dated = !!event;
  const rec = card.recommended_reminders ?? [];
  const recTimes = rec.filter((r) => r.fire_at).map((r) => formatWhen(r.fire_at)).filter(Boolean).join(' and ');
  const teams = safeUrl(card.links.teams_deep_link);
  const file = safeUrl(fileUrl(card));
  const canCalendar = !!calendarEventFor(card, now);

  return (
    <Screen>
      <View style={styles.top}>
        <AppText variant="headlineSm">{card.kind === 'change' ? 'Date change' : card.kind === 'document' ? 'File' : 'Post'}</AppText>
        <IconButton icon="close" label="Close" onPress={close} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        <View style={styles.cardWrap}><DeckCard card={card} detail now={now} /></View>

        <View style={styles.actions}>
          {dated && rec.length > 0 && !recommendedAlreadySet(card) ? (
            <PrimaryButton label="Recommended reminders" icon="bell-plus-outline" onPress={() => run(actions.addRecommended())} />
          ) : null}
          {dated && rec.length > 0 && !recommendedAlreadySet(card) ? <AppText variant="caption" color={colors.onSurfaceMuted} style={styles.hint}>{recTimes}</AppText> : null}

          <OptionRow icon="bell-outline" label="Remind me…" detail="Before the event, or at a time you pick" onPress={() => setSheet('remind')} />
          {card.user.starred
            ? <OptionRow icon="star" label={starLabel(card)} detail="Tap to remove the star" onPress={() => run(actions.unstar())} selected />
            : <OptionRow icon="star-outline" label="Star" detail="Keep it in Starred" onPress={() => run(actions.star(null))} />}
          <OptionRow icon="timer-star-outline" label="Temporary star…" detail="Starred for a while" onPress={() => setSheet('star')} />
          <OptionRow icon="clock-outline" label="Snooze…" detail="Bring it back later" onPress={() => setSheet('snooze')} />
          {canCalendar ? <OptionRow icon="calendar-plus" label="Add to calendar" onPress={() => run(actions.addToCalendar().then((r) => r.result))} /> : null}
          {teams ? <OptionRow icon="microsoft-teams" label="Open in Teams" detail="The original message" onPress={() => run(actions.openTeams())} /> : null}
          {file ? <OptionRow icon="file-outline" label="Open file" onPress={() => run(actions.openFile())} /> : null}
        </View>

        {card.user.reminders.length > 0 ? (
          <View style={styles.section}>
            <SectionLabel text="Your reminders" />
            {card.user.reminders.map((r) => (
              <View key={r.id} style={styles.line}>
                <Icon name="bell-ring-outline" size={18} color={colors.accentSoft} />
                <AppText variant="body" style={styles.flex}>{formatWhen(r.fire_at) ?? 'Time pending'}{r.virtual ? ' · automatic' : ''}{isPending(r) ? ' · syncing' : ''}</AppText>
                {isPending(r) ? null : <IconButton icon="close" label="Remove reminder" size={36} onPress={() => run(actions.removeReminder(r.id))} />}
              </View>
            ))}
          </View>
        ) : null}

        {(card.event_history ?? []).some((h) => h.changes.length > 0) ? (
          <View style={styles.section}>
            <SectionLabel text="What changed" />
            {(card.event_history ?? []).flatMap((h) => h.changes.map((c) => ({ ...c, event: card.events.find((e) => e.id === h.event_id) }))).filter((c) => !['created', 'restated', 'needs_review'].includes(c.type))
              .map((c) => {
                const s = changeSummary({ type: c.type, before: c.before, after: c.after });
                return (
                  <View key={c.id} style={styles.history}>
                    <AppText variant="labelSm" color={colors.accentSoft}>{c.event?.title ?? 'Event'} · {s.label}</AppText>
                    {s.from || s.to ? <AppText variant="body">{[s.from, s.to].filter(Boolean).join('  →  ')}</AppText> : null}
                    <AppText variant="caption" color={colors.onSurfaceMuted}>{relativeTime(c.at, now)}</AppText>
                  </View>
                );
              })}
          </View>
        ) : null}

        {(card.folded ?? []).length > 0 ? (
          <View style={styles.section}>
            <SectionLabel text="Earlier posts this replaces" />
            {(card.folded ?? []).map((f) => <View key={f.id} style={styles.cardWrap}><DeckCard card={f} detail now={now} /></View>)}
          </View>
        ) : null}
      </ScrollView>

      {toast ? (
        <View accessibilityLiveRegion="polite" style={[styles.toast, toast.bad && styles.toastBad]}>
          <AppText variant="label" color={toast.bad ? '#FCA5A5' : colors.onSurface}>{toast.text}</AppText>
        </View>
      ) : null}

      <RemindSheet visible={sheet === 'remind'} card={card} now={now} onClose={() => setSheet(null)}
        onBefore={(m) => { setSheet(null); void run(actions.remindBefore(m)); }} onAt={(iso) => { setSheet(null); void run(actions.remindAt(iso)); }} />
      <TempStarSheet visible={sheet === 'star'} card={card} now={now} onClose={() => setSheet(null)} onPick={(t) => { setSheet(null); void run(actions.star(t)); }} />
      <SnoozeSheet visible={sheet === 'snooze'} card={card} now={now} onClose={() => setSheet(null)} onPick={(until, label) => { setSheet(null); void run(actions.snooze(until, label)); }} />
    </Screen>
  );
}

function RemindSheet({ visible, card, now, onClose, onBefore, onAt }: { visible: boolean; card: Card; now: Date; onClose(): void; onBefore(m: number): void; onAt(iso: string): void }) {
  const { presets } = offsetPresets(card, now);
  const [custom, setCustom] = useState(false);
  const [value, setValue] = useState<PickerValue>(() => defaultPickerTime(new Date()));
  const chosen = composeAbsolute(now, value.dayOffset, value.hh, value.mm);
  return (
    <Sheet visible={visible} title="Remind me" onClose={() => { setCustom(false); onClose(); }}>
      {presets.length > 0 ? <SectionLabel text="BEFORE THE EVENT" /> : null}
      {presets.map((p) => <OptionRow key={p.minutes} icon="bell-outline" label={p.label} detail={p.fires} onPress={() => onBefore(p.minutes)} />)}
      <SectionLabel text="AT A TIME" />
      {absolutePresets(now).map((p) => <OptionRow key={p.id} icon="clock-outline" label={p.label} detail={p.detail} onPress={() => onAt(p.at)} />)}
      {custom ? (
        <View style={styles.custom}>
          <TimePicker now={now} value={value} onChange={setValue} />
          <PrimaryButton label={chosen ? `Remind me ${formatWhen(chosen.at)}` : 'Pick a time in the future'} disabled={!chosen} onPress={() => chosen && onAt(chosen.at)} />
        </View>
      ) : (
        <SecondaryButton label="Pick a date and time…" icon="calendar-clock-outline" onPress={() => { setValue(defaultPickerTime(new Date())); setCustom(true); }} />
      )}
    </Sheet>
  );
}

function TempStarSheet({ visible, card, now, onClose, onPick }: { visible: boolean; card: Card; now: Date; onClose(): void; onPick(t: TempStar): void }) {
  return (
    <Sheet visible={visible} title="Star for how long?" onClose={onClose}>
      {tempStarOptions(card, now).map((o) => <OptionRow key={o.id} icon="timer-star-outline" label={o.label} detail={o.detail} onPress={() => onPick(o.id)} />)}
    </Sheet>
  );
}

function SnoozeSheet({ visible, card, now, onClose, onPick }: { visible: boolean; card: Card; now: Date; onClose(): void; onPick(until: string, label: string): void }) {
  return (
    <Sheet visible={visible} title="Snooze until" onClose={onClose}>
      {snoozeOptions(now, card).map((o) => <OptionRow key={o.id} icon="clock-outline" label={o.label} detail={o.detail} onPress={() => onPick(o.until, o.detail ? `${o.label.toLowerCase()} (${o.detail})` : o.label.toLowerCase())} />)}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: space.sm, paddingBottom: space.md },
  scroll: { paddingBottom: space.xl * 2, gap: space.lg },
  cardWrap: { minHeight: 120 },
  actions: { gap: space.sm },
  hint: { textAlign: 'center' },
  section: { gap: space.sm },
  line: { flexDirection: 'row', alignItems: 'center', gap: space.sm, minHeight: 44 },
  flex: { flex: 1 },
  history: { backgroundColor: solid.card, borderColor: solid.cardBorder, borderWidth: 1, borderRadius: radius.md, padding: space.md, gap: 4 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  custom: { gap: space.md, paddingTop: space.sm },
  toast: { position: 'absolute', left: 0, right: 0, bottom: space.lg, alignItems: 'center', paddingVertical: 14, borderRadius: radius.md, backgroundColor: solid.block, borderWidth: 1, borderColor: colors.borderAccent },
  toastBad: { backgroundColor: '#4A2330', borderColor: '#EF4444' },
});
