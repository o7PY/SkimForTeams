# ELI5: what is Skim?

## The big idea
Professors already post everything in Microsoft Teams: exam dates, file links, "class moved to Room 4".
Students drown in it and miss things. **Skim** fixes that in two parts:

1. **A Teams bot** that a professor adds to their class team once. After that the prof changes nothing.
   The bot quietly reads what's posted.
2. **A student app** that turns those posts into swipe cards ("Midterm moved to Nov 5"), lets students star
   what matters, sends reminders, and has an "ask the course" chat ("when is the project due?").

The bot is the pipe. Everything the app does later depends on the bot reliably seeing every message.

## The roadmap
| Phase | What | Where we are |
|---|---|---|
| 1 | **Retrieval**: the bot sees and stores every message | **Built and running; partly tested** |
| 2 | **Files + history**: list the files in each channel, and read messages from before the bot joined | Designed, not built |
| 3 | **AI understanding**: pick out what's important, find dates, spot "this date changed" | Later |
| 4 | **Student app**: swipe cards, stars, reminders, notifications | Later |
| 5 | **Calendar, documents, chat**: calendar feed, favourite files, "ask the course" | Later |
| 6 | **Real pilot** with an actual professor | Later |

## What works today (Phase 1)
- Add the bot to a Teams team: it says hello **once**, with a short **pairing code** (that's how the app will
  later know which class is which).
- After that it is **silent** in channels and group chats. It never posts to a class.
- Every post, edit, delete, reaction and file attachment is saved. Edits keep the old wording, so we can
  later say "the exam moved from Nov 3 to Nov 5". Deletes are only marked, never erased.
- Message the bot privately and it answers `Logged ✅` (a handy "is it alive?" check).
- We can look at everything in the Supabase table viewer, or through password-protected web pages.

```
   Teams (the class team)
        |   "someone posted / edited / reacted..."  (a small signed message)
        v
   Skim bot on Google Cloud Run  --- checks it's really from Microsoft
        |   1. saves the message exactly as received
        |   2. tidies it into tables (messages, versions, files, reactions)
        v
   Supabase (database)  <---  you look here (table viewer / debug pages)
```

**Honest status:** the greeting and the private reply are confirmed in a real test tenant. The rest
(channel posts, edits, deletes, reactions, group chats, removal) is built and passes automated tests but
has **not yet been checked live**. The latest improvement to the greeting (it now also fires when Teams
announces the bot as a new team member, and never twice) is written but not yet deployed.

## What Phase 2 adds
- **The Files tab.** Every Teams channel has a Files tab. Phase 2 will list what's in it, keep that list fresh
  every ~15 minutes (new, renamed, moved, deleted files), and connect a chat attachment to the file it
  refers to, with no duplicates.
- **Old messages.** Today the bot only sees what's posted after it joins. Phase 2 will read the earlier
  posts and replies from Microsoft and file them the same way, marking where each came from.
- **Files stay in Teams.** We keep only a file's name, location, size, dates and a **link**. We never copy
  the files. That is cheaper and safer, and Teams keeps controlling who may open them.
- Still no AI, no app: Phase 2 only gives the later phases complete data.
