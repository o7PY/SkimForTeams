// Rows used by the lists and by Settings. All solid surfaces; every control is at least 48 px tall.
import { Pressable, StyleSheet, Switch, View } from 'react-native';

import { colors, important as red, radius, solid, space } from '@/constants/skim-theme';
import { cardTitle, EVENT_TYPE_LABEL, importantEvent, relativeTime, starExpiryLabel } from '@/lib/format';
import type { Card } from '@/lib/types';
import { AppText } from './app-text';
import { IconButton } from './buttons';
import { Icon, type IconName } from './icon';

const KIND_ICON: Record<Card['kind'], IconName> = { message: 'message-text-outline', document: 'file-document-outline', change: 'calendar-clock-outline' };

/** One card in History or Starred: where it came from, its headline, when, and one action button on the right. */
export function CardRow({ card, onOpen, action, now = new Date(), showExpiry = false, note }: {
  card: Card; onOpen(): void; action: { icon: IconName; label: string; onPress(): void }; now?: Date; showExpiry?: boolean; note?: string;
}) {
  const hot = importantEvent(card);
  const expiry = showExpiry ? starExpiryLabel(card.user.temp_star_expires_at, now) : null;
  const when = relativeTime(card.swiped?.at ?? card.starred_at ?? card.occurred_at, now);
  return (
    <View style={[styles.cardRow, hot && styles.cardRowHot]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`${card.team.name ?? 'Course'}. ${cardTitle(card)}`} onPress={onOpen} style={({ pressed }) => [styles.rowMain, pressed && { opacity: 0.6 }]}>
        <Icon name={KIND_ICON[card.kind]} size={22} color={hot ? red.text : colors.accentSoft} />
        <View style={styles.flex}>
          <AppText variant="label" numberOfLines={2}>{cardTitle(card)}</AppText>
          <AppText variant="caption" color={colors.onSurfaceMuted} numberOfLines={1}>
            {[card.team.name, when, hot ? EVENT_TYPE_LABEL[hot.type] : null, card.message?.deleted ? 'deleted in Teams' : null].filter(Boolean).join(' · ')}
          </AppText>
          {expiry ? <AppText variant="caption" color={colors.accentSoft}>{expiry}</AppText> : null}
          {note ? <AppText variant="caption" color={colors.onSurfaceMuted}>{note}</AppText> : null}
        </View>
      </Pressable>
      <IconButton icon={action.icon} label={action.label} size={44} tone="accent" onPress={action.onPress} />
    </View>
  );
}

export function Empty({ icon, title, text }: { icon: IconName; title: string; text: string }) {
  return (
    <View style={styles.empty}>
      <Icon name={icon} size={48} color={colors.onSurfaceMuted} />
      <AppText variant="headlineSm">{title}</AppText>
      <AppText variant="body" color={colors.onSurfaceVariant} style={styles.centerText}>{text}</AppText>
    </View>
  );
}

export function SwitchRow({ label, detail, value, onChange, disabled }: { label: string; detail?: string; value: boolean; onChange(v: boolean): void; disabled?: boolean }) {
  return (
    <View style={[styles.row, disabled && { opacity: 0.5 }]}>
      <View style={styles.flex}>
        <AppText variant="label">{label}</AppText>
        {detail ? <AppText variant="caption" color={colors.onSurfaceMuted}>{detail}</AppText> : null}
      </View>
      <Switch accessibilityLabel={label} value={value} disabled={disabled} onValueChange={onChange}
        trackColor={{ false: '#4B5568', true: '#0E7490' }} thumbColor={value ? colors.primary : '#CBD5E1'} />
    </View>
  );
}

export function CheckRow({ label, detail, checked, onChange }: { label: string; detail?: string; checked: boolean; onChange(v: boolean): void }) {
  return (
    <Pressable accessibilityRole="checkbox" accessibilityLabel={label} accessibilityState={{ checked }} onPress={() => onChange(!checked)}
      style={({ pressed }) => [styles.row, checked && styles.rowOn, pressed && { opacity: 0.7 }]}>
      <Icon name={checked ? 'checkbox-marked' : 'checkbox-blank-outline'} size={26} color={checked ? colors.primary : colors.onSurfaceMuted} />
      <View style={styles.flex}>
        <AppText variant="label">{label}</AppText>
        {detail ? <AppText variant="caption" color={colors.onSurfaceMuted}>{detail}</AppText> : null}
      </View>
    </Pressable>
  );
}

/** A value with minus and plus buttons: times of day, hours, history size. */
export function StepperRow({ label, detail, value, onMinus, onPlus, minusDisabled, plusDisabled }: {
  label: string; detail?: string; value: string; onMinus(): void; onPlus(): void; minusDisabled?: boolean; plusDisabled?: boolean;
}) {
  return (
    <View style={styles.row}>
      <View style={styles.flex}>
        <AppText variant="label">{label}</AppText>
        {detail ? <AppText variant="caption" color={colors.onSurfaceMuted}>{detail}</AppText> : null}
      </View>
      <IconButton icon="minus" label={`${label}: less`} size={40} disabled={minusDisabled} onPress={onMinus} />
      <AppText variant="label" style={styles.value} accessibilityLabel={`${label}: ${value}`}>{value}</AppText>
      <IconButton icon="plus" label={`${label}: more`} size={40} disabled={plusDisabled} onPress={onPlus} />
    </View>
  );
}

export function SectionTitle({ children }: { children: string }) {
  return <AppText variant="headlineSm" style={styles.section}>{children}</AppText>;
}

const styles = StyleSheet.create({
  flex: { flex: 1, gap: 2 },
  cardRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingLeft: space.md, paddingRight: space.sm, paddingVertical: space.sm + 2, borderRadius: radius.lg, backgroundColor: solid.card, borderWidth: 1, borderColor: solid.cardBorder },
  cardRowHot: { backgroundColor: solid.cardHot, borderColor: red.border },
  rowMain: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: space.md, minHeight: 56 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, minHeight: 56, paddingHorizontal: space.md, paddingVertical: 10, borderRadius: radius.md, backgroundColor: solid.card, borderWidth: 1, borderColor: solid.cardBorder },
  rowOn: { borderColor: colors.primary, backgroundColor: solid.block },
  value: { minWidth: 64, textAlign: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.xl * 2 },
  centerText: { textAlign: 'center' },
  section: { marginTop: space.md },
});
