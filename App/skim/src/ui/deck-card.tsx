// One card (PHASE4.md 7.2). The real Teams content is always on it; the AI only adds the event lines, the change block and the
// badges. Static on purpose: the swipe animates the WRAPPER, so dragging never re-renders this.
import { View } from 'react-native';

import { makeStyles, radius, space, teamAccent, tint, toneOf, typeTone, useTheme } from '@/constants/theme';
import { cardBody, cardTitle, changeSummary, eventLine, hasUnconfirmed, importantEvent, relativeTime } from '@/lib/format';
import type { Card } from '@/lib/types';
import { AppText } from './app-text';
import { Icon, type IconName } from './icon';
import { ChipBody, eventIcon, TypeChip } from './type-chip';

const KIND_ICON: Record<Card['kind'], IconName> = { message: 'message-text-outline', document: 'file-document-outline', change: 'calendar-clock-outline' };
const MAX_FILES = 3;

export function Pill({ text, tone = 'primary' }: { text: string; tone?: 'primary' | 'important' | 'warning' | 'update' | 'muted' }) {
  const t = useTheme();
  const fg = { primary: t.primary, important: t.important.text, warning: t.warning, update: t.update, muted: t.textSecondary }[tone];
  const bg = tone === 'important' ? tint(t.important.border, t.important.fill, 0.25) : tone === 'muted' ? t.inset : toneOf(t, fg).bg;
  return <ChipBody bg={bg} fg={fg} text={text} />;
}

