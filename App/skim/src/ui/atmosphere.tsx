// The page is never flat: a base, a soft top vignette and 2-3 oversized colour blooms (calm: the vivid colour lives in chips and dots).
// Blooms are radial gradients (no blur filter, which Android only supports from API 31), so they cost nothing while cards move.
import { StyleSheet, View } from 'react-native';

import { useTheme } from '@/constants/theme';

const bloom = (colour: string) => `radial-gradient(circle, ${colour} 0%, transparent 70%)`;

export function Atmosphere() {
  const t = useTheme();
  return (
    <View pointerEvents="none" style={[styles.fill, { backgroundColor: t.background }]}>
      <View style={[styles.blob, { top: -160, right: -140, width: 420, height: 420, backgroundImage: bloom(t.bloom.a) }]} />
      <View style={[styles.blob, { top: 260, left: -220, width: 460, height: 460, backgroundImage: bloom(t.bloom.b) }]} />
      <View style={[styles.blob, { bottom: -180, right: -160, width: 440, height: 440, backgroundImage: bloom(t.bloom.c) }]} />
      <View style={[styles.fill, { backgroundImage: `linear-gradient(to bottom, ${t.bloom.top}, transparent 38%)` }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
  blob: { position: 'absolute', borderRadius: 9999 },
});
