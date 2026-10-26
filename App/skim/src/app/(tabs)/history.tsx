// History (PHASE4.md 7.2 item 5): the cards the student swiped, newest swipe first, each with "Put back".
import { router } from 'expo-router';
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View } from 'react-native';

import { colors, space } from '@/constants/skim-theme';
import { useListActions, useHistory } from '@/lib/lists';
import { useSettings } from '@/lib/use-settings';
import { AppText } from '@/ui/app-text';
import { CardRow, Empty } from '@/ui/rows';
import { TabScreen } from '@/ui/tab-screen';

export default function History() {
  const h = useHistory();
  const actions = useListActions();
  const { settings } = useSettings();
  const now = new Date();
  const failed = h.isError && !h.data;

  return (
    <TabScreen title="History" subtitle={`Your last ${settings.history_limit} cards`}>
      {h.isPending && !h.data ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : (
        <FlatList
          data={h.cards}
          keyExtractor={(c) => String(c.id)}
          contentContainerStyle={h.cards.length ? styles.list : styles.fill}
          ItemSeparatorComponent={() => <View style={styles.gap} />}
          refreshControl={<RefreshControl refreshing={h.isRefetching} onRefresh={() => void h.refetch()} tintColor={colors.primary} colors={[colors.primary]} progressBackgroundColor={colors.surfaceAlt} />}
          ListHeaderComponent={failed ? <AppText variant="body" color={colors.error}>Can’t load your history. Pull down to try again.</AppText> : undefined}
          ListEmptyComponent={failed ? undefined : <Empty icon="history" title="Nothing here yet" text="Cards you swipe away show up here, so you can put one back." />}
          renderItem={({ item }) => (
            <CardRow
              card={item}
              now={now}
              note={item.swiped?.state === 'opened' ? 'Opened' : 'Marked as read'}
              onOpen={() => router.push({ pathname: '/item/[id]', params: { id: String(item.id) } })}
              action={{ icon: 'undo-variant', label: 'Put back in the deck', onPress: () => void actions.putBack(item) }}
            />
          )}
        />
      )}
    </TabScreen>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { paddingBottom: space.md },
  fill: { flexGrow: 1 },
  gap: { height: space.sm + 2 },
});
