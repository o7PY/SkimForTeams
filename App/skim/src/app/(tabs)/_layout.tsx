// The four tabs (PHASE4.md 7.2): Deck, History, Starred, Important. A floating bar with a solid surface (P4-D-173).
import { Tabs } from 'expo-router';

import { makeStyles, radius, useTheme } from '@/constants/theme';
import { FONT } from '@/ui/app-text';
import { Icon, type IconName } from '@/ui/icon';

const TABS: { name: string; title: string; on: IconName; off: IconName }[] = [
  { name: 'index', title: 'Deck', on: 'cards', off: 'cards-outline' },
  { name: 'history', title: 'History', on: 'history', off: 'history' },
  { name: 'starred', title: 'Starred', on: 'star', off: 'star-outline' },
  { name: 'important', title: 'Important', on: 'bell-alert', off: 'bell-alert-outline' },
];

export default function TabsLayout() {
  const styles = useStyles();
  const t = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: t.primary,
        tabBarInactiveTintColor: t.textSecondary,
        tabBarStyle: styles.bar,
        tabBarLabelStyle: styles.label,
        tabBarItemStyle: styles.item,
        sceneStyle: { backgroundColor: t.background },
      }}>
      {TABS.map((tab) => (
        <Tabs.Screen key={tab.name} name={tab.name} options={{ title: tab.title, tabBarIcon: ({ focused, color }) => <Icon name={focused ? tab.on : tab.off} size={24} color={String(color)} /> }} />
      ))}
    </Tabs>
  );
}

const useStyles = makeStyles((t) => ({
  bar: {
    position: 'absolute', left: 16, right: 16, bottom: 16, height: 64, paddingBottom: 0, paddingTop: 0, borderRadius: radius.lg,
    backgroundColor: t.surfaceRaised, borderWidth: 1, borderTopWidth: 1, borderColor: t.border, borderTopColor: t.border, elevation: 8,
  },
  item: { paddingVertical: 8 },
  label: { fontFamily: FONT[600], fontSize: 10, letterSpacing: 0.2, textTransform: 'uppercase' },
}));
