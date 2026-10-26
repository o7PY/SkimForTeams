// A small day + time chooser in plain React Native (a native date picker would need yet another build). Days: the next 14, India time.
import { useMemo } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';

import { dayChoices } from '@/lib/actions';
import { colors, radius, solid, space } from '@/constants/skim-theme';
import { AppText } from './app-text';
import { IconButton } from './buttons';

export type PickerValue = { dayOffset: number; hh: number; mm: number };
const STEP = 5;
const pad = (n: number) => String(n).padStart(2, '0');

export function stepHour(v: PickerValue, by: number): PickerValue {
  return { ...v, hh: (v.hh + by + 24) % 24 };
}
export function stepMinute(v: PickerValue, by: number): PickerValue {
  const total = v.mm + by * STEP;
  const mm = ((total % 60) + 60) % 60;
  return { ...v, mm: mm - (mm % STEP) };
}

export function TimePicker({ now, value, onChange }: { now: Date; value: PickerValue; onChange(v: PickerValue): void }) {
  const days = useMemo(() => dayChoices(now), [now]);
  return (
    <View style={styles.wrap}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.days}>
        {days.map((d) => (
          <Pressable key={d.offset} accessibilityRole="button" accessibilityState={{ selected: d.offset === value.dayOffset }} onPress={() => onChange({ ...value, dayOffset: d.offset })}
            style={[styles.day, d.offset === value.dayOffset && styles.dayOn]}>
            <AppText variant="labelSm" color={d.offset === value.dayOffset ? colors.primary : colors.onSurfaceVariant}>{d.label}</AppText>
          </Pressable>
        ))}
      </ScrollView>
      <View style={styles.time}>
        <Stepper label="Hour" text={pad(value.hh)} onUp={() => onChange(stepHour(value, 1))} onDown={() => onChange(stepHour(value, -1))} />
        <AppText variant="display" color={colors.onSurfaceMuted} style={styles.colon}>:</AppText>
        <Stepper label="Minute" text={pad(value.mm)} onUp={() => onChange(stepMinute(value, 1))} onDown={() => onChange(stepMinute(value, -1))} />
      </View>
    </View>
  );
}

function Stepper({ label, text, onUp, onDown }: { label: string; text: string; onUp(): void; onDown(): void }) {
  return (
    <View style={styles.stepper}>
      <IconButton icon="chevron-up" label={`${label} up`} size={44} onPress={onUp} />
      <AppText variant="display" accessibilityLabel={`${label} ${text}`}>{text}</AppText>
      <IconButton icon="chevron-down" label={`${label} down`} size={44} onPress={onDown} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: space.md },
  days: { gap: space.sm },
  day: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.md, backgroundColor: '#323A4B', borderWidth: 1, borderColor: solid.cardBorder },
  dayOn: { borderColor: colors.primary, backgroundColor: solid.block },
  time: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space.md },
  stepper: { alignItems: 'center', gap: 4 },
  colon: { marginBottom: 4 },
});
