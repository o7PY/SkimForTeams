// A two-or-three way switch for a screen with more than one view (Starred / Reminders). Solid, 48 dp tall, a label on every option.
import { Pressable, View } from 'react-native';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { AppText } from './app-text';

export function Segmented<T extends string>({ options, value, onChange }: { options: readonly { id: T; label: string }[]; value: T; onChange(v: T): void }) {
  const styles = useStyles();
  const t = useTheme();
  return (
    <View accessibilityRole="tablist" style={styles.wrap}>
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable key={o.id} accessibilityRole="tab" accessibilityLabel={o.label} accessibilityState={{ selected: on }} onPress={() => onChange(o.id)}
            style={({ pressed }) => [styles.item, on && styles.on, pressed && { opacity: 0.7 }]}>
            <AppText variant="label" color={on ? t.onPrimary : t.textSecondary}>{o.label}</AppText>
          </Pressable>
        );
      })}
    </View>
  );
}

const useStyles = makeStyles((t) => ({
  wrap: { flexDirection: 'row', padding: 4, gap: 4, marginBottom: space.md, borderRadius: radius.full, backgroundColor: t.surfaceRaised, borderWidth: 1, borderColor: t.border },
  item: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radius.full },
  on: { backgroundColor: t.primary },
}));
