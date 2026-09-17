# Changelog

Notable changes to SpikeSight. Newest first.

The version you are running is shown at the top of the gear panel.

<!-- Keep entries casual and short. Say what changed and why anyone would
     care - not how it works. Implementation detail belongs in the code. -->

---

## 2.2 — It tells you when there's a new one

### Added

- **Update notice.** SpikeSight now checks whether a newer release exists and
  shows a bar at the top if there is, with a link to it. No more finding out
  months later that you're four versions behind.

It asks GitHub at most once every few hours, sends nothing but the version
you're on, and **never downloads or installs anything on its own** — clicking
through is your call. There's a switch for it in Settings, and turning it off
means the request is never made at all.

---

## 2.1 — Now called SpikeSight

### Changed

- **ValScout is now SpikeSight.** The old name was close enough to another
  app's to cause confusion.
- **Your stuff comes with you.** Flags, encounters, settings and the match
  cache are copied over the first time you run it. Nothing is deleted — the
  old folder is left exactly as it was, so check everything's there and then
  delete it if you like. It's usually around a gigabyte, almost all of it
  browser cache.
- If you had it starting with Windows, the old entry is removed so you don't
  end up launching both.
- Speaking of which: that browser cache is now capped. It was allowed to grow
  to hundreds of megabytes for a page that never changes.

One thing you'll want to redo yourself: a desktop shortcut from the old
version still points at the old app. Delete it and make a new one from the
gear menu.

---

## 2.0.1

### Changed

- **Top agents now follow the map you're about to play**, rather than showing
  everything lumped together. Maps suit different agents, so that's the number
  you actually want. It reads further back through your history to make that
  worth something — you'll usually have 5-10 games per map instead of 2. Only
  falls back to all maps if you've never played the one you're on.
- **SpikeSight no longer minimizes itself when the match starts.** The enemy
  team only shows up once you're in, which is when the board is most worth
  looking at. It's a toggle now — "Minimize after agent select" in Settings —
  for anyone who'd rather have the frames.

---

## 2.0 — Light mode, the overlay, and no more GPU

### Added

- **Light mode.** There's a Theme picker at the top now — Dark, Light, or
  System to follow Windows.
- **A pre-match overlay.** During agent select it sits on the edge of your
  screen with your team's ranks, win rates, K/D, recent form and any flags on
  them, plus your best agents for that map. It can't be clicked, so it never
  gets in the way of a pick, and you can drag it wherever you want. Off by
  default — turn it on in Settings.

### Fixed

- **No more GPU.** SpikeSight draws its own window on the processor now. It used
  to hold a Direct3D device open on your graphics card, which was enough to
  cost you frames in game — sometimes even after you closed it.

---

## 1.0.5 — Telling hidden players apart

### Added

- Players in streamer mode now get a little code next to their agent, like
  `K3PT`. It is the same code every time you run into them, so a flag you put
  on one Brimstone stays on that Brimstone and not on every Brimstone you ever
  meet. Notes always worked this way under the hood - you just couldn't see it.
- The Encounters tab used to list all of them as "Unknown / hidden player",
  which was useless once there was more than one. They show up as separate
  people now, and you can search the code.

### Fixed

- Your notes file can now survive going bad. If it does, SpikeSight rebuilds it
  at startup from whatever is still readable and keeps the broken one next to
  it, instead of quietly carrying on and losing more.
- The backup it takes at every startup wasn't actually a working copy. It is
  now. If you ever needed the old one, it wouldn't have opened.

---

## 1.0.4 — Party detection

Mostly about spotting stacks properly.

### Added

- 4- and 5-stacks now show up as one group instead of a pile of loose pairs.
  Handy in Swiftplay and TDM, where big parties are actually allowed.

### Changed

- Much better at working out who queued together, and a lot less likely to make
  it up. It used to see "A plays with B" and "B plays with C" and decide all
  three were a stack. Often they weren't.
- Stats now come from whatever mode you're in. Sitting in a Swiftplay lobby
  while it read your competitive history was never going to tell you much.
- Dropped the STREAMER tag on hidden players. The name already shows up in
  italics as their agent, so the tag wasn't earning its space.

### Fixed

