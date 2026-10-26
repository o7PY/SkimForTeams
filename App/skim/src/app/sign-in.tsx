import { useState } from 'react';
import { View } from 'react-native';

import { makeStyles, space, useTheme } from '@/constants/theme';
import { useAuth } from '@/lib/auth';
import { AppText } from '@/ui/app-text';
import { PrimaryButton } from '@/ui/buttons';
import { Screen } from '@/ui/screen';

export default function SignIn() {
  const styles = useStyles();
  const t = useTheme();
  const { signInWithGoogle } = useAuth();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setMessage(null);
    const r = await signInWithGoogle();
    if (!r.ok && !r.cancelled) setMessage(r.message ?? 'Sign-in failed.');
    setBusy(false); // on success the auth guard swaps this screen out
  }

  return (
    <Screen>
      <View style={styles.hero}>
        <AppText variant="display">Skim</AppText>
        <AppText variant="headlineSm" color={t.textSecondary} style={styles.tag}>Everything that matters in your course, as a deck of cards.</AppText>
      </View>
      <View style={styles.actions}>
        {message ? <AppText variant="body" color={t.warning} accessibilityLiveRegion="polite">{message}</AppText> : null}
        <PrimaryButton label="Continue with Google" icon="google" busy={busy} onPress={go} />
      </View>
    </Screen>
  );
}

const useStyles = makeStyles((t) => ({
  hero: { flex: 1, justifyContent: 'center', gap: space.md },
  tag: { fontFamily: 'Manrope_500Medium', letterSpacing: 0 },
  actions: { gap: space.md, paddingBottom: space.md },
}));
