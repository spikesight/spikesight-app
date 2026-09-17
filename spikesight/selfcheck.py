"""``python -m spikesight.selfcheck`` - verify the local setup end to end.

Runs the same reads the app does, in order, and prints what worked. Safe to run
with VALORANT sitting in the menus; it will simply report that no match is
active. No secrets are printed.
"""

from __future__ import annotations

import asyncio
import sys

from . import content as content_module
from . import gamedata, paths, ranks
from .config import load_config
from .errors import SpikeSightError
from .local_api import LocalClient, read_lockfile
from .ratelimit import RateLimiter
from .riot_remote import RemoteClient

OK = "  [ ok ] "
BAD = "  [FAIL] "
INFO = "  [ .. ] "


async def run() -> int:
    cfg = load_config()
    failures = 0

    print("\nSpikeSight self-check\n" + "=" * 60)
    print(f"{INFO}Config file: {paths.CONFIG_FILE}")
    print(f"{INFO}Data dir:    {paths.DATA_DIR}")

    # 1. lockfile ---------------------------------------------------------
    try:
        lockfile = read_lockfile()
        print(f"{OK}Lockfile found (port {lockfile.port}, {lockfile.protocol}).")
    except SpikeSightError as exc:
        print(f"{BAD}{exc}")
        print("\n  Start the Riot Client and run this again.\n")
        return 1

    local = LocalClient()
    try:
        # 2. credentials --------------------------------------------------
        try:
            creds = await local.credentials()
            print(f"{OK}Local credentials read for puuid {creds.puuid[:8]}...")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}Could not read entitlements token: {exc}")
            return 1

        # 3. endpoints ----------------------------------------------------
        try:
            endpoints = await gamedata.resolve_endpoints(local, cfg)
            print(f"{OK}Region/shard: {endpoints.region}/{endpoints.shard}")
            print(f"{OK}Client version: {endpoints.client_version}")
            print(f"{INFO}pd  -> {endpoints.pd}")
            print(f"{INFO}glz -> {endpoints.glz}")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}{exc}")
            return 1

        limiter = RateLimiter(
            float(cfg.get("network.requests_per_second", 4.0)),
            int(cfg.get("network.burst", 8)),
            int(cfg.get("network.max_concurrency", 3)),
        )
        remote = RemoteClient(local, endpoints, limiter,
                              timeout=float(cfg.get("network.timeout", 12.0)))

        try:
            # 4. presence -------------------------------------------------
            try:
                presence = await local.self_presence(creds.puuid)
                if presence:
                    print(f"{OK}Presence: sessionLoopState={presence.session_loop_state or '?'}"
                          f" queue={presence.queue_id or '-'}")
                else:
                    print(f"{INFO}No VALORANT presence yet (is the game running?).")
            except Exception as exc:  # noqa: BLE001
                print(f"{BAD}Presence read failed: {exc}")
                failures += 1

            # 5. content --------------------------------------------------
            game_content = await content_module.load_content(remote, cfg)
            act = game_content.current_act()
            if game_content.agents:
                print(f"{OK}Content loaded from {game_content.source}: "
                      f"{len(game_content.agents)} agents, {len(game_content.acts)} acts.")
                print(f"{INFO}Current act: {act.full if act else 'unknown'}")
            else:
                print(f"{BAD}Content unavailable; names will show as raw ids.")
                failures += 1

            # 6. own MMR --------------------------------------------------
            try:
                mmr = await remote.mmr(creds.puuid)
                if not isinstance(mmr, dict):
                    raise RuntimeError("empty response")
                latest = mmr.get("LatestCompetitiveUpdate") or {}
                tier = ranks.tier(latest.get("TierAfterUpdate"))
                print(f"{OK}MMR endpoint reachable. Last competitive result: "
                      f"{tier.name} @ {latest.get('RankedRatingAfterUpdate', 0)} RR")
                if mmr.get("IsActRankBadgeHidden"):
                    print(f"{INFO}Your act rank badge is hidden - SpikeSight respects that.")
            except Exception as exc:  # noqa: BLE001
                print(f"{BAD}MMR request failed: {exc}")
                failures += 1

            # 7. live match ----------------------------------------------
            try:
                core = await remote.coregame_player(creds.puuid)
                pre = await remote.pregame_player(creds.puuid)
                if isinstance(core, dict) and core.get("MatchID"):
                    print(f"{OK}You are in a live match ({core['MatchID'][:8]}...).")
                elif isinstance(pre, dict) and pre.get("MatchID"):
                    print(f"{OK}You are in agent select ({pre['MatchID'][:8]}...).")
                else:
                    print(f"{INFO}Not in a match right now - that is fine.")
            except Exception as exc:  # noqa: BLE001
                print(f"{BAD}Match-state probe failed: {exc}")
                failures += 1

            print(f"{INFO}Requests sent during this check: {limiter.total_requests}")
        finally:
            await remote.aclose()
    finally:
        await local.aclose()

    print("=" * 60)
    if failures:
        print(f"  {failures} check(s) failed. See messages above.\n")
    else:
        print("  Everything looks good. Run start.bat to launch the UI.\n")
    return 1 if failures else 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