- Friends in your own party with streamer mode on were showing as "Unknown".
  They don't anymore — your own client already knows who they are.

---

## 1.0.3 — Window management

SpikeSight stopped being "a web page served on localhost" and started behaving
like a normal Windows application.

### Added

- **Its own window** instead of a tab in whatever browser you had open.
  Chromeless, its own taskbar entry, its own icon.
- **Single instance.** Opening SpikeSight while it is already running brings the
  existing window back rather than starting a second copy.
- **Minimize to the notification area** *(off by default)* — minimizing hides
  the window instead of leaving a taskbar button.
- **Close to the notification area** *(off by default)* — closing the window
  leaves SpikeSight running so it keeps following your matches. Quit it from the
  tray menu.
- **Start with Windows** *(off by default)* — one entry under your own user
  account. No admin rights, no service, no scheduled task, and Windows' own
  Startup Apps screen can disable it.
- **Tray icon** with Open, the three toggles, and Quit. It appears only once one
  of the tray options is switched on.
- **Settings panel** behind the gear icon, alongside the existing diagnostics.
- **Create desktop shortcut**, from that settings panel. It replaces the loose
  batch file that used to ship in the zip - a stray `.bat` that shells out to
  PowerShell looks exactly like a malware dropper to antivirus, which held the
  file open and made the folder awkward to delete.
- **Log file** at `%LOCALAPPDATA%\SpikeSight\spikesight.log`, so a packaged build
  with no console can still say what went wrong.

### Changed

- Startup failures now appear in a dialog box rather than vanishing with the
  window.

### Fixed

- The window could open before the server was actually answering.
- Turning the tray options off while the window was closed left SpikeSight
  running with nothing on screen and no way to reach or quit it.

---

## 1.0.2 — New UI

### Added

- **Agent portraits** on the scoreboard instead of agent names.
- **Adjustable density** — Compact, Comfortable or Large — scaling row height,
  column widths and type together.
- **Every column explains itself.** Hover any header for what it actually
  measures. `K/D` in particular is recent form, not lifetime and not the
  current act, which is what `Act W-L` shows.
- Column headers for the History and Encounters tables, which previously had
  rows with nothing labeling them.
- **New fonts that aren't ugly!**

### Changed

- Peak and previous act now stack the rank over the act it happened in, and are
  centered.
- The smurf badge reads `SMURF 79/100` and `smurf 30/100`, so it parses as a
  score rather than a count of something. Hovering gives the whole calculation.
- Party chips enlarged to sit properly alongside the agent portraits.
- Status badges and row actions split into separate columns, so a busy row can
  no longer push the flag button off screen.

### Fixed

- The RR column wrapped onto two lines when a match included a rank change.

---

## 1.0.1 — Encounters and match history

### Added

- **History tab** — your recent matches with the RR gained or lost on each,
  the rank you landed on, round score, K/D/A, K/D and ACS, plus a rating
  sparkline and rolled-up W-L, win rate, K/D, ACS and net RR.
- **Encounters tab** — every player you have actually played a match with: how
  many times, how many of those were against you rather than with you, when you
  last met and what they were playing. Searchable, sortable, filterable to
  flagged players.
- An `×3` badge on the lobby row, so a repeat offender is obvious without
  leaving the scoreboard.
- **One-click tracker.gg links** for any player with a visible riot ID. Players
  in Streamer Mode get no link.

### Changed

- A roster is recorded only once the match ends. Agent select is a lobby you
  can still dodge, and a lobby you dodged is not an encounter.
- The board stays on screen after a match ends rather than clearing.

---

## 1.0.0 — Initial release

- The first one.

### Privacy and Terms of Service

- Read-only throughout. No code path can queue, dodge, lock an agent, send a
  message, join a party, or modify any game state.
- Riot's per-player privacy flags are enforced in the backend, before data
  reaches the browser: Streamer Mode, hidden account level, hidden act rank
  badge and anonymized leaderboard placement.
- Conservative shared rate limiting, aggressive caching of immutable data, and
  a local presence heartbeat so Riot's servers are only contacted when your
  match state actually changes.
- Everything stays on your machine. No telemetry, no account, no upload path.
