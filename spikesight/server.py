"""FastAPI application: local HTTP + WebSocket transport for the UI.

The server binds to 127.0.0.1 by default. Nothing is exposed to the network,
and no data leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import (
    Body,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, paths
from . import autostart, changelog, perfcheck, shortcut
from .config import WRITABLE_SETTINGS, Config, load_config, save_user_settings
from .notes import SEVERITIES, SUGGESTED_TAGS
from .poller import Poller

log = logging.getLogger(__name__)


class RevalidatingStatic(StaticFiles):
    """Serve the UI with ``Cache-Control: no-cache``.

    Without it the browser heuristically caches styles.css and app.js, so an
    edited stylesheet silently keeps rendering the old layout until a hard
    refresh. Everything here is on loopback, so revalidating costs a 304 and
    removes that entire class of "I refreshed and nothing changed" bug.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Hub:
    """Fan-out of poller events to every connected browser tab."""

    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()
        # The overlay listens too, but it is not "the window": the app must
        # still shut down when the main window closes, overlay or not.
        self._passive: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, socket: WebSocket, passive: bool = False) -> None:
        async with self._lock:
            self._sockets.add(socket)
            if passive:
                self._passive.add(socket)

    async def discard(self, socket: WebSocket) -> None:
        async with self._lock:
            self._sockets.discard(socket)
            self._passive.discard(socket)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            targets = list(self._sockets)
        dead = []
        for socket in targets:
            try:
                await socket.send_text(payload)
            except Exception:  # noqa: BLE001 - a closed tab is not an error
                dead.append(socket)
        if dead:
            async with self._lock:
                for socket in dead:
                    self._sockets.discard(socket)
                    self._passive.discard(socket)

    @property
    def count(self) -> int:
        """Connected main windows. The overlay is not counted."""
        return len(self._sockets - self._passive)


