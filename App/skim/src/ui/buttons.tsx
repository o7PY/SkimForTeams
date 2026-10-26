// Design.md buttons: a gradient pill for the one primary action, glass for everything else. Style arrays are flattened before they reach
// a Link/Slot child (expo-router refuses arrays there).
import { ActivityIndicator, Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';

import { colors, radius, solid, space } from '@/constants/skim-theme';
import { AppText } from './app-text';
import { Icon, type IconName } from './icon';

type Common = { onPress?: () => void; disabled?: boolean; label: string; style?: StyleProp<ViewStyle> };

export function PrimaryButton({ onPress, disabled, busy, label, icon, style }: Common & { busy?: boolean; icon?: IconName }) {
  const off = disabled || busy;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!off, busy: !!busy }} disabled={off} onPress={onPress}
      style={({ pressed }) => [styles.primaryWrap, (pressed || off) && styles.dim, pressed && styles.press, style]}>
      <View style={styles.primary}>
        {busy ? <ActivityIndicator color={colors.onSurface} /> : (
          <View style={styles.row}>
            {icon ? <Icon name={icon} size={20} /> : null}
            <AppText variant="label">{label}</AppText>
          </View>
        )}
      </View>
    </Pressable>
  );
}

export function SecondaryButton({ onPress, disabled, label, icon, style, danger }: Common & { icon?: IconName; danger?: boolean }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled }} disabled={disabled} onPress={onPress}
      style={({ pressed }) => [styles.secondary, (pressed || disabled) && styles.dim, pressed && styles.press, style]}>
      <View style={styles.row}>
        {icon ? <Icon name={icon} size={20} color={danger ? '#FCA5A5' : colors.onSurfaceVariant} /> : null}
        <AppText variant="label" color={danger ? '#FCA5A5' : colors.onSurfaceVariant}>{label}</AppText>
      </View>
    </Pressable>
  );
}

export function IconButton({ onPress, disabled, label, icon, size = 48, tone = 'glass', style }: Common & { icon: IconName; size?: number; tone?: 'glass' | 'accent' }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: !!disabled }} disabled={disabled} onPress={onPress} hitSlop={6}
      style={({ pressed }) => [styles.icon, { width: size, height: size }, tone === 'accent' && styles.iconAccent, (pressed || disabled) && styles.dim, pressed && styles.press, style]}>
      <Icon name={icon} size={Math.round(size * 0.5)} color={tone === 'accent' ? colors.primary : colors.onSurface} />
    </Pressable>
  );
}

export function TextButton({ onPress, label, disabled }: Common) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={disabled} onPress={onPress} hitSlop={8} style={({ pressed }) => [styles.text, (pressed || disabled) && styles.dim]}>
      <AppText variant="labelSm" color={colors.primary}>{label}</AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  dim: { opacity: 0.55 },
  press: { transform: [{ scale: 0.96 }] },
  primaryWrap: { height: 56, borderRadius: radius.full, overflow: 'hidden', boxShadow: '0 4px 15px rgba(6,182,212,0.40)' },
  // The design's cyan -> blue gradient, drawn by React Native itself (no native module, so no new build). The solid colour is the fallback.
  primary: { flex: 1, paddingHorizontal: space.lg, alignItems: 'center', justifyContent: 'center', backgroundColor: '#2563EB', backgroundImage: 'linear-gradient(135deg, #22D3EE 0%, #2563EB 100%)' },
  secondary: { height: 56, paddingHorizontal: space.md, borderRadius: radius.md, borderWidth: 1, borderColor: solid.cardBorder, backgroundColor: solid.card, alignItems: 'center', justifyContent: 'center' },
  icon: { borderRadius: radius.full, borderWidth: 1, borderColor: solid.cardBorder, backgroundColor: solid.card, alignItems: 'center', justifyContent: 'center' },
  iconAccent: { borderColor: colors.borderAccent, backgroundColor: solid.block },
  text: { paddingHorizontal: 4, paddingVertical: 4, borderRadius: radius.sm },
});
