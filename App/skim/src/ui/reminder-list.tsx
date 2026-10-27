// Every reminder, soonest first, each with an on/off switch. Switched-off ones stay in the list (dimmed) so they can be turned back on.
import { router } from 'expo-router';
import { FlatList, Pressable, RefreshControl, Switch, View } from 'react-native';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { formatWhen } from '@/lib/format';
import { haptics } from '@/lib/haptics';
import type { ListedReminder } from '@/lib/reminder-plan';
import { useReminderList, useReminderSwitch } from '@/lib/reminders-list';
import { AppText } from './app-text';
import { Icon } from './icon';
import { Reveal } from './motion';
import { Empty } from './rows';
import { ErrorState, RowSkeletons } from './states';

export function reminderKindLabel(r: Pick<ListedReminder, 'kind' | 'virtual'>): string {
  if (r.virtual || r.kind.startsWith('auto_')) return 'Automatic';
  if (r.kind.startsWith('recommended_')) return 'Recommended';
  return 'Custom';
}

export function ReminderRow({ reminder, onSwitch, onOpen }: { reminder: ListedReminder; onSwitch(enabled: boolean): void; onOpen?: () => void }) {
  const styles = useStyles();
  const t = useTheme();
  const when = formatWhen(reminder.fire_at) ?? '';
  const kind = reminderKindLabel(reminder);
  return (
    <View style={[styles.row, !reminder.enabled && styles.off]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`${reminder.title}, ${when}. Open the card`} disabled={!onOpen} onPress={onOpen}
        style={({ pressed }) => [styles.main, pressed && { opacity: 0.6 }]}>
        <Icon name={reminder.enabled ? 'bell-ring-outline' : 'bell-off-outline'} size={22} color={reminder.enabled ? t.primary : t.textSecondary} />
        <View style={styles.flex}>
          <AppText variant="label" numberOfLines={2} color={reminder.enabled ? t.textPrimary : t.textSecondary}>{reminder.title}</AppText>
          <AppText variant="caption" color={t.textSecondary} numberOfLines={1}>{[when, kind, reminder.enabled ? null : 'Off'].filter(Boolean).join(' · ')}</AppText>
        </View>
      </Pressable>
      <Switch accessibilityLabel={`${reminder.title}, ${when}, ${kind}`} value={reminder.enabled}
        onValueChange={(v) => { haptics.selection(); onSwitch(v); }} trackColor={{ false: t.border, true: t.switchOn }} thumbColor={reminder.enabled ? t.primary : t.switchThumbOff} />
    </View>
  );
}

export function ReminderList() {
  const styles = useStyles();
  const t = useTheme();
  const list = useReminderList();
  const setEnabled = useReminderSwitch();
  const failed = list.isError && !list.data;
  if (list.isPending && !list.data) return <RowSkeletons />;
  return (
    <FlatList
      data={list.reminders}
      keyExtractor={(r) => r.id}
      contentContainerStyle={list.reminders.length ? styles.list : styles.fill}
      ItemSeparatorComponent={() => <View style={styles.gap} />}
      refreshControl={<RefreshControl refreshing={list.isRefetching} onRefresh={() => void list.refetch()} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}
      ListEmptyComponent={failed
        ? <ErrorState title="Can’t load your reminders" text="Check your connection, then try again." onRetry={() => void list.refetch()} />
        : <Empty icon="bell-outline" title="No reminders yet" text="Tap Remind me on a card, or tick event kinds in Important to get automatic ones." />}
      renderItem={({ item, index }) => (
        <Reveal scope="reminders" index={index}>
          <ReminderRow reminder={item} onSwitch={(v) => void setEnabled(item.id, v)}
            onOpen={item.item_id ? () => router.push({ pathname: '/item/[id]', params: { id: String(item.item_id) } }) : undefined} />
        </Reveal>
      )}
    />
  );
}

const useStyles = makeStyles((t) => ({
  list: { paddingBottom: space.md },
  fill: { flexGrow: 1 },
  gap: { height: space.sm + 2 },
  flex: { flex: 1, gap: 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingLeft: space.md, paddingRight: space.sm, paddingVertical: space.sm, minHeight: 64, borderRadius: radius.lg, backgroundColor: t.surfaceRaised, borderWidth: 1, borderColor: t.border },
  off: { backgroundColor: t.surface },
  main: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: space.md, minHeight: 56 },
}));
