// The five tabs: Teams, History, Deck (in the middle, a larger blue circle), Starred, Important. A floating bar with a solid surface
// (P4-D-173). The app opens on the Deck.
import { Tabs } from 'expo-router';
import { View } from 'react-native';

import { makeStyles, radius, useTheme } from '@/constants/theme';
import { FONT } from '@/ui/app-text';
import { Icon, type IconName } from '@/ui/icon';

// The first tab in the bar is Teams, but the app opens on the Deck.
export const unstable_settings = { initialRouteName: 'index' };

const TABS: { name: string; title: string; on: IconName; off: IconName; center?: boolean }[] = [
  { name: 'teams', title: 'Teams', on: 'account-group', off: 'account-group-outline' },
  { name: 'history', title: 'History', on: 'history', off: 'history' },
  { name: 'index', title: 'Deck', on: 'cards', off: 'cards-outline', center: true },
  { name: 'starred', title: 'Starred', on: 'star', off: 'star-outline' },
  { name: 'important', title: 'Important', on: 'bell-alert', off: 'bell-alert-outline' },
];

/** The Deck's tab icon: a blue circle, bigger than the others, with a ring when it is the open tab. */
function DeckIcon({ focused }: { focused: boolean }) {
  const styles = useStyles();
  const t = useTheme();
  return (
    <View style={[styles.deck, focused && styles.deckOn]}>
      <Icon name="cards" size={28} color={t.onPrimary} />
    </View>
  );
}

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
        <Tabs.Screen
          key={tab.name}
          name={tab.name}
          options={{
            title: tab.title,
            ...(tab.center
              ? { tabBarLabel: () => null, tabBarAccessibilityLabel: tab.title, tabBarIcon: ({ focused }) => <DeckIcon focused={focused} /> }
              : { tabBarIcon: ({ focused, color }) => <Icon name={focused ? tab.on : tab.off} size={24} color={String(color)} /> }),
          }}
        />
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
  deck: { width: 56, height: 56, borderRadius: radius.full, backgroundColor: t.primary, alignItems: 'center', justifyContent: 'center', borderWidth: 3, borderColor: t.surfaceRaised },
  deckOn: { borderColor: t.primaryBorder },
}));
