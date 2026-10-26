// Buttons: one solid primary pill for the one primary action, raised surfaces for everything else. Style arrays are flattened before
// they reach a Link/Slot child (expo-router refuses arrays there).
import { ActivityIndicator, Pressable, View, type StyleProp, type ViewStyle } from 'react-native';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { AppText } from './app-text';
import { Icon, type IconName } from './icon';

type Common = { onPress?: () => void; disabled?: boolean; label: string; style?: StyleProp<ViewStyle> };

export function PrimaryButton({ onPress, disabled, busy, label, icon, style }: Common & { busy?: boolean; icon?: IconName }) {
  const t = useTheme();
  const styles = useStyles();
  const off = disabled || busy;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!off, busy: !!busy }} disabled={off} onPress={onPress}
      style={({ pressed }) => [styles.primaryWrap, (pressed || off) && styles.dim, pressed && styles.press, style]}>
      <View style={styles.primary}>
        {busy ? <ActivityIndicator color={t.onPrimary} /> : (
          <View style={styles.row}>
            {icon ? <Icon name={icon} size={20} color={t.onPrimary} /> : null}
            <AppText variant="label" color={t.onPrimary}>{label}</AppText>
          </View>
        )}
      </View>
    </Pressable>
  );
}

export function SecondaryButton({ onPress, disabled, label, icon, style, danger }: Common & { icon?: IconName; danger?: boolean }) {
  const t = useTheme();
  const styles = useStyles();
  const tone = danger ? t.warning : t.textSecondary; // red is reserved for "important": a destructive action is amber with a label
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled }} disabled={disabled} onPress={onPress}
      style={({ pressed }) => [styles.secondary, (pressed || disabled) && styles.dim, pressed && styles.press, style]}>
      <View style={styles.row}>
        {icon ? <Icon name={icon} size={20} color={tone} /> : null}
        <AppText variant="label" color={tone}>{label}</AppText>
      </View>
    </Pressable>
  );
}

export function IconButton({ onPress, disabled, label, icon, size = 48, tone = 'glass', style }: Common & { icon: IconName; size?: number; tone?: 'glass' | 'accent' }) {
  const t = useTheme();
  const styles = useStyles();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled }} disabled={disabled} onPress={onPress} hitSlop={6}
      style={({ pressed }) => [styles.icon, { width: size, height: size }, tone === 'accent' && styles.iconAccent, (pressed || disabled) && styles.dim, pressed && styles.press, style]}>
      <Icon name={icon} size={Math.round(size * 0.5)} color={tone === 'accent' ? t.primary : t.textPrimary} />
    </Pressable>
  );
}

export function TextButton({ onPress, label, disabled }: Common) {
  const t = useTheme();
  const styles = useStyles();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={disabled} onPress={onPress} hitSlop={8} style={({ pressed }) => [styles.text, (pressed || disabled) && styles.dim]}>
      <AppText variant="labelSm" color={t.primary}>{label}</AppText>
    </Pressable>
  );
}

const useStyles = makeStyles((t) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  dim: { opacity: 0.55 },
  press: { transform: [{ scale: 0.96 }] },
  primaryWrap: { height: 56, borderRadius: radius.full, overflow: 'hidden', boxShadow: `0 4px 15px ${t.shadow}` },
  primary: { flex: 1, paddingHorizontal: space.lg, alignItems: 'center', justifyContent: 'center', backgroundColor: t.primary },
  secondary: { height: 56, paddingHorizontal: space.md, borderRadius: radius.md, borderWidth: 1, borderColor: t.border, backgroundColor: t.surfaceRaised, alignItems: 'center', justifyContent: 'center' },
  icon: { borderRadius: radius.full, borderWidth: 1, borderColor: t.border, backgroundColor: t.surfaceRaised, alignItems: 'center', justifyContent: 'center' },
  iconAccent: { borderColor: t.primaryBorder, backgroundColor: t.primarySoft },
  text: { paddingHorizontal: 4, paddingVertical: 4, borderRadius: radius.sm },
}));
