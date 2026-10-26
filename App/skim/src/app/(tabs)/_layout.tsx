// The four tabs (PHASE4.md 7.2): Deck, History, Starred, Important. A floating bar with a solid surface (P4-D-173).
import { Tabs } from 'expo-router';
import { StyleSheet } from 'react-native';

import { colors, radius, solid } from '@/constants/skim-theme';
import { FONT } from '@/ui/app-text';
import { Icon, type IconName } from '@/ui/icon';

const TABS: { name: string; title: string; on: IconName; off: IconName }[] = [
  { name: 'index', title: 'Deck', on: 'cards', off: 'cards-outline' },
  { name: 'history', title: 'History', on: 'history', off: 'history' },
  { name: 'starred', title: 'Starred', on: 'star', off: 'star-outline' },
  { name: 'important', title: 'Important', on: 'bell-alert', off: 'bell-alert-outline' },
];

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: 'rgba(255,255,255,0.55)',
        tabBarStyle: styles.bar,
        tabBarLabelStyle: styles.label,
        tabBarItemStyle: styles.item,
        sceneStyle: { backgroundColor: colors.surface },
      }}>
      {TABS.map((t) => (
        <Tabs.Screen key={t.name} name={t.name} options={{ title: t.title, tabBarIcon: ({ focused, color }) => <Icon name={focused ? t.on : t.off} size={24} color={String(color)} /> }} />
      ))}
    </Tabs>
  );
}

const styles = StyleSheet.create({
  bar: {
    position: 'absolute', left: 16, right: 16, bottom: 16, height: 64, paddingBottom: 0, paddingTop: 0, borderRadius: radius.lg,
    backgroundColor: solid.card, borderWidth: 1, borderTopWidth: 1, borderColor: solid.cardBorder, borderTopColor: solid.cardBorder, elevation: 8,
  },
  item: { paddingVertical: 8 },
  label: { fontFamily: FONT[600], fontSize: 10, letterSpacing: 0.2, textTransform: 'uppercase' },
});
