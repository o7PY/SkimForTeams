import { Linking, View } from 'react-native';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { BANNER_TEXT, permissionAction } from '@/lib/notif-permission';
import { usePush } from '@/lib/push-runtime';
import { useNotificationPermission } from '@/lib/use-permission';
import { AppText } from './app-text';
import { PrimaryButton } from './buttons';
import { Icon } from './icon';

/** Shown on the deck while notifications are off. Asks (or points to the phone's settings) only when the student presses the button. */
export function NotificationsBanner() {
  const styles = useStyles();
  const t = useTheme();
  const { state, refresh } = useNotificationPermission();
  const push = usePush();
  const action = permissionAction(state);
  if (action === 'none') return null;
  const text = BANNER_TEXT[action];
  return (
    <View style={styles.box}>
      <View style={styles.head}>
        <Icon name="bell-outline" size={22} color={t.primary} />
        <AppText variant="label" style={styles.flex}>{text.title}</AppText>
      </View>
      <AppText variant="bodySm" color={t.textSecondary}>{text.body}</AppText>
      <PrimaryButton
        label={text.button}
        onPress={async () => {
          if (action === 'ask') await push.enable();
          else await Linking.openSettings();
          refresh();
        }}
      />
    </View>
  );
}

const useStyles = makeStyles((t) => ({
  box: { gap: space.sm + 2, padding: space.md, marginBottom: space.md, borderRadius: radius.lg, borderWidth: 1, borderColor: t.primaryBorder, backgroundColor: t.primarySoft },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  flex: { flex: 1 },
}));
