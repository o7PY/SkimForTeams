// The event-type chip: the same colour, icon and label everywhere an event type shows (cards, detail, the Important checklist).
// A chip whose colour changes (say a card turns from unconfirmed to confirmed) cross-fades over 200 ms (P5-D-107).
import Animated from 'react-native-reanimated';

import { makeStyles, typeTone, useTheme } from '@/constants/theme';
import { EVENT_TYPE_LABEL } from '@/lib/format';
import { variantStyle } from './app-text';
import { Icon, type IconName } from './icon';

export const EVENT_ICON: Record<string, IconName> = {
  quiz: 'help-circle-outline', exam: 'file-document-edit-outline', assignment: 'pencil-outline', project: 'layers-triple-outline', lab: 'flask-outline',
  class: 'book-open-variant', office_hours: 'door-open', presentation: 'presentation', other: 'tag-outline',
};
export const eventIcon = (type: string): IconName => EVENT_ICON[type] ?? EVENT_ICON.other;

const FADE = { transitionDuration: 200 } as const;

/** A small rounded label; `bg` and `fg` cross-fade when they change. */
export function ChipBody({ bg, fg, text, icon }: { bg: string; fg: string; text: string; icon?: IconName }) {
  const styles = useStyles();
  return (
    <Animated.View style={[styles.chip, { backgroundColor: bg, transitionProperty: 'backgroundColor', ...FADE }]}>
      {icon ? <Icon name={icon} size={14} color={fg} /> : null}
      <Animated.Text style={[variantStyle('micro'), styles.text, { color: fg, transitionProperty: 'color', ...FADE }]}>{text}</Animated.Text>
    </Animated.View>
  );
}

export function TypeChip({ type, label }: { type: string; label?: string }) {
  const t = useTheme();
  const tone = typeTone(t, type);
  return <ChipBody bg={tone.bg} fg={tone.fg} icon={eventIcon(type)} text={label ?? EVENT_TYPE_LABEL[type] ?? EVENT_TYPE_LABEL.other} />;
}

const useStyles = makeStyles(() => ({
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, alignSelf: 'flex-start' },
  text: { includeFontPadding: false },
}));
