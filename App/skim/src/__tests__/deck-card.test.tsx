import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { important as red, solid } from '@/constants/skim-theme';
import type { Card } from '@/lib/types';
import { DeckCard } from '@/ui/deck-card';

import { changeCard, documentCard, event, messageCard } from './fixtures';


// The icon font is a binary asset Jest cannot resolve; a stub that prints the icon's name lets tests see which icon was chosen.
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

const NOW = new Date('2026-10-26T12:00:00+05:30');

function render(card: Card, detail = false) {
  let r!: ReturnType<typeof create>;
  act(() => {
    r = create(<DeckCard card={card} detail={detail} now={NOW} />);
  });
  return r;
}

const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';

/** What a person reads: each <Text> is one string, however React split its children. */
function texts(node: ReactTestInstance): string[] {
  const out: string[] = [];
  const plain = (n: ReactTestInstance | string): string => (typeof n === 'string' ? n : n.children.map(plain).join(''));
  const walk = (n: ReactTestInstance | string) => {
    if (typeof n === 'string') return;
    if (isText(n)) { out.push(plain(n)); return; }
    n.children.forEach(walk);
  };
  walk(node);
  return out;
}
const all = (r: ReturnType<typeof create>) => texts(r.root).join(' | ');
const flat = (style: unknown): Record<string, unknown> => Object.assign({}, ...[style].flat(5).filter(Boolean));
const rootStyle = (r: ReturnType<typeof create>) => flat((r.toJSON() as unknown as { props: { style: unknown } }).props.style);

test('a staff message: team, author, time, headline and body are all on the card', () => {
  const t = all(render(messageCard({ occurred_at: '2026-10-26T09:00:00+05:30' })));
  expect(t).toContain('MA 221');
  expect(t).toContain('Prof Rao · Staff');
  expect(t).toContain('3 h ago');
  expect(t).toContain('Quiz 1 is on Monday');
  expect(t).toContain('Bring a calculator and your ID card.');
});

test('a student author is named without the Staff tag; no author line when there is none', () => {
  expect(all(render(messageCard({ author: { name: 'Asha', is_staff: false } })))).toContain('Asha');
  expect(all(render(messageCard({ author: { name: 'Asha', is_staff: false } })))).not.toContain('Staff');
  expect(all(render(messageCard({ author: null })))).not.toContain('·  Staff');
});

test('an important event turns the card red and names its type; an ordinary card does not', () => {
  const hot = render(messageCard({ events: [event()] }));
  expect(rootStyle(hot).borderColor).toBe(red.border);
  expect(rootStyle(hot).backgroundColor).toBe(solid.cardHot);
  expect(all(hot)).toContain('Quiz');
  const calm = render(messageCard({ events: [event({ important: false })] }));
  expect(rootStyle(calm).borderColor).not.toBe(red.border);
  expect(rootStyle(render(messageCard())).borderColor).not.toBe(red.border);
});

test('a cancelled important event is not highlighted, and its line says so', () => {
  const r = render(messageCard({ events: [event({ status: 'cancelled' })] }));
  expect(rootStyle(r).borderColor).not.toBe(red.border);
  expect(all(r)).toContain('cancelled');
});

test('event lines carry title, date and time, and venue', () => {
  expect(all(render(messageCard({ events: [event()] })))).toContain('Quiz 1 · Mon 2 Nov, 10:00 · LHC 104');
});

test('the deck shows two events at most; the detail shows them all', () => {
  const events = [event({ id: 1, title: 'A' }), event({ id: 2, title: 'B' }), event({ id: 3, title: 'C' })];
  const deck = all(render(messageCard({ events })));
  expect(deck).toContain('A ·');
  expect(deck).toContain('B ·');
  expect(deck).not.toContain('C ·');
  expect(all(render(messageCard({ events }), true))).toContain('C ·');
});

test('badges: unconfirmed, edited, reply, course ended', () => {
  const t = all(render(messageCard({
    team: { id: 'T1', name: 'MA 221', ended: true },
    events: [event({ unconfirmed: true })],
    message: { ...messageCard().message!, edited: true, reply_to_subject: 'Quiz 1' },
  })));
  for (const word of ['Unconfirmed', 'Edited', 'Reply', 'Course ended', 'In reply to “Quiz 1”']) expect(t.toLowerCase()).toContain(word.toLowerCase());
});

test('no badges on a plain card', () => {
  const t = all(render(messageCard()));
  for (const word of ['Unconfirmed', 'Edited', 'Reply', 'Course ended']) expect(t).not.toMatch(new RegExp(word, 'i'));
});

