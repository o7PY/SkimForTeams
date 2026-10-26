// The designed states every screen shares (P5-D-109): a loading skeleton, an empty state, an offline banner and an error with a retry.
// Presentation only: they show what the screens already know. The skeleton is the one thing allowed to pulse on its own.
import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';

import { makeStyles, radius, space, tint, useTheme } from '@/constants/theme';
import { useOnline } from '@/lib/use-online';
import { AppText } from './app-text';
import { SecondaryButton } from './buttons';
import { Icon } from './icon';

function Block({ w, h, round }: { w: number | `${number}%`; h: number; round?: boolean }) {
  const t = useTheme();
  const reduced = useReducedMotion();
  const pulse = useSharedValue(1);
  useEffect(() => {
    if (!reduced) pulse.set(withRepeat(withTiming(0.55, { duration: 800 }), -1, true));
  }, [reduced, pulse]);
  const style = useAnimatedStyle(() => ({ opacity: pulse.get() }));
  return <Animated.View style={[{ width: w, height: h, borderRadius: round ? radius.full : 8, backgroundColor: t.inset }, style]} />;
}

/** A card-shaped placeholder for the deck and the detail screen. */
export function DeckSkeleton() {
  const styles = useStyles();
  return (
    <View accessible accessibilityLabel="Loading your cards" accessibilityRole="progressbar" style={styles.card}>
      <View style={styles.row}>
        <Block w={36} h={36} round />
        <View style={styles.grow}><Block w="45%" h={12} /><Block w="25%" h={10} /></View>
      </View>
      <Block w={90} h={22} />
      <Block w="90%" h={22} />
      <Block w="70%" h={22} />
      <View style={styles.gap}><Block w="100%" h={14} /><Block w="100%" h={14} /><Block w="80%" h={14} /></View>
    </View>
  );
}

/** Placeholders for History and Starred. */
export function RowSkeletons({ count = 5 }: { count?: number }) {
  const styles = useStyles();
  return (
    <View accessible accessibilityLabel="Loading" accessibilityRole="progressbar" style={styles.rows}>
      {Array.from({ length: count }, (_, i) => (
        <View key={i} style={[styles.card, styles.rowCard]}>
          <Block w={28} h={28} round />
          <View style={styles.grow}><Block w="80%" h={14} /><Block w="50%" h={10} /></View>
        </View>
      ))}
    </View>
  );
}

/** Something failed to load: say so plainly and offer the retry the screen already has. */
export function ErrorState({ title, text, onRetry }: { title: string; text: string; onRetry(): void }) {
  const t = useTheme();
  const styles = useStyles();
  return (
    <View style={styles.center}>
      <Icon name="cloud-off-outline" size={44} color={t.warning} />
      <AppText variant="headlineSm">{title}</AppText>
      <AppText variant="body" color={t.textSecondary} style={styles.text}>{text}</AppText>
      <SecondaryButton label="Try again" icon="refresh" onPress={onRetry} />
    </View>
  );
}

/** A small static picture for "All caught up": a stack of cards with a tick. */
export function CaughtUpArt() {
  const t = useTheme();
  const styles = useStyles();
  return (
    <View accessible={false} importantForAccessibility="no-hide-descendants" style={styles.art}>
      <View style={[styles.artCard, { backgroundColor: tint(t.update, t.surfaceRaised, 0.25), transform: [{ rotate: '-10deg' }] }]} />
      <View style={[styles.artCard, { backgroundColor: t.primarySoft, transform: [{ rotate: '-3deg' }] }]} />
      <View style={[styles.artCard, styles.artFront, { transform: [{ rotate: '5deg' }] }]}>
        <Icon name="check" size={36} color={t.success} />
      </View>
    </View>
  );
}

/** Shown under a screen's title while the phone has no connection. */
export function OfflineBanner() {
  const t = useTheme();
  const styles = useStyles();
  if (useOnline()) return null;
  return (
    <View accessibilityRole="alert" style={styles.banner}>
      <Icon name="wifi-off" size={20} color={t.offline} />
      <AppText variant="bodySm" color={t.textPrimary} style={styles.grow}>You’re offline. Showing saved cards; changes sync when you’re back.</AppText>
    </View>
  );
}

const useStyles = makeStyles((t) => ({
  card: { flex: 1, borderRadius: radius.lg, borderWidth: 1, borderColor: t.border, backgroundColor: t.surfaceRaised, padding: space.card, gap: space.sm + 4 },
  rowCard: { flex: 0, flexDirection: 'row', alignItems: 'center', gap: space.md, padding: space.md, borderRadius: radius.lg },
  rows: { gap: space.sm + 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.sm + 4 },
  grow: { flex: 1, gap: 6 },
  gap: { gap: 8, marginTop: 8 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, paddingHorizontal: space.lg },
  text: { textAlign: 'center' },
  art: { width: 120, height: 120, alignItems: 'center', justifyContent: 'center' },
  artCard: { position: 'absolute', width: 80, height: 100, borderRadius: radius.md },
  artFront: { backgroundColor: t.surfaceRaised, borderWidth: 1, borderColor: t.border, alignItems: 'center', justifyContent: 'center' },
  banner: { flexDirection: 'row', alignItems: 'center', gap: space.sm + 2, padding: space.md - 4, marginBottom: space.sm + 2, borderRadius: radius.md, borderWidth: 1, borderColor: t.border, backgroundColor: t.surface },
}));
