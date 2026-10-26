// A bottom sheet. The PANEL is solid (nothing behind it shows through); only the dim layer behind the panel is translucent, because
// that is what dims the screen.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Keyboard, Modal, Pressable, ScrollView, View, type HostInstance, type LayoutChangeEvent, type ScrollViewInstance } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { keyboardOverlap, sheetLayout } from '@/lib/sheet-layout';
import { AppText } from './app-text';
import { IconButton } from './buttons';
import { Icon, type IconName } from './icon';

const SETTLE_MS = 150; // let the window finish any resize of its own before measuring what the keyboard still covers

export function Sheet({ visible, title, onClose, children }: { visible: boolean; title: string; onClose(): void; children: ReactNode }) {
  const styles = useStyles();
  const root = useRef<HostInstance>(null);
  const scroll = useRef<ScrollViewInstance>(null);
  const keyboardTop = useRef<number | null>(null);
  const [overlap, setOverlap] = useState(0);
  const [height, setHeight] = useState(800);

  // How far does the keyboard still cover this sheet's window? (0 when the phone already shrank the window for it.)
  const measure = useCallback(() => {
    root.current?.measureInWindow((_x, y, _w, h) => setOverlap(keyboardOverlap(y + h, keyboardTop.current)));
  }, []);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (e) => {
      keyboardTop.current = e.endCoordinates.screenY;
      setTimeout(() => {
        measure();
        setTimeout(() => scroll.current?.scrollToEnd({ animated: true }), 60); // the field and the button are the last things in a sheet
      }, SETTLE_MS);
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
    setHeight(e.nativeEvent.layout.height);
    if (keyboardTop.current !== null) measure(); // the window changed size while the keyboard is up: measure again
  };
  const layout = sheetLayout(height, overlap);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent navigationBarTranslucent>
      <View ref={root} collapsable={false} style={styles.fill} onLayout={onLayout}>
        <Pressable accessibilityLabel="Close" accessibilityRole="button" style={styles.dim} onPress={onClose} />
        <SafeAreaView edges={['bottom']} style={[styles.panel, { maxHeight: layout.maxHeight }]}>
          <View style={styles.head}>
            <AppText variant="headlineSm" style={styles.title}>{title}</AppText>
            <IconButton icon="close" label="Close" size={40} onPress={onClose} />
          </View>
          <ScrollView ref={scroll} contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">{children}</ScrollView>
          <View testID="keyboard-spacer" style={{ height: layout.paddingBottom }} />
        </SafeAreaView>
      </View>
    </Modal>
  );
}

export function OptionRow({ icon, label, detail, onPress, disabled, selected }: { icon?: IconName; label: string; detail?: string; onPress(): void; disabled?: boolean; selected?: boolean }) {
  const styles = useStyles();
  const t = useTheme();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={detail ? `${label}, ${detail}` : label} accessibilityState={{ disabled: !!disabled, selected: !!selected }} disabled={disabled} onPress={onPress}
      style={({ pressed }) => [styles.row, selected && styles.rowOn, (pressed || disabled) && { opacity: 0.6 }]}>
      {icon ? <Icon name={icon} size={22} color={selected ? t.primary : t.textSecondary} /> : null}
      <View style={styles.rowText}>
        <AppText variant="label">{label}</AppText>
        {detail ? <AppText variant="caption" color={t.textSecondary}>{detail}</AppText> : null}
      </View>
    </Pressable>
  );
}

export function SectionLabel({ text }: { text: string }) {
  const styles = useStyles();
  const t = useTheme();
  return <AppText variant="micro" color={t.textSecondary} style={styles.section}>{text}</AppText>;
}

const useStyles = makeStyles((t) => ({
  fill: { flex: 1, justifyContent: 'flex-end' },
  dim: { ...{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }, backgroundColor: t.scrim },
  panel: { backgroundColor: t.surfaceRaised, borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, borderWidth: 1, borderBottomWidth: 0, borderColor: t.border },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: space.sm },
  title: { flex: 1 },
  body: { paddingHorizontal: space.lg, paddingBottom: space.lg, gap: space.sm },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, minHeight: 56, paddingHorizontal: space.md, paddingVertical: 10, borderRadius: radius.md, backgroundColor: t.inset, borderWidth: 1, borderColor: t.border },
  rowOn: { borderColor: t.primary, backgroundColor: t.primarySoft },
  rowText: { flex: 1, gap: 2 },
  section: { marginTop: space.sm },
}));
