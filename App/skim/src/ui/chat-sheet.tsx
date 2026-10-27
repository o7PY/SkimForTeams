// "Ask the course" (PHASE6A): a chat sheet. The sheet is built with Reanimated (core Modal as the window, no native library): it slides
// up and down in 250 ms with no bounce, and fades instead when the phone asks for reduced motion. The conversation lives in this
// component's state only, so closing the sheet (which unmounts it) clears it. Colours come from the theme only.
import { router } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Keyboard, Linking, Modal, Pressable, ScrollView, TextInput, useWindowDimensions, View, type HostInstance, type LayoutChangeEvent, type ScrollViewInstance } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { makeStyles, radius, space, tint, toneOf, useTheme } from '@/constants/theme';
import { chatErrorMessage, citationAction, MAX_QUESTION, suggestions, toHistory, useAskCourse, type ChatCitation, type ChatTurn } from '@/lib/chat';
import { useOnline } from '@/lib/use-online';
import { keyboardOverlap, sheetLayout } from '@/lib/sheet-layout';
import { AppText } from './app-text';
import { IconButton } from './buttons';
import { Icon } from './icon';
import { OfflineBanner } from './states';

export type ChatAnchor = { id: number; title: string };

const SLIDE_MS = 250;
const FADE_MS = 150;
const SETTLE_MS = 150; // let the window finish any resize of its own before measuring what the keyboard still covers

export function ChatSheet({ visible, anchor, onClose }: { visible: boolean; anchor: ChatAnchor | null; onClose(): void }) {
  const styles = useStyles();
  const reduced = useReducedMotion();
  const { height } = useWindowDimensions();
  const [mounted, setMounted] = useState(visible);
  if (visible && !mounted) setMounted(true); // shown again while it was still sliding away or already gone: bring the window back
  const progress = useSharedValue(0);
  const root = useRef<HostInstance>(null);
  const keyboardTop = useRef<number | null>(null);
  const [overlap, setOverlap] = useState(0);
  const [winH, setWinH] = useState(height);

  useEffect(() => {
    const ms = reduced ? FADE_MS : SLIDE_MS;
    if (visible) {
      progress.set(withTiming(1, { duration: ms, easing: Easing.out(Easing.cubic) }));
    } else {
      const gone = (finished?: boolean) => {
        'worklet';
        if (finished) scheduleOnRN(setMounted, false);
      };
      progress.set(withTiming(0, { duration: ms, easing: Easing.in(Easing.cubic) }, gone));
    }
  }, [visible, reduced, progress]);

  // How far does the keyboard still cover this window? (0 when the phone already shrank the window for it.)
  const measure = useCallback(() => {
    root.current?.measureInWindow((_x, y, _w, h) => setOverlap(keyboardOverlap(y + h, keyboardTop.current)));
  }, []);
  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (e) => {
      keyboardTop.current = e.endCoordinates.screenY;
      setTimeout(measure, SETTLE_MS);
    });
    const hide = Keyboard.addListener('keyboardDidHide', () => {
      keyboardTop.current = null;
      setOverlap(0);
    });
    return () => {
      show.remove();
      hide.remove();
    };
  }, [measure]);
  const onLayout = (e: LayoutChangeEvent) => {
    setWinH(e.nativeEvent.layout.height);
    if (keyboardTop.current !== null) measure();
  };
  const layout = sheetLayout(winH, overlap);

  const panel = useAnimatedStyle(() => (reduced ? { opacity: progress.get() } : { transform: [{ translateY: (1 - progress.get()) * winH }] }));
  const scrim = useAnimatedStyle(() => ({ opacity: progress.get() }));

  if (!mounted) return null;
  return (
    <Modal visible transparent animationType="none" onRequestClose={onClose} statusBarTranslucent navigationBarTranslucent>
      <View ref={root} collapsable={false} style={styles.fill} onLayout={onLayout}>
        <Animated.View style={[styles.scrim, scrim]}>
          <Pressable accessibilityRole="button" accessibilityLabel="Close" style={styles.fill} onPress={onClose} />
        </Animated.View>
        <Animated.View style={[styles.panelWrap, panel]}>
          <SafeAreaView edges={['bottom']} style={[styles.panel, { height: Math.min(Math.floor(winH * 0.88), layout.maxHeight) }]}>
            <Conversation initialAnchor={anchor} onClose={onClose} />
            <View testID="keyboard-spacer" style={{ height: layout.paddingBottom }} />
          </SafeAreaView>
        </Animated.View>
      </View>
    </Modal>
  );
}

