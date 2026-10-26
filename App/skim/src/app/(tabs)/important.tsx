// Important (PHASE4.md 7.2 item 7, P4-D-125): which kinds of event matter. Ticked types are shown in red, get automatic reminders
// (if that is on) and can send date-change alerts.
import { ScrollView, View } from 'react-native';

import { makeStyles, space, useTheme } from '@/constants/theme';
import { EVENT_TYPE_LABEL } from '@/lib/format';
import { EVENT_TYPES, toggleType } from '@/lib/settings';
import { useSettings } from '@/lib/use-settings';
import { AppText } from '@/ui/app-text';
import { CheckRow, SwitchRow } from '@/ui/rows';
import { TabScreen } from '@/ui/tab-screen';

const DESCRIPTION: Record<string, string> = {
  quiz: 'Short tests', exam: 'Midsems, endsems and other exams', assignment: 'Homework and deadlines', project: 'Project milestones and demos',
  lab: 'Lab sessions and lab reports', class: 'Lectures, and changes to them', office_hours: 'When and where to find the professor',
  presentation: 'Talks and seminars', other: 'Anything else with a date',
};

export default function Important() {
  const styles = useStyles();
  const t = useTheme();
  const { settings, update } = useSettings();
  return (
    <TabScreen title="Important" subtitle="What should stand out">
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        <AppText variant="body" color={t.textSecondary}>Ticked kinds are shown in red, and can remind you and alert you when their date changes.</AppText>
        <View style={styles.list}>
          {EVENT_TYPES.map((t) => (
            <CheckRow key={t} type={t} label={EVENT_TYPE_LABEL[t]} detail={DESCRIPTION[t]} checked={settings.important_types.includes(t)}
              onChange={() => void update({ important_types: toggleType(settings.important_types, t) })} />
          ))}
        </View>
        <SwitchRow
          label="Remind me automatically"
          detail="A reminder the evening before and on the day for ticked kinds, without you setting one"
          value={settings.reminders.auto_for_important}
          onChange={(v) => void update({ reminders: { auto_for_important: v } })}
        />
      </ScrollView>
    </TabScreen>
  );
}

const useStyles = makeStyles((t) => ({
  scroll: { gap: space.md, paddingBottom: space.md },
  list: { gap: space.sm },
}));
