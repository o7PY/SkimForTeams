// The event-type chip: the same colour, icon and label everywhere an event type shows (cards, detail, the Important checklist).
import { View } from 'react-native';

import { makeStyles, typeTone, useTheme } from '@/constants/theme';
import { EVENT_TYPE_LABEL } from '@/lib/format';
import { AppText } from './app-text';
import { Icon, type IconName } from './icon';

export const EVENT_ICON: Record<string, IconName> = {
  quiz: 'help-circle-outline', exam: 'file-document-edit-outline', assignment: 'pencil-outline', project: 'layers-triple-outline', lab: 'flask-outline',
  class: 'book-open-variant', office_hours: 'door-open', presentation: 'presentation', other: 'tag-outline',
};
export const eventIcon = (type: string): IconName => EVENT_ICON[type] ?? EVENT_ICON.other;

export function TypeChip({ type, label }: { type: string; label?: string }) {
  const t = useTheme();
  const styles = useStyles();
  const tone = typeTone(t, type);
  return (
    <View style={[styles.chip, { backgroundColor: tone.bg }]}>
      <Icon name={eventIcon(type)} size={14} color={tone.fg} />
      <AppText variant="micro" color={tone.fg}>{label ?? EVENT_TYPE_LABEL[type] ?? EVENT_TYPE_LABEL.other}</AppText>
    </View>
  );
}

const useStyles = makeStyles(() => ({
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8 },
}));
