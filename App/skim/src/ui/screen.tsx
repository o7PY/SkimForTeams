import type { ReactNode } from 'react';
import { StyleSheet, View, type ViewStyle } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, space } from '@/constants/skim-theme';
import { Atmosphere } from './atmosphere';

/** Every screen: the atmospheric field behind, content inside the safe area with the 24px gutter. */
export function Screen({ children, style, gutter = true }: { children: ReactNode; style?: ViewStyle; gutter?: boolean }) {
  return (
    <View style={styles.root}>
      <Atmosphere />
      <SafeAreaView style={[styles.safe, gutter && styles.gutter, style]}>{children}</SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  safe: { flex: 1 },
  gutter: { paddingHorizontal: space.gutter },
});
