// Teams: every course the student has joined; tap one to see all of its cards (app/team/[id].tsx).
import { router } from 'expo-router';
import { FlatList, Pressable, RefreshControl, View } from 'react-native';

import { makeStyles, radius, space, teamAccent, useTheme } from '@/constants/theme';
import { useSubscriptions } from '@/lib/queries';
import { AppText } from '@/ui/app-text';
import { PrimaryButton } from '@/ui/buttons';
import { Icon } from '@/ui/icon';
import { Reveal } from '@/ui/motion';
import { Empty } from '@/ui/rows';
import { ErrorState, RowSkeletons } from '@/ui/states';
import { TabScreen } from '@/ui/tab-screen';
import { Pill } from '@/ui/deck-card';

export default function Teams() {
  const styles = useStyles();
  const t = useTheme();
  const subs = useSubscriptions();
  const teams = subs.data ?? [];
  const failed = subs.isError && !subs.data;

  return (
    <TabScreen title="Teams" subtitle={teams.length ? `${teams.length} ${teams.length === 1 ? 'course' : 'courses'}` : undefined}>
      {subs.isPending && !subs.data ? (
        <RowSkeletons />
      ) : failed ? (
        <ErrorState title="Can’t load your teams" text="Check your connection, then try again." onRetry={() => void subs.refetch()} />
      ) : (
        <FlatList
          data={teams}
          keyExtractor={(x) => x.id}
          contentContainerStyle={teams.length ? styles.list : styles.fill}
          ItemSeparatorComponent={() => <View style={styles.gap} />}
          refreshControl={<RefreshControl refreshing={subs.isRefetching} onRefresh={() => void subs.refetch()} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Empty icon="account-group-outline" title="No teams yet" text="Join a course with the code your professor’s Skim bot posted in the class team." />
              <PrimaryButton label="Join a team" icon="plus" onPress={() => router.push('/join')} />
            </View>
          }
          renderItem={({ item, index }) => (
            <Reveal scope="teams" index={index}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${item.name ?? 'Course'}${item.ended ? ', ended' : ''}${item.muted ? ', muted' : ''}. Show its cards`}
                onPress={() => router.push({ pathname: '/team/[id]', params: { id: item.id } })}
                style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}>
                <View style={[styles.dot, { backgroundColor: teamAccent(t, item.id) }]} />
                <View style={styles.flex}>
                  <AppText variant="label" numberOfLines={2}>{item.name ?? 'Course'}</AppText>
                </View>
                {item.ended ? <Pill tone="muted" text="Ended" /> : null}
                {item.muted ? <Icon name="bell-off-outline" size={18} color={t.textSecondary} /> : null}
                <Icon name="chevron-right" size={22} color={t.textSecondary} />
              </Pressable>
            </Reveal>
          )}
        />
      )}
    </TabScreen>
  );
}

const useStyles = makeStyles((t) => ({
  list: { paddingBottom: space.md },
  fill: { flexGrow: 1 },
  gap: { height: space.sm + 2 },
  flex: { flex: 1 },
  empty: { flex: 1, justifyContent: 'center', gap: space.md },
  dot: { width: 14, height: 14, borderRadius: 7 },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: space.md, minHeight: 64, paddingHorizontal: space.md, paddingVertical: space.sm + 2,
    borderRadius: radius.lg, backgroundColor: t.surfaceRaised, borderWidth: 1, borderColor: t.border,
  },
}));
