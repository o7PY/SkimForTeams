import type { ReactNode } from 'react';
import { View } from 'react-native';

import { makeStyles, space, useTheme } from '@/constants/theme';
import { router } from 'expo-router';
import { AppText } from './app-text';
import { IconButton } from './buttons';
import { Screen } from './screen';
import { OfflineBanner } from './states';

/** Room under the content for the floating tab bar (64 high + 16 inset + 16 breathing space). */
export const TAB_BAR_ROOM = 96;

/** A tab's frame: title on the left, the settings gear on the right, and the bottom room the floating tab bar needs. */
export function TabScreen({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  const styles = useStyles();
  const t = useTheme();
  return (
    <Screen>
      <View style={styles.header}>
        <View style={styles.flex}>
          <AppText variant="headlineLg">{title}</AppText>
          {subtitle ? <AppText variant="caption" color={t.textSecondary}>{subtitle}</AppText> : null}
        </View>
        <IconButton icon="cog-outline" label="Settings" onPress={() => router.push('/settings')} />
      </View>
      <OfflineBanner />
      <View style={styles.body}>{children}</View>
    </Screen>
  );
}

const useStyles = makeStyles((t) => ({
  header: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingTop: space.sm, paddingBottom: space.md },
  flex: { flex: 1 },
  body: { flex: 1, paddingBottom: TAB_BAR_ROOM },
}));
