# ELI5: what is Skim doing?

**The idea.** A class lives in a Microsoft Teams "team". Professors post exam dates, file links and
changes there, and students miss things. Skim will eventually show students just the important bits.
Phase 1 only does the groundwork: **a bot that quietly sees every message and writes it down.**

**What Teams sends us.** Whenever anyone posts, edits, deletes, reacts to, or attaches a file to a
message where our bot is installed, Teams sends our server a small JSON note ("an activity"). It also
sends notes when the bot is added or removed.

**What the bot does with it.**
1. Checks the note really came from Microsoft (a signed token).
2. Saves the note exactly as received (the "raw" copy). If we ever misread it, nothing is lost.
3. Reads it and files it into neat tables: teams, messages, every edited version of a message,
   attachments, reactions.
4. Stays silent, except: one hello (once per install) with a **pairing code** when added to a team, and a
   `Logged ✅` reply if you DM it.

```
 Teams  --(signed JSON note)-->  Skim server  --raw copy-->  raw_activities
                                     |                          (Postgres on Supabase)
                                     +--tidy rows--> teams / messages / versions /
                                                     attachments / reactions
 You: look in the Supabase table viewer, or  GET /debug/...  (with your secret token)
```

**Looking at the data.** Open the Supabase table viewer, or call e.g.
`curl -H "Authorization: Bearer $DEBUG_TOKEN" https://<host>/debug/messages`. The server's log
also prints one JSON line per note, as a backup view.

**Edits and deletes.** Nothing is overwritten: an edit adds a new "version", a delete just stamps
`deleted_at`. Later phases can then say "the exam moved from Nov 3 to Nov 5".

**Not yet:** AI summaries, a student app, reminders, old-message backfill.
