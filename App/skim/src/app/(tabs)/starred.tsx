// Starred (PHASE4.md 7.2 item 6): permanent stars, and temporary ones with how long they have left.
import { router } from 'expo-router';
import { ActivityIndicator, FlatList, RefreshControl, View } from 'react-native';

import { makeStyles, space, useTheme } from '@/constants/theme';
import { starExpiryLabel } from '@/lib/format';
import { useListActions, useStarred } from '@/lib/lists';
import { AppText } from '@/ui/app-text';
import { haptics } from '@/lib/haptics';
import { Reveal } from '@/ui/motion';
import { CardRow, Empty } from '@/ui/rows';
import { TabScreen } from '@/ui/tab-screen';

export default function Starred() {
  const styles = useStyles();
  const t = useTheme();
  const s = useStarred();
  const actions = useListActions();
  const now = new Date();
  const failed = s.isError && !s.data;
  // A temporary star the phone already knows has run out is hidden at once; the server drops it on the next refresh anyway.
  const cards = s.cards.filter((c) => starExpiryLabel(c.user.temp_star_expires_at, now) !== 'expired');

  return (
    <TabScreen title="Starred" subtitle={cards.length ? `${cards.length} saved` : undefined}>
      {s.isPending && !s.data ? (
        <View style={styles.center}><ActivityIndicator color={t.primary} /></View>
      ) : (
        <FlatList
          data={cards}
          keyExtractor={(c) => String(c.id)}
          contentContainerStyle={cards.length ? styles.list : styles.fill}
          ItemSeparatorComponent={() => <View style={styles.gap} />}
          refreshControl={<RefreshControl refreshing={s.isRefetching} onRefresh={() => void s.refetch()} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}
          ListHeaderComponent={failed ? <AppText variant="body" color={t.warning}>Can’t load your stars. Pull down to try again.</AppText> : undefined}
          ListEmptyComponent={failed ? undefined : <Empty icon="star-outline" title="No stars yet" text="Open a card and tap Star to keep it here, or star it for a day or a week." />}
          renderItem={({ item, index }) => (
            <Reveal scope="starred" index={index}>
            <CardRow
              card={item}
              now={now}
              showExpiry
              onOpen={() => router.push({ pathname: '/item/[id]', params: { id: String(item.id) } })}
              action={{ icon: 'star-off-outline', label: 'Remove star', onPress: () => { haptics.selection(); void actions.unstar(item); } }}
            />
            </Reveal>
          )}
        />
      )}
    </TabScreen>
  );
}

const useStyles = makeStyles((t) => ({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { paddingBottom: space.md },
  fill: { flexGrow: 1 },
  gap: { height: space.sm + 2 },
}));
