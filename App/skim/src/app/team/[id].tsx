// One team's cards (opened from the Teams tab): every visible card of the course, newest first, paged as you scroll.
import { router, useLocalSearchParams } from 'expo-router';
import { FlatList, RefreshControl, View } from 'react-native';

import { makeStyles, space, useTheme } from '@/constants/theme';
import { useSubscriptions, useTeamCards } from '@/lib/queries';
import { AppText } from '@/ui/app-text';
import { IconButton } from '@/ui/buttons';
import { Reveal } from '@/ui/motion';
import { CardRow, Empty } from '@/ui/rows';
import { Screen } from '@/ui/screen';
import { ErrorState, OfflineBanner, RowSkeletons } from '@/ui/states';

export default function TeamCards() {
  const styles = useStyles();
  const t = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const teamId = id ?? '';
  const subs = useSubscriptions();
  const q = useTeamCards(teamId);
  const name = subs.data?.find((x) => x.id === teamId)?.name ?? 'Course';
  const now = new Date();
  const failed = q.isError && !q.data;
  const back = () => (router.canGoBack() ? router.back() : router.replace('/'));

  return (
    <Screen>
      <View style={styles.header}>
        <IconButton icon="arrow-left" label="Back" onPress={back} />
        <View style={styles.flex}>
          <AppText variant="headlineSm" numberOfLines={2}>{name}</AppText>
          {q.cards.length ? <AppText variant="caption" color={t.textSecondary}>{`${q.cards.length}${q.hasNextPage ? '+' : ''} cards`}</AppText> : null}
        </View>
      </View>
      <OfflineBanner />
      {q.isPending && !q.data ? (
        <RowSkeletons />
      ) : failed ? (
        <ErrorState title="Can’t load this team’s cards" text="Check your connection, then try again." onRetry={() => void q.refetch()} />
      ) : (
        <FlatList
          data={q.cards}
          keyExtractor={(c) => String(c.id)}
          contentContainerStyle={q.cards.length ? styles.list : styles.fill}
          ItemSeparatorComponent={() => <View style={styles.gap} />}
          onEndReachedThreshold={0.6}
          onEndReached={() => {
            if (q.hasNextPage && !q.isFetchingNextPage) void q.fetchNextPage();
          }}
          refreshControl={<RefreshControl refreshing={q.isRefetching && !q.isFetchingNextPage} onRefresh={() => void q.refetch()} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}
          ListEmptyComponent={<Empty icon="message-text-outline" title="No cards yet" text="Posts, files and date changes from this course will show up here." />}
          renderItem={({ item, index }) => (
            <Reveal scope={`team-${teamId}`} index={index}>
              <CardRow
                card={item}
                now={now}
                onOpen={() => router.push({ pathname: '/item/[id]', params: { id: String(item.id) } })}
                action={{ icon: 'chevron-right', label: 'Open', onPress: () => router.push({ pathname: '/item/[id]', params: { id: String(item.id) } }) }}
              />
            </Reveal>
          )}
        />
      )}
    </Screen>
  );
}

const useStyles = makeStyles(() => ({
  header: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingTop: space.sm, paddingBottom: space.md },
  flex: { flex: 1 },
  list: { paddingBottom: space.lg },
  fill: { flexGrow: 1 },
  gap: { height: space.sm + 2 },
}));
