// The /api/v1 card payload (PHASE4.md section 6.3), as the backend sends it.
export type EventChip = {
  id: number;
  title: string;
  type: string;
  start: string | null; // 'YYYY-MM-DD' (date only) or an ISO time with +05:30
  end: string | null;
  all_day: boolean;
  venue: string | null;
  status: string;
  unconfirmed: boolean;
  important: boolean;
  details?: string | null;
  is_deadline?: boolean;
};

export type ChangeSide = { start?: string; venue?: string | null; status?: string; details?: string | null; title?: string } | null;

export type Attachment = { name: string | null; document_id: number | null; web_url: string | null };

export type Card = {
  id: number;
  kind: 'message' | 'document' | 'change';
  team: { id: string; name: string | null; ended: boolean };
  occurred_at: string;
  folded_ids: number[];
  author: { name: string | null; is_staff: boolean } | null;
  message: {
    subject: string | null;
    text: string;
    text_truncated: boolean;
    edited: boolean;
    deleted: boolean;
    reply_to_subject: string | null;
    attachments: Attachment[];
  } | null;
  document: { name: string | null; action: string | null; old_name: string | null; old_path: string | null; web_url: string | null } | null;
  change: { type: string | null; before: ChangeSide; after: ChangeSide } | null;
  events: EventChip[];
  links: { teams_deep_link: string | null; file_url: string | null };
  user: {
    starred: boolean;
    temp_star_expires_at: string | null;
    reminders: { id: string; kind: string; fire_at: string | null; virtual?: boolean }[];
    snoozed_until: string | null;
  };
  // history / starred lists
  swiped?: { state: string; at: string };
  starred_at?: string;
  // detail only
  recommended_reminders?: { kind: string; event_id: number; fire_at: string | null }[];
  event_history?: { event_id: number; changes: { id: number; type: string; at: string; before: ChangeSide; after: ChangeSide }[] }[];
  folded?: Card[];
};

export type DeckPage = { cards: Card[]; next_cursor: string | null; total: number };
