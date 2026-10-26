// Design.md: the page is never flat. A slate base, a top vignette and 2-3 oversized colour blooms that the glass surfaces sit on.
// Blooms are radial gradients (no blur filter, which Android only supports from API 31), so they cost nothing while cards move.
import { StyleSheet, View } from 'react-native';

import { colors } from '@/constants/skim-theme';

const bloom = (rgb: string, alpha: number) => `radial-gradient(circle, rgba(${rgb},${alpha}) 0%, rgba(${rgb},0) 70%)`;

export function Atmosphere() {
  return (
    <View pointerEvents="none" style={styles.fill}>
      <View style={[styles.blob, { top: -160, right: -140, width: 420, height: 420, backgroundImage: bloom('37,99,235', 0.3) }]} />
      <View style={[styles.blob, { top: 260, left: -220, width: 460, height: 460, backgroundImage: bloom('6,182,212', 0.2) }]} />
      <View style={[styles.blob, { bottom: -180, right: -160, width: 440, height: 440, backgroundImage: bloom('99,102,241', 0.22) }]} />
      <View style={[styles.fill, { backgroundImage: 'linear-gradient(to bottom, rgba(30,41,59,0.85), rgba(15,23,42,0) 38%)' }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.surface },
  blob: { position: 'absolute', borderRadius: 9999 },
});