test('files: three chips and a +N more on the deck, every file in the detail', () => {
  const files = ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf', 'e.pdf'].map((name, i) => ({ name, document_id: i, web_url: null }));
  const card = messageCard({ message: { ...messageCard().message!, attachments: files } });
  const deck = all(render(card));
  expect(deck).toContain('a.pdf');
  expect(deck).toContain('c.pdf');
  expect(deck).not.toContain('d.pdf');
  expect(deck).toContain('2 more');
  const full = all(render(card, true));
  expect(full).toContain('e.pdf');
  expect(full).not.toContain('more');
});

test('a file with no name still shows a chip', () => {
  expect(all(render(messageCard({ message: { ...messageCard().message!, attachments: [{ name: null, document_id: null, web_url: null }] } })))).toContain('File');
});

test('a file card names the file and the action', () => {
  const t = all(render(documentCard()));
  expect(t).toContain('Syllabus v2.pdf');
  expect(t).toContain('File renamed from “Syllabus.pdf”');
});

test('a change card shows what moved: the label, the old time struck through, the new time', () => {
  const r = render(changeCard());
  const t = all(r);
  expect(t).toContain('Moved');
  expect(t).toContain('Fri 30 Oct, 10:00');
  expect(t).toContain('Mon 2 Nov, 10:00');
  const old = r.root.findAll((n) => isText(n) && n.children.join('') === 'Fri 30 Oct, 10:00')[0];
  expect(flat(old.props.style).textDecorationLine).toBe('line-through');
  expect(rootStyle(r).borderColor).toBe(red.border); // a moved quiz is still an important thing
});

test('a change card lists no separate event line (the change block already says it)', () => {
  expect(all(render(changeCard()))).not.toContain('LHC 104');
});

test('folded originals show as an update line with the count', () => {
  expect(all(render(changeCard({ folded_ids: [8, 9] })))).toContain('Update to Quiz 1 · replaces 2 earlier posts');
  expect(all(render(changeCard({ folded_ids: [8] })))).toContain('replaces 1 earlier post');
  expect(all(render(changeCard({ folded_ids: [] })))).not.toContain('replaces');
});

test('a deleted message shows no content at all', () => {
  const t = all(render(messageCard({ message: { ...messageCard().message!, deleted: true, text: '', attachments: [] } })));
  expect(t).toContain('Deleted in Teams');
  expect(t).not.toContain('Edited');
});

test('the deck clips long text; the detail does not', () => {
  const card = messageCard({ message: { ...messageCard().message!, text: 'Title\n' + 'word '.repeat(300) } });
  const body = (r: ReturnType<typeof create>) => r.root.findAll((n) => isText(n) && n.children.join('').startsWith('word word'))[0];
  expect(body(render(card)).props.numberOfLines).toBe(7);
  expect(body(render(card, true)).props.numberOfLines).toBeUndefined();
});

test('the card announces itself to a screen reader with its team and headline', () => {
  const j = render(messageCard()).toJSON() as unknown as { props: Record<string, unknown> };
  expect(j.props.accessible).toBe(true);
  expect(j.props.accessibilityLabel).toBe('MA 221. Quiz 1 is on Monday');
});

test('a team without a name still renders', () => {
  expect(all(render(messageCard({ team: { id: 'T1', name: null, ended: false } })))).toContain('Course');
});

test('each kind of card has its own icon', () => {
  expect(all(render(messageCard()))).toContain('[icon:message-text-outline]');
  expect(all(render(documentCard()))).toContain('[icon:file-document-outline]');
  expect(all(render(changeCard()))).toContain('[icon:calendar-clock-outline]');
});

const OPAQUE = /^#[0-9A-Fa-f]{6}$/; // 6-digit hex = no alpha channel

test('a card is NEVER see-through: its surface is a solid colour in every state (cards behind it must not show through)', () => {
  for (const card of [messageCard(), messageCard({ events: [event()] }), documentCard(), changeCard()]) {
    const bg = rootStyle(render(card)).backgroundColor as string;
    expect(bg).toMatch(OPAQUE);
  }
  for (const c of Object.values(solid)) expect(c).toMatch(OPAQUE);
});

test('the change block inside a card is solid too', () => {
  const r = render(changeCard());
  const block = r.root.findAll((n) => (n.type as unknown) === 'View' && flat(n.props.style).borderColor !== undefined && flat(n.props.style).padding === 14)[0];
  expect(flat(block.props.style).backgroundColor).toMatch(OPAQUE);
});

test('every coloured mark on a card (pills, file chips, the icon disc) is solid as well', () => {
  const r = render(messageCard({
    events: [event({ unconfirmed: true })],
    message: { ...messageCard().message!, edited: true, attachments: [{ name: 'a.pdf', document_id: 1, web_url: null }] },
  }));
  const colorsUsed = r.root.findAll((n) => (n.type as unknown) === 'View')
    .map((n) => flat(n.props.style).backgroundColor).filter((c): c is string => typeof c === 'string');
  expect(colorsUsed.length).toBeGreaterThan(4);
  for (const c of colorsUsed) expect(c).toMatch(OPAQUE);
});