let turnSeq = 0;
const nextId = () => `t${++turnSeq}`;

function Conversation({ initialAnchor, onClose }: { initialAnchor: ChatAnchor | null; onClose(): void }) {
  const styles = useStyles();
  const t = useTheme();
  const ask = useAskCourse();
  const online = useOnline();
  const [anchor, setAnchor] = useState(initialAnchor);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const scroll = useRef<ScrollViewInstance>(null);
  useEffect(() => () => void (alive.current = false), []);

  async function send(raw: string) {
    const question = raw.trim().slice(0, MAX_QUESTION);
    if (!question || busy || !online) return;
    const history = toHistory(turns);
    setTurns((cur) => [...cur, { id: nextId(), role: 'user', text: question }]);
    setText('');
    setBusy(true);
    try {
      const r = await ask({ question, itemId: anchor?.id ?? null, history });
      if (alive.current) setTurns((cur) => [...cur, { id: nextId(), role: 'assistant', text: r.answer, citations: r.citations, refused: r.refused }]);
    } catch (e) {
      if (alive.current) setTurns((cur) => [...cur, { id: nextId(), role: 'assistant', text: chatErrorMessage(e), notice: true }]);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const openCitation = (c: ChatCitation) => {
    const a = citationAction(c);
    if (a.kind === 'card') {
      onClose();
      router.push({ pathname: '/item/[id]', params: { id: String(a.itemId) } });
    } else if (a.kind === 'link') {
      void Linking.openURL(a.url);
    }
  };

  const canSend = !!text.trim() && !busy && online;
  return (
    <View style={styles.conv}>
      <View style={styles.head}>
        <AppText variant="headlineSm" style={styles.flex}>Ask the course</AppText>
        <IconButton icon="close" label="Close" size={40} onPress={onClose} />
      </View>
      <OfflineBanner />
      {anchor ? (
        <View style={styles.anchor}>
          <Icon name="link-variant" size={16} color={t.primary} />
          <AppText variant="labelSm" color={t.primary} numberOfLines={1} style={styles.flex}>{`About: ${anchor.title}`}</AppText>
          <IconButton icon="close" label="Stop asking about this card" size={32} onPress={() => setAnchor(null)} />
        </View>
      ) : null}

      <ScrollView ref={scroll} style={styles.flex} contentContainerStyle={styles.list} keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: false })}>
        {turns.length === 0 ? (
          <View style={styles.starters}>
            <AppText variant="body" color={t.textSecondary}>Ask about your courses. Answers come only from your course messages and files, with sources.</AppText>
            {suggestions(!!anchor).map((s) => (
              <Pressable key={s} accessibilityRole="button" accessibilityLabel={s} disabled={!online || busy} onPress={() => void send(s)}
                style={({ pressed }) => [styles.starter, (pressed || !online) && { opacity: 0.6 }]}>
                <AppText variant="label" color={t.primary}>{s}</AppText>
              </Pressable>
            ))}
          </View>
        ) : (
          turns.map((turn) => <Bubble key={turn.id} turn={turn} onCitation={openCitation} />)
        )}
        {busy ? <Thinking /> : null}
      </ScrollView>

      <View style={styles.inputRow}>
        <TextInput
          accessibilityLabel="Your question"
          value={text}
          onChangeText={setText}
          editable={online && !busy}
          multiline
          maxLength={MAX_QUESTION}
          placeholder={online ? 'Ask about your courses' : 'You’re offline'}
          placeholderTextColor={t.textSecondary}
          style={styles.input}
        />
        <IconButton icon="send" label="Send" size={48} tone="accent" disabled={!canSend} onPress={() => void send(text)} />
      </View>
    </View>
  );
}

function Bubble({ turn, onCitation }: { turn: ChatTurn; onCitation(c: ChatCitation): void }) {
  const styles = useStyles();
  const t = useTheme();
  if (turn.role === 'user') {
    return (
      <View style={[styles.bubble, styles.mine]}>
        <AppText variant="body" color={t.onPrimary}>{turn.text}</AppText>
      </View>
    );
  }
  return (
    <View style={styles.theirsWrap}>
      <View style={[styles.bubble, styles.theirs, turn.notice && { backgroundColor: toneOf(t, t.warning).bg, borderColor: t.warning }]}>
        {turn.notice ? <Icon name="alert-circle-outline" size={18} color={t.warning} /> : turn.refused ? <Icon name="help-circle-outline" size={18} color={t.textSecondary} /> : null}
        <AppText variant="body" color={turn.notice ? t.warning : t.textPrimary} accessibilityLiveRegion="polite">{turn.text}</AppText>
      </View>
      {turn.citations?.length ? (
        <View style={styles.cites}>
          {turn.citations.map((c) => <CitationChip key={c.n} c={c} onPress={() => onCitation(c)} />)}
        </View>
      ) : null}
    </View>
  );
}