def create_app(cfg: Config | None = None, demo: bool = False) -> FastAPI:
    cfg = cfg or load_config()
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if demo:
            from .demo import DemoPoller

            poller = DemoPoller(cfg, hub.broadcast)
        else:
            poller = Poller(cfg, hub.broadcast)
        app.state.poller = poller
        app.state.cfg = cfg
        # The desktop thread uses this to push messages to the pages.
        app.state.loop = asyncio.get_running_loop()
        poller.start()
        try:
            yield
        finally:
            await poller.stop()

    app = FastAPI(title="SpikeSight", version=__version__, lifespan=lifespan)
    # The launcher watches this to decide when the UI has gone away. It has to
    # be attached before startup, not inside the lifespan, so it is readable
    # the moment create_app returns.
    app.state.hub = hub
    # Written by the overlay page, read by the desktop session's loop.
    app.state.overlay_metrics = None
    # Move mode, and the window it moves. The window is attached by the
    # desktop session; without one (--no-window, tests) moving is refused.
    app.state.overlay_edit = False
    app.state.overlay_window = None
    app.state.loop = None

    if paths.WEB_DIR.exists():
        app.mount(
            "/static", RevalidatingStatic(directory=str(paths.WEB_DIR)), name="static"
        )

    # -- pages --------------------------------------------------------------

    @app.get("/")
    async def index():
        index_path = paths.WEB_DIR / "index.html"
        if not index_path.exists():
            return JSONResponse({"error": "web/index.html is missing"}, status_code=500)
        return FileResponse(str(index_path), headers={"Cache-Control": "no-cache"})

    @app.get("/overlay")
    async def overlay_page():
        page = paths.WEB_DIR / "overlay.html"
        if not page.exists():
            return JSONResponse({"error": "web/overlay.html is missing"}, status_code=500)
        return FileResponse(str(page), headers={"Cache-Control": "no-cache"})

    @app.get("/api/overlay/agents")
    async def overlay_agents():
        poller = app.state.poller
        if not hasattr(poller, "agent_pool"):
            return {"ready": False}
        try:
            return await poller.agent_pool()
        except Exception as exc:  # noqa: BLE001 - a cosmetic panel, never fatal
            log.warning("Agent pool failed: %s", exc)
            return {"ready": False}

    async def _set_overlay_edit(on: bool) -> None:
        app.state.overlay_edit = on
        await hub.broadcast({"type": "overlay-edit", "on": on})

    def _save_overlay_position(position) -> None:
        x, y = position if position else (None, None)
        cfg.set("overlay.x", x)
        cfg.set("overlay.y", y)
        save_user_settings({"overlay.x": x, "overlay.y": y})

    @app.get("/api/overlay/edit")
    async def overlay_edit_state():
        return {
            "on": bool(app.state.overlay_edit),
            "enabled": bool(cfg.get("overlay.enabled")),
            "available": app.state.overlay_window is not None,
        }

    @app.post("/api/overlay/edit")
    async def overlay_edit(payload: dict = Body(...)):
        """Move mode: the overlay shows and takes the mouse until Done."""
        on = bool(payload.get("on"))
        if on and not cfg.get("overlay.enabled"):
            raise HTTPException(409, "Turn the overlay on first.")
        if on and app.state.overlay_window is None:
            raise HTTPException(409, "The overlay only runs in the SpikeSight window.")
        await _set_overlay_edit(on)
        return {"on": on}

    @app.post("/api/overlay/drag")
    async def overlay_drag(payload: dict = Body(...)):
        window = app.state.overlay_window
        if window is None or not app.state.overlay_edit:
            raise HTTPException(409, "Not in move mode.")
        phase = str(payload.get("phase") or "")
        if phase == "start":
            return {"ok": window.begin_drag()}
        try:
            dx = float(payload.get("dx") or 0)
            dy = float(payload.get("dy") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "bad offsets") from exc
        window.drag(dx, dy)
        if phase == "end":
            position = window.end_drag()
            if position:
                try:
                    _save_overlay_position(position)
                except (OSError, KeyError, ValueError) as exc:
                    raise HTTPException(500, f"could not save: {exc}") from exc
        return {"ok": True}

    @app.post("/api/overlay/reset")
    async def overlay_reset():
        """Forget the dragged spot and dock to the chosen side again."""
        _save_overlay_position(None)
        return {"ok": True}

    @app.post("/api/overlay/metrics")
    async def overlay_metrics(payload: dict = Body(...)):
        """The overlay page's size, so its window can be fitted around it."""
        try:
            metrics = {
                key: float(payload[key])
                for key in ("contentHeight", "outerWidth", "innerWidth",
                            "outerHeight", "innerHeight")
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, "bad metrics") from exc
        app.state.overlay_metrics = metrics
        return {"ok": True}

    # -- live feed ----------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(socket: WebSocket, role: str = ""):
        await socket.accept()
        await hub.add(socket, passive=(role == "overlay"))
        poller: Poller = app.state.poller
        try:
            await socket.send_text(
                json.dumps({"type": "snapshot", "snapshot": poller.snapshot}, default=str)
            )
            while True:
                # The UI never sends commands; this just detects disconnects.
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            await hub.discard(socket)

    # -- state --------------------------------------------------------------

    @app.get("/api/state")
    async def get_state():
        poller: Poller = app.state.poller
        return {"snapshot": poller.snapshot, "connection": poller.connection}

    @app.post("/api/refresh")
    async def refresh():
        poller: Poller = app.state.poller
        poller.request_refresh()
        return {"ok": True}

    @app.get("/api/meta")
    async def meta():
        return {
            "version": __version__,
            "severities": SEVERITIES,
            "suggestedTags": SUGGESTED_TAGS,
            "matchHistoryDepth": cfg.get("stats.match_history_depth", 5),
            "statsQueue": cfg.get("stats.queue", "competitive"),
            "dataDir": str(paths.DATA_DIR),
            "configFile": str(paths.CONFIG_FILE),
        }

    @app.post("/api/desktop-shortcut")
    async def make_desktop_shortcut():
        try:
            created = shortcut.create_desktop_shortcut()
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True, "path": str(created)}

    @app.get("/api/changelog")
    async def changelog_endpoint():
        return {"version": __version__, "releases": changelog.load()}

    @app.get("/api/diagnostics")
    async def diagnostics():
        poller = app.state.poller
        remote = poller.remote
        return {
            "version": __version__,
            "connection": poller.connection,
            "settings": {
                "deepStats": cfg.get("stats.enable_deep_stats"),
                "smurfDetection": cfg.get("smurf.enabled"),
                "overlay": cfg.get("overlay.enabled"),
            },
            "requestsSent": poller.limiter.total_requests,
            "rateLimit": {
                "perSecond": cfg.get("network.requests_per_second"),
                "burst": cfg.get("network.burst"),
                "concurrency": cfg.get("network.max_concurrency"),
            },
            "cache": remote.cache.stats() if remote else {},
            "cachedMatches": (
                poller.builder.cached_matches if poller.builder else 0
            ),
            "notes": await poller.notes.stats(),
            "performance": perfcheck.summary(),
            "clients": hub.count,
            "paths": {
                "lockfile": str(paths.RIOT_LOCKFILE),
                "gameLog": str(paths.RIOT_SHOOTER_LOG),
                "data": str(paths.DATA_DIR),
                "appLog": str(paths.DATA_DIR / "spikesight.log"),
            },
        }

    # -- history and encounters ---------------------------------------------

    @app.get("/api/history")
    async def history(count: int = 0, refresh: bool = False):
        poller = app.state.poller
        if not hasattr(poller, "history"):
            raise HTTPException(503, "History is not available in demo mode.")
        wanted = count or int(cfg.get("history.default_count", 15))
        try:
            return await poller.history(count=wanted, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not fatal
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/encounters")
    async def encounters(
        query: str = Query("", alias="q"),
        limit: int = 300,
        flagged: bool = False,
        sort: str = "count",
    ):
        poller = app.state.poller
        rows = await poller.notes.encounter_index(
            query=query, limit=max(1, min(limit, 1000)), flagged_only=flagged, sort=sort
        )
        return {"players": rows, "storage": await poller.notes.stats()}

    # -- settings -----------------------------------------------------------

    #: UI name -> config path. Keeping the mapping here means the browser can
    #: never write an arbitrary config key.
    SETTING_PATHS = {
        "deepStats": "stats.enable_deep_stats",
        "smurfDetection": "smurf.enabled",
        "minimizeToTray": "tray.minimize_to_tray",
        "closeToTray": "tray.close_to_tray",
        "startWithWindows": "app.start_with_windows",
        "minimizeDuringMatch": "app.minimize_during_match",
        "theme": "ui.theme",
        "overlay": "overlay.enabled",
        "overlaySide": "overlay.side",
    }

    def _current_settings() -> dict:
        values = {}
        for name, path in SETTING_PATHS.items():
            kind = WRITABLE_SETTINGS[path]
            values[name] = bool(cfg.get(path)) if kind is bool else cfg.get(path)
        # The registry is the truth for this one; the setting is just a request.
        values["startWithWindows"] = autostart.is_enabled()
        values["overlayMoved"] = cfg.get("overlay.x") is not None
        return values

    @app.get("/api/settings")
    async def read_settings():
        return {"settings": _current_settings()}

    @app.post("/api/settings")
    async def update_settings(payload: dict = Body(...)):
        poller = app.state.poller
        updates: dict = {}
        for name, path in SETTING_PATHS.items():
            if name in payload:
                try:
                    value = WRITABLE_SETTINGS[path](payload[name])
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, f"{name}: {exc}") from exc
                updates[path] = value
        # Picking a side is a request to dock there, so it drops any spot the
        # overlay was dragged to.
        if "overlay.side" in updates:
            updates["overlay.x"] = None
            updates["overlay.y"] = None
        for path, value in updates.items():
            cfg.set(path, value)

        if not updates:
            raise HTTPException(400, "no known settings in request")

        if "app.start_with_windows" in updates:
            actual = autostart.sync(updates["app.start_with_windows"])
            if actual != updates["app.start_with_windows"]:
                log.warning("Windows refused the startup entry.")
                cfg.set("app.start_with_windows", actual)
                updates["app.start_with_windows"] = actual

        try:
            save_user_settings(updates)
        except (OSError, KeyError) as exc:
            raise HTTPException(500, f"could not save settings: {exc}") from exc

        # Rank/level redaction changes what the scoreboard is allowed to show,
        # so the current snapshot has to be rebuilt rather than reused.
        if {"stats.enable_deep_stats", "smurf.enabled"} & set(updates):
            poller.request_refresh()
        elif "ui.theme" in updates:
            await poller.republish()
        if updates.get("overlay.enabled") is False and app.state.overlay_edit:
            await _set_overlay_edit(False)

        return {"ok": True, "settings": _current_settings()}

    # -- notes --------------------------------------------------------------

    @app.get("/api/notes")
    async def list_notes(query: str = Query("", alias="q"), limit: int = 200):
        poller: Poller = app.state.poller
        return {"notes": await poller.notes.flagged(query, limit)}

    @app.post("/api/notes")
    async def create_note(payload: dict = Body(...)):
        poller: Poller = app.state.poller
        puuid = str(payload.get("puuid") or "").strip()
        if not puuid:
            raise HTTPException(400, "puuid is required")
        severity = int(payload.get("severity") or 2)
        if severity not in SEVERITIES:
            raise HTTPException(400, "severity must be 1, 2 or 3")
        tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
        body = str(payload.get("body") or "").strip()[:2000]
        note = await poller.notes.add_note(
            puuid, severity, tags, body, payload.get("matchId")
        )
        await poller.republish()
        poller.request_refresh()
        return {"note": note}

    @app.patch("/api/notes/{note_id}")
    async def edit_note(note_id: int, payload: dict = Body(...)):
        poller: Poller = app.state.poller
        severity = int(payload.get("severity") or 2)
        if severity not in SEVERITIES:
            raise HTTPException(400, "severity must be 1, 2 or 3")
        tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
        body = str(payload.get("body") or "").strip()[:2000]
        note = await poller.notes.update_note(note_id, severity, tags, body)
        if note is None:
            raise HTTPException(404, "note not found")
        poller.request_refresh()
        return {"note": note}

    @app.delete("/api/notes/{note_id}")
    async def remove_note(note_id: int):
        poller: Poller = app.state.poller
        if not await poller.notes.delete_note(note_id):
            raise HTTPException(404, "note not found")
        poller.request_refresh()
        return {"ok": True}

    @app.get("/api/player/{puuid}")
    async def player_profile(puuid: str):
        poller: Poller = app.state.poller
        return await poller.notes.player_profile(puuid)

    @app.get("/api/notes/export")
    async def export_notes():
        poller: Poller = app.state.poller
        return await poller.notes.export()

    @app.post("/api/notes/import")
    async def import_notes(payload: dict = Body(...)):
        poller: Poller = app.state.poller
        counts = await poller.notes.import_payload(payload)
        poller.request_refresh()
        return {"ok": True, "imported": counts}

    return app