export function DeckCard({ card, detail = false, now = new Date() }: { card: Card; detail?: boolean; now?: Date }) {
  const t = useTheme();
  const styles = useStyles();
  const hot = importantEvent(card);
  const title = cardTitle(card);
  const body = cardBody(card);
  const change = card.change ? changeSummary(card.change) : null;
  const files = card.message?.attachments ?? [];
  const deleted = !!card.message?.deleted;
  const types = [...new Set(card.events.map((e) => e.type))].slice(0, 2);
  const isChange = card.kind === 'change';

  return (
    <View
      accessible
      accessibilityLabel={`${card.team.name ?? 'Course'}. ${title}`}
      style={[styles.card, isChange && { borderLeftWidth: 4, borderLeftColor: t.update }, hot ? { borderColor: t.important.border, backgroundColor: t.important.fill } : null]}>
      <View style={styles.header}>
        <View style={[styles.iconWrap, { backgroundColor: hot ? tint(t.important.border, t.important.fill, 0.25) : isChange ? toneOf(t, t.update).bg : t.primarySoft }]}>
          <Icon name={KIND_ICON[card.kind]} size={20} color={hot ? t.important.text : isChange ? t.update : t.primary} />
        </View>
        <View style={styles.headerText}>
          <View style={styles.teamRow}>
            <View style={[styles.teamDot, { backgroundColor: teamAccent(t, card.team.id) }]} />
            <AppText variant="labelSm" color={t.primary} numberOfLines={1} style={styles.flex}>{card.team.name ?? 'Course'}</AppText>
          </View>
          {card.author?.name ? (
            <AppText variant="caption" color={t.textSecondary} numberOfLines={1}>{card.author.name}{card.author.is_staff ? ' · Staff' : ''}</AppText>
          ) : null}
        </View>
        <AppText variant="caption" color={t.textSecondary}>{relativeTime(card.occurred_at, now)}</AppText>
      </View>

      <View style={styles.badges}>
        {hot ? <Pill tone="important" text="Important" /> : null}
        {types.map((ty) => <TypeChip key={ty} type={ty} />)}
        {hasUnconfirmed(card) ? <Pill tone="warning" text="Unconfirmed" /> : null}
        {card.message?.edited && !deleted ? <Pill tone="muted" text="Edited" /> : null}
        {card.message?.reply_to_subject ? <Pill tone="muted" text="Reply" /> : null}
        {card.team.ended ? <Pill tone="muted" text="Course ended" /> : null}
      </View>

      <AppText variant="headlineMd" numberOfLines={detail ? undefined : 3} style={styles.title}>{title}</AppText>

      {change ? (
        <View style={styles.change}>
          <AppText variant="micro" color={t.update}>{change.label}</AppText>
          {change.from || change.to ? (
            <View style={styles.changeRow}>
              {change.from ? <AppText variant="body" color={t.textSecondary} style={styles.struck}>{change.from}</AppText> : null}
              {change.from && change.to ? <Icon name="arrow-right" size={16} color={t.textSecondary} /> : null}
              {change.to ? <AppText variant="label">{change.to}</AppText> : null}
            </View>
          ) : null}
        </View>
      ) : null}

      {body ? <AppText variant="body" color={t.textSecondary} numberOfLines={detail ? undefined : 7}>{body}</AppText> : null}
      {card.message?.reply_to_subject ? <AppText variant="caption" color={t.textSecondary} numberOfLines={1}>In reply to “{card.message.reply_to_subject}”</AppText> : null}

      {card.events.length > 0 && card.kind !== 'change' ? (
        <View style={styles.list}>
          {card.events.slice(0, detail ? undefined : 2).map((e) => (
            <View key={e.id} style={styles.eventRow}>
              <Icon name={eventIcon(e.type)} size={16} color={e.important && e.status !== 'cancelled' ? t.important.text : typeTone(t, e.type).fg} />
              <AppText variant="labelSm" numberOfLines={2} style={styles.flex} color={e.status === 'cancelled' ? t.textSecondary : t.textPrimary}>
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
              <Icon name="paperclip" size={16} color={t.textSecondary} />
              <AppText variant="labelSm" color={t.textSecondary} numberOfLines={1} style={styles.flex}>{f.name ?? 'File'}</AppText>
            </View>
          ))}
          {!detail && files.length > MAX_FILES ? <AppText variant="caption" color={t.textSecondary}>+{files.length - MAX_FILES} more</AppText> : null}
        </View>
      ) : null}

      {card.folded_ids.length > 0 ? (
        <View style={styles.folded}>
          <Icon name="layers-outline" size={16} color={t.textSecondary} />
          <AppText variant="caption" color={t.textSecondary} style={styles.flex}>
            {card.events[0] ? `Update to ${card.events[0].title} · ` : ''}replaces {card.folded_ids.length} earlier {card.folded_ids.length === 1 ? 'post' : 'posts'}
          </AppText>
        </View>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((t) => ({
  card: {
    flex: 1, overflow: 'hidden', borderRadius: radius.lg, borderWidth: 1, borderColor: t.border,
    backgroundColor: t.surfaceRaised, padding: space.card, gap: space.sm + 4,
  },
  header: { flexDirection: 'row', alignItems: 'center', gap: space.sm + 4 },
  headerText: { flex: 1 },
  teamRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  teamDot: { width: 10, height: 10, borderRadius: 5 },
  iconWrap: { width: 36, height: 36, borderRadius: radius.full, alignItems: 'center', justifyContent: 'center' },
  badges: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  title: { marginTop: 2 },
  change: { backgroundColor: tint(t.update, t.surfaceRaised, 0.12), borderColor: tint(t.update, t.surfaceRaised, 0.35), borderWidth: 1, borderRadius: radius.md, padding: space.md - 2, gap: 6 },
  changeRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: space.sm },
  struck: { textDecorationLine: 'line-through' },
  list: { gap: 6 },
  eventRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  fileChip: { flexDirection: 'row', alignItems: 'center', gap: space.sm, backgroundColor: t.inset, borderRadius: radius.sm + 4, paddingHorizontal: 10, paddingVertical: 7 },
  folded: { flexDirection: 'row', alignItems: 'center', gap: space.sm, marginTop: 'auto' },
  flex: { flex: 1 },
}));
