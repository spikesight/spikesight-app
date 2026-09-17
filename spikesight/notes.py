"""Local player notes and encounter history.

A private, local record of who was a problem, surfaced the instant they show
up in a lobby again - while the dodge window is still open.

Everything lives in one SQLite file under %LOCALAPPDATA%\\SpikeSight. Nothing is
uploaded anywhere, and notes are keyed on puuid so they still work for players
who have Streamer Mode on - without ever recording who those players are. They
are shown by the short code from :func:`spikesight.privacy.hidden_tag` instead,
which is what makes one of them distinguishable from the next.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .privacy import hidden_tag

log = logging.getLogger(__name__)

SEVERITIES = {
    1: {"key": "watch", "label": "Watch", "color": "#d8a13a"},
    2: {"key": "avoid", "label": "Avoid", "color": "#e0722f"},
    3: {"key": "dodge", "label": "Dodge", "color": "#d13c4b"},
}

SUGGESTED_TAGS = [
    "thrower", "troll", "toxic", "afk", "griefer", "instalock",
    "rage quit", "no comms", "smurf", "sandbagger", "good teammate",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid       TEXT PRIMARY KEY,
    last_name   TEXT,
    last_tag    TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    encounters  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    puuid       TEXT NOT NULL,
    severity    INTEGER NOT NULL DEFAULT 2,
    tags        TEXT NOT NULL DEFAULT '[]',
    body        TEXT NOT NULL DEFAULT '',
    match_id    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_puuid ON notes(puuid);

CREATE TABLE IF NOT EXISTS encounters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    puuid       TEXT NOT NULL,
    match_id    TEXT,
    seen_at     TEXT NOT NULL,
    relation    TEXT NOT NULL,
    agent       TEXT,
    map_name    TEXT,
    queue       TEXT
);
CREATE INDEX IF NOT EXISTS idx_enc_puuid ON encounters(puuid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_enc_unique ON encounters(puuid, match_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Note:
    id: int
    puuid: str
    severity: int
    tags: list[str]
    body: str
    match_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        meta = SEVERITIES.get(self.severity, SEVERITIES[2])
        return {
            "id": self.id,
            "puuid": self.puuid,
            "severity": self.severity,
            "severityKey": meta["key"],
            "severityLabel": meta["label"],
            "severityColor": meta["color"],
            "tags": self.tags,
            "body": self.body,
            "matchId": self.match_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def _is_healthy(connection: sqlite3.Connection) -> bool:
    try:
        return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        # A badly damaged file throws here rather than returning a verdict.
        return False


def _readable_rows(connection: sqlite3.Connection, table: str):
    """Every row of ``table`` that survives, skipping the pages that do not."""
    ids = []
    try:
        cursor = connection.execute(f"SELECT rowid FROM {table}")
        while True:
            try:
                row = cursor.fetchone()
            except sqlite3.DatabaseError:
                break
            if row is None:
                break
            ids.append(row[0])
    except sqlite3.DatabaseError:
        return
    for rowid in ids:
        try:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE rowid = ?", (rowid,)
            ).fetchone()
        except sqlite3.DatabaseError:
            continue
        if row is not None:
            yield dict(row)


class NotesStore:
    """Thread-confined SQLite wrapper; all public methods are async."""

    def __init__(self, db_path=None) -> None:
        paths.ensure_data_dirs()
        self._path = str(db_path or paths.NOTES_DB)
        # Every statement runs on this one thread, one at a time. An asyncio
        # lock alone is not enough: when the awaiting task is cancelled (a new
        # lobby replacing the old scan, or shutdown) the lock is released while
        # the query is still running on its worker thread, and the next query
        # would then share the connection with it mid-write.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notes-db")
        self._guard = threading.Lock()
        self._closed = False
        self._connection = self._open()
        self._make_backup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # WAL survives an ungraceful exit (closing the console window, a crash,
        # a power cut) without losing committed writes. Every note and every
        # encounter is committed the moment it is made, so nothing lives only
        # in memory between sessions.
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        except sqlite3.DatabaseError:
            pass
        connection.executescript(_SCHEMA)
        connection.commit()
        return connection

    def _open(self) -> sqlite3.Connection:
        """Connect, rebuilding the file first if it has gone bad.

        A damaged SQLite file does not announce itself. The tables you happen
        to touch keep working while others throw, so everything looks fine
        right up until your flags are the thing that will not load. Checking
        once at startup is cheap, and turns silent loss into a bad page or two.
        """
        connection = self._connect()
        if _is_healthy(connection):
            return connection
        connection.close()
        log.error("Notes database is damaged; rebuilding from what is readable")
        self._recover()
        return self._connect()

    def _recover(self) -> None:
        """Set the damaged file aside and salvage every row still readable.

        Corruption is usually confined to a few pages, so most rows survive.
        The original is kept rather than deleted - it is the only copy of
        whatever could not be read back.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        live = Path(self._path)
        quarantine = live.with_name(f"{live.name}.corrupt-{stamp}")
        try:
            live.replace(quarantine)
            # The -wal sidecar is not a scratch file: in WAL mode the most
            # recent commits live there until a checkpoint folds them in.
            # Deleting it here would throw away the newest notes, which are
            # exactly the ones worth saving. It travels with its database, and
            # SQLite finds it again by name.
            for suffix in ("-wal", "-shm"):
                stray = live.with_name(live.name + suffix)
                if stray.exists():
                    stray.replace(quarantine.with_name(quarantine.name + suffix))
        except OSError:
            log.exception("Could not set the damaged database aside")
            return

        rescued = self._connect()
        try:
            source = sqlite3.connect(f"file:{quarantine.as_posix()}?mode=ro", uri=True)
            source.row_factory = sqlite3.Row
        except sqlite3.DatabaseError:
            log.error("The damaged database could not be opened at all")
            rescued.close()
            return

        counts = {}
        for table in ("players", "notes", "encounters"):
            saved = 0
            for row in _readable_rows(source, table):
                # A damaged page can hand back a row whose columns have slid
                # out of place - an agent name in the encounter count, a match
                # id where the tag should be. Every table here is keyed on a
                # puuid, so a row without one is wreckage, not a player.
                if not row.get("puuid"):
                    continue
                columns = ",".join(row)
                marks = ",".join("?" for _ in row)
                try:
                    rescued.execute(
                        f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({marks})",
                        list(row.values()),
                    )
                    saved += 1
                except sqlite3.DatabaseError:
                    pass
            counts[table] = saved
        rescued.commit()
        rescued.close()
        source.close()
        log.warning(
            "Recovered %s players, %s notes and %s encounters. The damaged file "
            "is kept at %s",
            counts.get("players", 0), counts.get("notes", 0),
            counts.get("encounters", 0), quarantine,
        )

    def _make_backup(self) -> None:
        """Keep one rolling copy beside the live file, refreshed at startup.

        This has to go through SQLite's own backup API. Copying the file with
        the filesystem looks like it works and does not: in WAL mode part of
        the committed data lives in the ``-wal`` sidecar, so the copy is a
        half-written database that only reveals itself when you need it.
        """
        source = Path(self._path)
        destination = source.with_name(source.name + ".backup")
        if not _is_healthy(self._connection):
            log.error("Notes database is damaged; keeping the previous backup as it is")
            return
        try:
            if not source.exists() or source.stat().st_size == 0:
                return
            target = sqlite3.connect(str(destination))
            try:
                self._connection.backup(target)
            finally:
                target.close()
        except (OSError, sqlite3.DatabaseError):
            log.exception("Could not refresh the notes backup")

    def close(self) -> None:
        """Close after any statement already queued or running has finished."""
        if self._closed:
            return
        self._closed = True

        def finish():
            with self._guard:
                self._connection.close()

        try:
            self._executor.submit(finish).result(timeout=30)
        except Exception:  # noqa: BLE001 - shutting down regardless
            log.exception("Notes database did not close cleanly")
        self._executor.shutdown(wait=True)

    def _locked(self, fn, *args):
        # Work queued before close() still runs; anything after it meets a
        # closed connection, which sqlite3 refuses cleanly.
        with self._guard:
            return fn(*args)

    async def _run(self, fn, *args):
        if self._closed:
            raise sqlite3.ProgrammingError("notes database is closed")
        loop = asyncio.get_running_loop()
        # Cancelling the await does not cancel the statement; it finishes on
        # the executor thread before the next one starts.
        return await loop.run_in_executor(self._executor, self._locked, fn, *args)

    # -- players ------------------------------------------------------------

    def _touch_player(self, puuid: str, name: str | None, tag: str | None) -> None:
        now = _now()
        cursor = self._connection.execute(
            "SELECT puuid FROM players WHERE puuid = ?", (puuid,)
        )
        if cursor.fetchone() is None:
            self._connection.execute(
                "INSERT INTO players (puuid, last_name, last_tag, first_seen, last_seen,"
                " encounters) VALUES (?, ?, ?, ?, ?, 0)",
                (puuid, name, tag, now, now),
            )
        else:
            # Only overwrite the alias when we are actually allowed to know it.
            if name:
                self._connection.execute(
                    "UPDATE players SET last_name = ?, last_tag = ?, last_seen = ?"
                    " WHERE puuid = ?",
                    (name, tag, now, puuid),
                )
            else:
                self._connection.execute(
                    "UPDATE players SET last_seen = ? WHERE puuid = ?", (now, puuid)
                )
        self._connection.commit()

    async def touch_player(self, puuid: str, name: str | None, tag: str | None) -> None:
        await self._run(self._touch_player, puuid, name, tag)

    # -- notes --------------------------------------------------------------

    def _rows_to_notes(self, rows) -> list[Note]:
        out = []
        for row in rows:
            try:
                tags = json.loads(row["tags"])
            except (TypeError, json.JSONDecodeError):
                tags = []
            out.append(
                Note(
                    id=row["id"],
                    puuid=row["puuid"],
                    severity=int(row["severity"]),
                    tags=tags if isinstance(tags, list) else [],
                    body=row["body"] or "",
                    match_id=row["match_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return out

    def _notes_for(self, puuids: tuple[str, ...]) -> dict[str, list[dict]]:
        if not puuids:
            return {}
        placeholders = ",".join("?" for _ in puuids)
        rows = self._connection.execute(
            f"SELECT * FROM notes WHERE puuid IN ({placeholders})"
            " ORDER BY severity DESC, updated_at DESC",
            puuids,
        ).fetchall()
        grouped: dict[str, list[dict]] = {}
        for note in self._rows_to_notes(rows):
            grouped.setdefault(note.puuid, []).append(note.to_dict())
        return grouped

    async def notes_for(self, puuids: list[str]) -> dict[str, list[dict]]:
        return await self._run(self._notes_for, tuple(dict.fromkeys(puuids)))

    def _add_note(
        self, puuid: str, severity: int, tags: list[str], body: str, match_id: str | None
    ) -> dict:
        now = _now()
        cursor = self._connection.execute(
            "INSERT INTO notes (puuid, severity, tags, body, match_id, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (puuid, int(severity), json.dumps(tags), body, match_id, now, now),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._rows_to_notes([row])[0].to_dict()

    async def add_note(
        self,
        puuid: str,
        severity: int,
        tags: list[str],
        body: str,
        match_id: str | None = None,
    ) -> dict:
        return await self._run(self._add_note, puuid, severity, tags, body, match_id)

    def _update_note(self, note_id: int, severity: int, tags: list[str], body: str) -> dict | None:
        self._connection.execute(
            "UPDATE notes SET severity = ?, tags = ?, body = ?, updated_at = ?"
            " WHERE id = ?",
            (int(severity), json.dumps(tags), body, _now(), note_id),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return self._rows_to_notes([row])[0].to_dict() if row else None

    async def update_note(
        self, note_id: int, severity: int, tags: list[str], body: str
    ) -> dict | None:
        return await self._run(self._update_note, note_id, severity, tags, body)

    def _delete_note(self, note_id: int) -> bool:
        cursor = self._connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._connection.commit()
        return cursor.rowcount > 0

    async def delete_note(self, note_id: int) -> bool:
        return await self._run(self._delete_note, note_id)

    # -- encounters ---------------------------------------------------------

    def _record_encounter(
        self,
        puuid: str,
        match_id: str | None,
        relation: str,
        agent: str | None,
        map_name: str | None,
        queue: str | None,
    ) -> bool:
        try:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO encounters (puuid, match_id, seen_at, relation,"
                " agent, map_name, queue) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (puuid, match_id, _now(), relation, agent, map_name, queue),
            )
            if cursor.rowcount:
                self._connection.execute(
                    "UPDATE players SET encounters = encounters + 1, last_seen = ?"
                    " WHERE puuid = ?",
                    (_now(), puuid),
                )
            self._connection.commit()
            return bool(cursor.rowcount)
        except sqlite3.DatabaseError:
            return False

    async def record_encounter(
        self,
        puuid: str,
        match_id: str | None,
        relation: str,
        agent: str | None = None,
        map_name: str | None = None,
        queue: str | None = None,
    ) -> bool:
        return await self._run(
            self._record_encounter, puuid, match_id, relation, agent, map_name, queue
        )

    def _encounter_summary(self, puuids: tuple[str, ...]) -> dict[str, dict]:
        if not puuids:
            return {}
        placeholders = ",".join("?" for _ in puuids)
        rows = self._connection.execute(
            "SELECT puuid,"
            "       COUNT(*) AS total,"
            "       SUM(CASE WHEN relation = 'ally'  THEN 1 ELSE 0 END) AS allies,"
            "       SUM(CASE WHEN relation = 'enemy' THEN 1 ELSE 0 END) AS enemies,"
            "       MIN(seen_at) AS first_seen,"
            "       MAX(seen_at) AS last_seen"
            f" FROM encounters WHERE puuid IN ({placeholders}) GROUP BY puuid",
            puuids,
        ).fetchall()
        return {
            row["puuid"]: {
                "count": int(row["total"] or 0),
                "ally": int(row["allies"] or 0),
                "enemy": int(row["enemies"] or 0),
                "firstSeen": row["first_seen"],
                "lastSeen": row["last_seen"],
            }
            for row in rows
        }

    async def encounter_summary(self, puuids: list[str]) -> dict[str, dict]:
        """How many times each player has been seen *before* right now."""
        return await self._run(self._encounter_summary, tuple(dict.fromkeys(puuids)))

    def _puuids_matching_code(self, query: str) -> list[str]:
        """Nameless players whose local code contains ``query``."""
        needle = query.strip().upper().lstrip("#")
        if not needle:
            return []
        rows = self._connection.execute(
            "SELECT puuid FROM players WHERE last_name IS NULL OR last_name = ''"
        ).fetchall()
        return [r["puuid"] for r in rows if needle in hidden_tag(r["puuid"])]

    def _encounter_index(
        self, query: str, limit: int, flagged_only: bool, sort: str
    ) -> list[dict]:
        order = {
            "count": "total DESC, last_seen DESC",
            "recent": "last_seen DESC",
            "name": "LOWER(COALESCE(p.last_name, 'zzz')) ASC",
            "severity": "severity DESC, total DESC",
        }.get(sort, "total DESC, last_seen DESC")

        sql = (
            "SELECT p.puuid, p.last_name, p.last_tag, p.first_seen, p.last_seen,"
            "       COUNT(e.id) AS total,"
            "       SUM(CASE WHEN e.relation = 'ally'  THEN 1 ELSE 0 END) AS allies,"
            "       SUM(CASE WHEN e.relation = 'enemy' THEN 1 ELSE 0 END) AS enemies,"
            "       COALESCE(MAX(n.severity), 0) AS severity,"
            "       COUNT(DISTINCT n.id) AS note_count,"
            "       MAX(e.map_name) AS last_map, MAX(e.agent) AS last_agent"
            " FROM players p"
            " LEFT JOIN encounters e ON e.puuid = p.puuid"
            " LEFT JOIN notes n      ON n.puuid = p.puuid"
        )
        args: list = []
        where = []
        if query:
            # A hidden player has no name to search, but they do have a code.
            # It is derived from the puuid, so matching it means resolving the
            # handful of nameless rows first and searching those in Python.
            coded = self._puuids_matching_code(query)
            clause = "(p.last_name LIKE ? OR n.body LIKE ? OR n.tags LIKE ?"
            like = f"%{query}%"
            args += [like, like, like]
            if coded:
                clause += " OR p.puuid IN (%s)" % ",".join("?" for _ in coded)
                args += coded
            where.append(clause + ")")
        if flagged_only:
            where.append("n.id IS NOT NULL")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" GROUP BY p.puuid ORDER BY {order} LIMIT ?"
        args.append(limit)

        rows = self._connection.execute(sql, args).fetchall()
        out = []
        for row in rows:
            severity = int(row["severity"] or 0)
            meta = SEVERITIES.get(severity)
            out.append(
                {
                    "puuid": row["puuid"],
                    "name": row["last_name"],
                    "tag": row["last_tag"],
                    "hiddenId": None if row["last_name"] else hidden_tag(row["puuid"]),
                    "count": int(row["total"] or 0),
                    "ally": int(row["allies"] or 0),
                    "enemy": int(row["enemies"] or 0),
                    "firstSeen": row["first_seen"],
                    "lastSeen": row["last_seen"],
                    "noteCount": int(row["note_count"] or 0),
                    "severity": severity,
                    "severityLabel": meta["label"] if meta else None,
                    "severityColor": meta["color"] if meta else None,
                    "lastMap": row["last_map"],
                    "lastAgent": row["last_agent"],
                }
            )
        return out

    async def encounter_index(
        self,
        query: str = "",
        limit: int = 300,
        flagged_only: bool = False,
        sort: str = "count",
    ) -> list[dict]:
        return await self._run(self._encounter_index, query, limit, flagged_only, sort)

    def _player_profile(self, puuid: str) -> dict:
        player = self._connection.execute(
            "SELECT * FROM players WHERE puuid = ?", (puuid,)
        ).fetchone()
        encounters = self._connection.execute(
            "SELECT * FROM encounters WHERE puuid = ? ORDER BY seen_at DESC LIMIT 50",
            (puuid,),
        ).fetchall()
        notes = self._notes_for((puuid,)).get(puuid, [])
        return {
            "puuid": puuid,
            "known": player is not None,
            "lastName": player["last_name"] if player else None,
            "lastTag": player["last_tag"] if player else None,
            "hiddenId": None if (player and player["last_name"]) else hidden_tag(puuid),
            "firstSeen": player["first_seen"] if player else None,
            "lastSeen": player["last_seen"] if player else None,
            "encounterCount": player["encounters"] if player else 0,
            "encounters": [dict(row) for row in encounters],
            "notes": notes,
        }

    async def player_profile(self, puuid: str) -> dict:
        return await self._run(self._player_profile, puuid)

    # -- library ------------------------------------------------------------

    def _flagged(self, query: str, limit: int) -> list[dict]:
        sql = (
            "SELECT n.*, p.last_name, p.last_tag, p.encounters, p.last_seen"
            " FROM notes n LEFT JOIN players p ON p.puuid = n.puuid"
        )
        args: list = []
        if query:
            like = f"%{query}%"
            clause = "p.last_name LIKE ? OR n.body LIKE ? OR n.tags LIKE ?"
            args = [like, like, like]
            coded = self._puuids_matching_code(query)
            if coded:
                clause += " OR n.puuid IN (%s)" % ",".join("?" for _ in coded)
                args += coded
            sql += f" WHERE {clause}"
        sql += " ORDER BY n.severity DESC, n.updated_at DESC LIMIT ?"
        args.append(limit)
        rows = self._connection.execute(sql, args).fetchall()
        out = []
        for row in rows:
            note = self._rows_to_notes([row])[0].to_dict()
            note["lastName"] = row["last_name"]
            note["lastTag"] = row["last_tag"]
            note["hiddenId"] = None if row["last_name"] else hidden_tag(note["puuid"])
            note["encounterCount"] = row["encounters"] or 0
            note["lastSeen"] = row["last_seen"]
            out.append(note)
        return out

    async def flagged(self, query: str = "", limit: int = 200) -> list[dict]:
        return await self._run(self._flagged, query, limit)

    def _export(self) -> dict:
        return {
            "version": 1,
            "exportedAt": _now(),
            "players": [dict(r) for r in self._connection.execute("SELECT * FROM players")],
            "notes": [dict(r) for r in self._connection.execute("SELECT * FROM notes")],
            "encounters": [
                dict(r) for r in self._connection.execute("SELECT * FROM encounters")
            ],
        }

    async def export(self) -> dict:
        return await self._run(self._export)

    def _import(self, payload: dict) -> dict:
        counts = {"players": 0, "notes": 0, "encounters": 0}
        for row in payload.get("players", []):
            self._connection.execute(
                "INSERT OR REPLACE INTO players (puuid, last_name, last_tag, first_seen,"
                " last_seen, encounters) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row.get("puuid"), row.get("last_name"), row.get("last_tag"),
                    row.get("first_seen") or _now(), row.get("last_seen") or _now(),
                    int(row.get("encounters") or 0),
                ),
            )
            counts["players"] += 1
        for row in payload.get("notes", []):
            self._connection.execute(
                "INSERT INTO notes (puuid, severity, tags, body, match_id, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.get("puuid"), int(row.get("severity") or 2),
                    row.get("tags") or "[]", row.get("body") or "",
                    row.get("match_id"), row.get("created_at") or _now(),
                    row.get("updated_at") or _now(),
                ),
            )
            counts["notes"] += 1
        for row in payload.get("encounters", []):
            self._connection.execute(
                "INSERT OR IGNORE INTO encounters (puuid, match_id, seen_at, relation,"
                " agent, map_name, queue) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.get("puuid"), row.get("match_id"), row.get("seen_at") or _now(),
                    row.get("relation") or "unknown", row.get("agent"),
                    row.get("map_name"), row.get("queue"),
                ),
            )
            counts["encounters"] += 1
        self._connection.commit()
        return counts

    async def import_payload(self, payload: dict) -> dict:
        return await self._run(self._import, payload)

    def _stats(self) -> dict:
        def scalar(sql: str) -> int:
            return int(self._connection.execute(sql).fetchone()[0])

        return {
            "players": scalar("SELECT COUNT(*) FROM players"),
            "notes": scalar("SELECT COUNT(*) FROM notes"),
            "flaggedPlayers": scalar("SELECT COUNT(DISTINCT puuid) FROM notes"),
            "encounters": scalar("SELECT COUNT(*) FROM encounters"),
            "path": self._path,
            "backup": self._path + ".backup",
        }

    async def stats(self) -> dict:
        return await self._run(self._stats)
