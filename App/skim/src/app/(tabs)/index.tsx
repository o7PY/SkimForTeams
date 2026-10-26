// The deck (home). Swipe left = read, right = options (opens the card); the buttons below do the same for anyone who cannot swipe.
import { Redirect, router } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, View } from 'react-native';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { useDeck, useDeckActions } from '@/lib/deck';
import { useSubscriptions } from '@/lib/queries';
import type { Card } from '@/lib/types';
import { AppText } from '@/ui/app-text';
import { IconButton, PrimaryButton, TextButton } from '@/ui/buttons';
import { Icon } from '@/ui/icon';
import { NotificationsBanner } from '@/ui/notifications-banner';
import { TabScreen } from '@/ui/tab-screen';
import { SwipeDeck, type SwipeDeckHandle } from '@/ui/swipe-deck';

const UNDO_MS = 5000;
const PREFETCH_WHEN_FEWER_THAN = 8;

export default function DeckScreen() {
  const styles = useStyles();
  const t = useTheme();
  const subs = useSubscriptions();
  const deck = useDeck();
  const actions = useDeckActions();
  const handle = useRef<SwipeDeckHandle>(null);
  const [undoable, setUndoable] = useState<Card | null>(null);
  const [pulling, setPulling] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { cards, hasNextPage, isFetchingNextPage, fetchNextPage } = deck;
  useEffect(() => {
    if (cards.length < PREFETCH_WHEN_FEWER_THAN && hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [cards.length, hasNextPage, isFetchingNextPage, fetchNextPage]);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const onDismiss = useCallback(
    (card: Card) => {
      void actions.dismiss(card);
      setUndoable(card);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setUndoable(null), UNDO_MS);
    },
    [actions],
  );
  const onOpen = useCallback(
    (card: Card) => {
      void actions.open(card);
      setUndoable(null);
      router.push({ pathname: '/item/[id]', params: { id: String(card.id) } });
    },
    [actions],
  );
  const undo = useCallback(() => {
    if (!undoable) return;
    if (timer.current) clearTimeout(timer.current);
    void actions.undo(undoable);
    setUndoable(null);
  }, [actions, undoable]);
  const refresh = useCallback(async () => {
    setPulling(true);
    try {
      await deck.refetch();
    } finally {
      setPulling(false);
    }
  }, [deck]);

  if (subs.data && subs.data.length === 0) return <Redirect href="/join" />;
  const loading = deck.isPending && !deck.data;
  const failed = deck.isError && !deck.data;

  return (
    <TabScreen title="Skim" subtitle={cards.length > 0 ? `${cards.length}${deck.hasNextPage ? '+' : ''} to read` : undefined}>
      <NotificationsBanner />

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={pulling} onRefresh={refresh} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}>
        {loading ? (
          <View style={styles.center}><ActivityIndicator color={t.primary} /></View>
        ) : failed ? (
          <View style={styles.center}>
            <Icon name="cloud-off-outline" size={40} color={t.textSecondary} />
            <AppText variant="headlineSm">Can’t load your deck</AppText>
            <AppText variant="body" color={t.textSecondary} style={styles.centerText}>Check your connection, then pull down to try again.</AppText>
          </View>
        ) : cards.length === 0 ? (
          <View style={styles.center}>
            <Icon name="check-circle-outline" size={56} color={t.primary} />
            <AppText variant="headlineMd">All caught up</AppText>
            <AppText variant="body" color={t.textSecondary} style={styles.centerText}>New posts, files and date changes from your courses will show up here.</AppText>
          </View>
        ) : (
          <SwipeDeck cards={cards} onDismiss={onDismiss} onOpen={onOpen} handle={handle} />
        )}
      </ScrollView>

      {cards.length > 0 ? (
        <View style={styles.actions}>
          <IconButton icon="close" label="Mark as read" size={56} onPress={() => handle.current?.fling('dismiss')} />
          <PrimaryButton label="Open" icon="gesture-swipe-right" style={styles.openButton} onPress={() => handle.current?.fling('open')} />
        </View>
      ) : null}

      {undoable ? (
        <View accessibilityLiveRegion="polite" style={styles.toast}>
          <AppText variant="label" color={t.textSecondary}>Marked as read</AppText>
          <TextButton label="Undo" onPress={undo} />
        </View>
      ) : null}
    </TabScreen>
  );
}

const useStyles = makeStyles((t) => ({
  scroll: { flexGrow: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, paddingHorizontal: space.lg },
  centerText: { textAlign: 'center' },
  actions: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.md },
  openButton: { flex: 1 },
  toast: {
    position: 'absolute', left: 0, right: 0, bottom: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: space.md, paddingVertical: 12, borderRadius: radius.md, borderWidth: 1, borderColor: t.border, backgroundColor: t.surfaceRaised,
  },
}));
