// One card (PHASE4.md 7.2). The real Teams content is always on it; the AI only adds the event lines, the change block and the
// badges. Static on purpose: the swipe animates the WRAPPER, so dragging never re-renders this.
import { StyleSheet, View } from 'react-native';

import { accents, colors, important as red, radius, solid, space } from '@/constants/skim-theme';
import { cardBody, cardTitle, changeSummary, eventLine, EVENT_TYPE_LABEL, hasUnconfirmed, importantEvent, relativeTime } from '@/lib/format';
import type { Card } from '@/lib/types';
import { AppText } from './app-text';
import { Icon, type IconName } from './icon';

const KIND_ICON: Record<Card['kind'], IconName> = { message: 'message-text-outline', document: 'file-document-outline', change: 'calendar-clock-outline' };
const MAX_FILES = 3;

export function Pill({ text, tone = 'accent' }: { text: string; tone?: 'accent' | 'red' | 'yellow' | 'muted' }) {
  const t = TONES[tone];
  return (
    <View style={[styles.pill, { backgroundColor: t.bg }]}>
      <AppText variant="micro" color={t.fg}>{text}</AppText>
    </View>
  );
}
const TONES = {
  accent: { bg: '#1E4A66', fg: colors.accentSoft },
  red: { bg: '#4A2330', fg: red.text },
  yellow: { bg: '#47401F', fg: accents.yellow },
  muted: { bg: '#38404F', fg: colors.onSurfaceVariant },
} as const;

export function DeckCard({ card, detail = false, now = new Date() }: { card: Card; detail?: boolean; now?: Date }) {
  const hot = importantEvent(card);
  const title = cardTitle(card);
  const body = cardBody(card);
  const change = card.change ? changeSummary(card.change) : null;
  const files = card.message?.attachments ?? [];
  const deleted = !!card.message?.deleted;

  return (
    <View
      accessible
      accessibilityLabel={`${card.team.name ?? 'Course'}. ${title}`}
      style={[styles.card, hot ? { borderColor: red.border, backgroundColor: solid.cardHot } : null]}>
      <View style={styles.header}>
        <View style={[styles.iconWrap, hot ? { backgroundColor: '#4A2330' } : null]}>
          <Icon name={KIND_ICON[card.kind]} size={20} color={hot ? red.text : colors.accentSoft} />
        </View>
        <View style={styles.headerText}>
          <AppText variant="labelSm" color={colors.accentSoft} numberOfLines={1}>{card.team.name ?? 'Course'}</AppText>
          {card.author?.name ? (
            <AppText variant="caption" color={colors.onSurfaceMuted} numberOfLines={1}>{card.author.name}{card.author.is_staff ? ' · Staff' : ''}</AppText>
          ) : null}
        </View>
        <AppText variant="caption" color={colors.onSurfaceMuted}>{relativeTime(card.occurred_at, now)}</AppText>
      </View>

      <View style={styles.badges}>
        {hot ? <Pill tone="red" text={EVENT_TYPE_LABEL[hot.type] ?? 'Important'} /> : null}
        {hasUnconfirmed(card) ? <Pill tone="yellow" text="Unconfirmed" /> : null}
        {card.message?.edited && !deleted ? <Pill tone="muted" text="Edited" /> : null}
        {card.message?.reply_to_subject ? <Pill tone="muted" text="Reply" /> : null}
        {card.team.ended ? <Pill tone="muted" text="Course ended" /> : null}
      </View>

      <AppText variant="headlineMd" numberOfLines={detail ? undefined : 3} style={styles.title}>{title}</AppText>

      {change ? (
        <View style={styles.change}>
          <AppText variant="micro" color={colors.accentSoft}>{change.label}</AppText>
          {change.from || change.to ? (
            <View style={styles.changeRow}>
              {change.from ? <AppText variant="body" color={colors.onSurfaceMuted} style={styles.struck}>{change.from}</AppText> : null}
              {change.from && change.to ? <Icon name="arrow-right" size={16} color={colors.onSurfaceVariant} /> : null}
              {change.to ? <AppText variant="label">{change.to}</AppText> : null}
            </View>
          ) : null}
        </View>
      ) : null}

      {body ? <AppText variant="body" color={colors.onSurfaceVariant} numberOfLines={detail ? undefined : 7}>{body}</AppText> : null}
      {card.message?.reply_to_subject ? <AppText variant="caption" color={colors.onSurfaceMuted} numberOfLines={1}>In reply to “{card.message.reply_to_subject}”</AppText> : null}

      {card.events.length > 0 && card.kind !== 'change' ? (
        <View style={styles.list}>
          {card.events.slice(0, detail ? undefined : 2).map((e) => (
            <View key={e.id} style={styles.eventRow}>
              <Icon name="calendar-blank-outline" size={16} color={e.important && e.status !== 'cancelled' ? red.text : colors.accentSoft} />
              <AppText variant="labelSm" numberOfLines={2} style={styles.flex} color={e.status === 'cancelled' ? colors.onSurfaceMuted : colors.onSurface}>
                {eventLine(e)}{e.status === 'cancelled' ? ' · cancelled' : ''}
              </AppText>
            </View>
          ))}
        </View>
      ) : null}

      {files.length > 0 ? (
        <View style={styles.list}>
          {files.slice(0, detail ? undefined : MAX_FILES).map((f, i) => (
            <View key={`${f.document_id ?? f.name}-${i}`} style={styles.fileChip}>
              <Icon name="paperclip" size={16} color={colors.onSurfaceVariant} />
              <AppText variant="labelSm" color={colors.onSurfaceVariant} numberOfLines={1} style={styles.flex}>{f.name ?? 'File'}</AppText>
            </View>
          ))}
          {!detail && files.length > MAX_FILES ? <AppText variant="caption" color={colors.onSurfaceMuted}>+{files.length - MAX_FILES} more</AppText> : null}
        </View>
      ) : null}

      {card.folded_ids.length > 0 ? (
        <View style={styles.folded}>
          <Icon name="layers-outline" size={16} color={colors.onSurfaceVariant} />
          <AppText variant="caption" color={colors.onSurfaceVariant} style={styles.flex}>
            {card.events[0] ? `Update to ${card.events[0].title} · ` : ''}replaces {card.folded_ids.length} earlier {card.folded_ids.length === 1 ? 'post' : 'posts'}
          </AppText>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1, overflow: 'hidden', borderRadius: radius.lg, borderWidth: 1, borderColor: solid.cardBorder,
    backgroundColor: solid.card, padding: space.card, gap: space.sm + 4,
  },
  header: { flexDirection: 'row', alignItems: 'center', gap: space.sm + 4 },
  headerText: { flex: 1 },
  iconWrap: { width: 36, height: 36, borderRadius: radius.full, alignItems: 'center', justifyContent: 'center', backgroundColor: '#1E4A66' },
  badges: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  pill: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: radius.sm },
  title: { marginTop: 2 },
  change: { backgroundColor: solid.block, borderColor: colors.borderAccent, borderWidth: 1, borderRadius: radius.md, padding: space.md - 2, gap: 6 },
  changeRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: space.sm },
  struck: { textDecorationLine: 'line-through' },
  list: { gap: 6 },
  eventRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  fileChip: { flexDirection: 'row', alignItems: 'center', gap: space.sm, backgroundColor: '#323A4B', borderRadius: radius.sm + 4, paddingHorizontal: 10, paddingVertical: 7 },
  folded: { flexDirection: 'row', alignItems: 'center', gap: space.sm, marginTop: 'auto' },
  flex: { flex: 1 },
});
