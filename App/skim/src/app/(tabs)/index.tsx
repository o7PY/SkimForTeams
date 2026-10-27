// The deck (home). Swipe left = dismiss, right = open the card; the buttons below do the same for anyone who cannot swipe.
import { Redirect, router } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated from 'react-native-reanimated';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { useChatEnabled } from '@/lib/chat';
import { useDeck, useDeckActions } from '@/lib/deck';
import { cardTitle } from '@/lib/format';
import { useSubscriptions } from '@/lib/queries';
import type { Card } from '@/lib/types';
import { AppText } from '@/ui/app-text';
import { IconButton, PrimaryButton, TextButton } from '@/ui/buttons';
import { ChatSheet, type ChatAnchor } from '@/ui/chat-sheet';
import { CaughtUpArt, DeckSkeleton, ErrorState } from '@/ui/states';
import { useToastMotion } from '@/ui/motion';
import { NotificationsBanner } from '@/ui/notifications-banner';
import { TabScreen } from '@/ui/tab-screen';
import { SwipeDeck, type SwipeDeckHandle } from '@/ui/swipe-deck';

const UNDO_MS = 5000;
const PREFETCH_WHEN_FEWER_THAN = 8;

export default function DeckScreen() {
  const styles = useStyles();
  const t = useTheme();
  const toastMotion = useToastMotion();
  const chatOn = useChatEnabled();
  const [chat, setChat] = useState<{ open: boolean; anchor: ChatAnchor | null }>({ open: false, anchor: null });
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
    <TabScreen
      title="Skim"
      subtitle={cards.length > 0 ? `${cards.length}${deck.hasNextPage ? '+' : ''} to read` : undefined}
      headerRight={chatOn ? <IconButton icon="message-question-outline" label="Ask about your courses" onPress={() => setChat({ open: true, anchor: null })} /> : undefined}>
      <NotificationsBanner />

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={pulling} onRefresh={refresh} tintColor={t.primary} colors={[t.primary]} progressBackgroundColor={t.surfaceRaised} />}>
        {loading ? (
          <DeckSkeleton />
        ) : failed ? (
          <ErrorState title="Can’t load your deck" text="Check your connection, then try again." onRetry={() => void deck.refetch()} />
        ) : cards.length === 0 ? (
          <View style={styles.center}>
            <CaughtUpArt />
            <AppText variant="headlineMd">All caught up</AppText>
            <AppText variant="body" color={t.textSecondary} style={styles.centerText}>New posts, files and date changes from your courses will show up here.</AppText>
          </View>
        ) : (
          <SwipeDeck cards={cards} onDismiss={onDismiss} onOpen={onOpen} handle={handle}
            onAsk={chatOn ? (card) => setChat({ open: true, anchor: { id: card.id, title: cardTitle(card) } }) : undefined} />
        )}
      </ScrollView>

      {cards.length > 0 ? (
        <View style={styles.actions}>
          <IconButton icon="close" label="Dismiss" size={56} onPress={() => handle.current?.fling('dismiss')} />
          <PrimaryButton label="Open" icon="gesture-swipe-right" style={styles.openButton} onPress={() => handle.current?.fling('open')} />
        </View>
      ) : null}

      {undoable ? (
        <Animated.View accessibilityLiveRegion="polite" entering={toastMotion.entering} exiting={toastMotion.exiting} style={styles.toast}>
          <AppText variant="label" color={t.textSecondary}>Dismissed</AppText>
          <TextButton label="Undo" onPress={undo} />
        </Animated.View>
      ) : null}
      {chatOn ? <ChatSheet visible={chat.open} anchor={chat.anchor} onClose={() => setChat((c) => ({ ...c, open: false }))} /> : null}
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