export function CitationChip({ c, onPress }: { c: ChatCitation; onPress(): void }) {
  const styles = useStyles();
  const t = useTheme();
  const action = citationAction(c);
  const live = action.kind !== 'none';
  return (
    <Pressable
      accessibilityRole={live ? 'button' : 'text'}
      accessibilityLabel={`Source ${c.n}: ${c.title}, ${c.team}${action.kind === 'card' ? '. Open the card' : action.kind === 'link' ? '. Open in Teams' : ''}`}
      disabled={!live}
      onPress={onPress}
      hitSlop={{ top: 6, bottom: 6, left: 4, right: 4 }}
      style={({ pressed }) => [styles.cite, { backgroundColor: tint(t.primary, t.surfaceRaised, 0.12) }, pressed && { opacity: 0.6 }]}>
      <AppText variant="labelSm" color={t.primary} numberOfLines={1}>{`[${c.n}] ${c.title}`}</AppText>
      {live ? <Icon name={action.kind === 'card' ? 'chevron-right' : 'open-in-new'} size={14} color={t.primary} /> : null}
    </Pressable>
  );
}

/** The assistant is working: one pulsing bubble (the same calm pulse as the loading skeletons; still under reduced motion). */
function Thinking() {
  const styles = useStyles();
  const t = useTheme();
  const reduced = useReducedMotion();
  const pulse = useSharedValue(1);
  useEffect(() => {
    if (!reduced) pulse.set(withRepeat(withTiming(0.5, { duration: 800 }), -1, true));
  }, [reduced, pulse]);
  const style = useAnimatedStyle(() => ({ opacity: pulse.get() }));
  return (
    <Animated.View accessible accessibilityLiveRegion="polite" accessibilityLabel="The assistant is thinking" style={[styles.bubble, styles.theirs, style]}>
      <AppText variant="body" color={t.textSecondary}>Thinking…</AppText>
    </Animated.View>
  );
}

const useStyles = makeStyles((t) => ({
  fill: { flex: 1, justifyContent: 'flex-end' },
  flex: { flex: 1 },
  scrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: t.scrim },
  panelWrap: { justifyContent: 'flex-end' },
  panel: { backgroundColor: t.surfaceRaised, borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, borderWidth: 1, borderBottomWidth: 0, borderColor: t.border },
  conv: { flex: 1, paddingHorizontal: space.md },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingTop: space.md, paddingBottom: space.sm },
  anchor: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingLeft: space.md, paddingRight: 4, marginBottom: space.sm, borderRadius: radius.full, borderWidth: 1, borderColor: t.primaryBorder, backgroundColor: t.primarySoft },
  list: { flexGrow: 1, gap: space.sm + 2, paddingVertical: space.sm },
  starters: { gap: space.sm + 2 },
  starter: { minHeight: 48, justifyContent: 'center', paddingHorizontal: space.md, borderRadius: radius.md, borderWidth: 1, borderColor: t.primaryBorder, backgroundColor: t.surface },
  bubble: { maxWidth: '88%', paddingHorizontal: space.md, paddingVertical: space.sm + 2, borderRadius: radius.lg, gap: 6 },
  mine: { alignSelf: 'flex-end', backgroundColor: t.primary, borderBottomRightRadius: 6 },
  theirsWrap: { alignSelf: 'flex-start', maxWidth: '100%', gap: 6 },
  theirs: { alignSelf: 'flex-start', backgroundColor: t.inset, borderWidth: 1, borderColor: t.border, borderBottomLeftRadius: 6 },
  cites: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  cite: { flexDirection: 'row', alignItems: 'center', gap: 4, minHeight: 32, maxWidth: 260, paddingHorizontal: 10, borderRadius: radius.sm + 4 },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: space.sm, paddingTop: space.sm, paddingBottom: space.sm },
  input: { flex: 1, minHeight: 48, maxHeight: 120, paddingHorizontal: space.md, paddingVertical: 12, borderRadius: radius.lg, borderWidth: 1, borderColor: t.border, backgroundColor: t.inset, color: t.textPrimary, fontFamily: 'Manrope_500Medium', fontSize: 15 },
}));
