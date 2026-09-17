/* SpikeSight UI. Vanilla JS, no build step. */
(() => {
  "use strict";

  const PARTY_COLORS = ["#3ba7e0", "#e0a13b", "#8f5be0", "#3be08f", "#e05b8f", "#5be0d8"];
  // [class, header label, what the column actually means]. Several of these
  // are ambiguous at a glance - "K/D" especially - so every header carries its
  // own explanation on hover.
  const COLUMNS = [
    ["col-party", "", "Party color. Solid: confirmed from a live party id. "
      + "Striped: inferred from how often these players have queued together."],
    ["col-agent", "", "Agent this player has locked in."],
    ["col-name", "Player",
      "Riot ID. A name in italics means Streamer Mode is on for that player: "
      + "they are shown as their agent, and SpikeSight never requests their name."],
    ["col-rank", "Rank",
      "Current competitive rank, with RR — or the leaderboard position for "
      + "Immortal and Radiant."],
    ["col-peak", "Peak",
      "Highest rank this account has ever reached, and the act it happened in. "
      + "Hidden when the player has hidden their act rank badge."],
    ["col-prev", "Prev act",
      "Where they finished the previous act. Useful for spotting someone who "
      + "has just deranked, or is climbing back."],
    ["col-act", "Act W-L",
      "Wins and losses in the CURRENT act only, straight from Riot's act rank "
      + "data. Not lifetime, and not this session."],
    ["col-lvl", "Lvl",
      "Account level. Shows 'hidden' when the player has hidden it — SpikeSight "
      + "then leaves it out of smurf scoring rather than guessing."],
    ["col-kd", "K/D", null],   // filled in at render time; see kdHeaderHint()
    ["col-flags", "Flags",
      "Smurf score out of 100, and party size for a three-stack or bigger."],
    ["col-btn", "", "Open tracker.gg, or flag this player."],
  ];

  // History table headers: [class, label, definition]. Same contract as
  // COLUMNS above - nothing on screen should need guessing at.
  const MATCH_COLUMNS = [
    ["", "", "Result stripe: green for a win, red for a loss."],
    ["", "", "The agent you played."],
    ["", "Map", "Map, and the queue the match was played in."],
    ["", "Score", "Final round score: rounds you won versus rounds you lost."],
    ["", "K/D/A", "Your kills, deaths and assists in this match."],
    ["", "K/D", "Kills divided by deaths in this match alone."],
    ["", "ACS", "Average combat score: your score per round. Roughly 200 is "
      + "an even performance for the rank."],
    ["", "RR", "Rating won or lost, and the rank you finished the match on. "
      + "Unrated, Swiftplay and Deathmatch show no RR change."],
    ["mh-right", "Played", "When the match started, in your local time."],
  ];

  const DENSITIES = ["compact", "comfortable", "large"];
  const DEFAULT_DENSITY = "comfortable";
  const VIEWS = ["lobby", "history", "encounters"];

  const state = {
    snapshot: null,
    meta: null,
    view: "lobby",
    drawerPuuid: null,
    drawerFallback: null,
    draft: { severity: 2, tags: new Set(), body: "" },
    progress: null,
    history: null,
    encounters: null,
    overlayMoving: false,
    onOverlayMoving: null,
  };

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };

  const fmtDate = (ms) => (ms ? new Date(ms).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
  const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString() : "—");

  /* ---------------- density ---------------- */

  // Purely a viewer preference, so it lives in this browser rather than in
  // config.toml. Storage can throw in a private window; never let it break
  // the page.
  function applyDensity(name, persist) {
    const value = DENSITIES.includes(name) ? name : DEFAULT_DENSITY;
    document.documentElement.dataset.density = value;
    const select = $("densitySelect");
    if (select) select.value = value;
    if (persist) {
      try { localStorage.setItem("spikesight.density", value); } catch { /* ignore */ }
    }
  }

  function initDensity() {
    let stored = null;
    try { stored = localStorage.getItem("spikesight.density"); } catch { /* ignore */ }
    applyDensity(stored || DEFAULT_DENSITY, false);
    $("densitySelect").addEventListener("change", (event) => {
      applyDensity(event.target.value, true);
    });
  }

  /* ---------------- theme ---------------- */

  async function initTheme() {
    const select = $("themeSelect");
    const theme = window.SpikeSightTheme;
    select.value = theme.current;
    select.value = await theme.sync();
    select.addEventListener("change", async () => {
      const previous = theme.current;
      theme.apply(select.value);
      try {
        await api("/api/settings", {
          method: "POST",
          body: JSON.stringify({ theme: select.value }),
        });
      } catch (error) {
        theme.apply(previous);
        select.value = previous;
        alert(`Could not save the theme: ${error.message || error}`);
      }
    });
  }

  /* ---------------- transport ---------------- */

  let socket = null;
  let retry = 0;
  let heartbeat = null;

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws`);

    socket.onopen = () => {
      retry = 0;
      // Keeps the server-side receive loop alive so disconnects are detected.
      clearInterval(heartbeat);
      heartbeat = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 20000);
    };
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "snapshot") {
        state.snapshot = message.snapshot;
        state.progress = null;
        render();
      } else if (message.type === "overlay-edit") {
        state.overlayMoving = !!message.on;
        if (!message.on) {
          // A finished move changed the saved spot; the row's hint follows it.
          api("/api/settings").then((data) => {
            if (state.onOverlayMoving) state.onOverlayMoving(data.settings);
          }).catch(() => {});
        } else if (state.onOverlayMoving) {
          state.onOverlayMoving();
        }
      } else if (message.type === "progress") {
        state.progress = message;
        renderProgress();
      }
    };
    socket.onclose = () => {
      clearInterval(heartbeat);
      retry = Math.min(retry + 1, 10);
      setTimeout(connect, 400 * retry);
    };
  }

  async function api(path, options) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const text = await response.text();
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch { /* not JSON */ }
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }

  /* ---------------- views ---------------- */

  function switchView(name) {
    state.view = VIEWS.includes(name) ? name : "lobby";
    VIEWS.forEach((view) => {
      $(`view-${view}`).classList.toggle("hidden", view !== state.view);
    });
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.view === state.view);
    });
    if (state.view === "history") loadHistory(false);
    if (state.view === "encounters") loadEncounters();
    renderTabHint();
  }

  function renderTabHint() {
    const snap = state.snapshot;
    if (state.view === "lobby" || !snap) { $("tabHint").textContent = ""; return; }
    const inLobby = snap.teams && snap.teams.some((t) => t.players.length);
    $("tabHint").textContent = inLobby && !snap.stale
      ? "A lobby is live on the Lobby tab" : "";
  }

  /* ---------------- lobby rendering ---------------- */

  function render() {
    const snap = state.snapshot;
    if (!snap) return;

    renderHeader(snap);
    renderAlerts(snap.alerts || []);
    renderWinProbability(snap.winProbability);
    renderBoard(snap);
    renderProgress();
    renderTabHint();
  }

  function renderHeader(snap) {
    const pill = $("statePill");
    const map = {
      PREGAME: ["Agent select", "pill-pregame"],
      INGAME: ["In match", "pill-ingame"],
      MENUS: [snap.stale ? "Last lobby" : "Menus", "pill-idle"],
      DISCONNECTED: ["Disconnected", "pill-error"],
      STARTING: ["Starting", "pill-idle"],
    }[snap.state] || [snap.state, "pill-idle"];
    pill.textContent = map[0];
    pill.className = `pill ${map[1]}`;

    const connection = snap.connection || {};
    $("connDot").className = `conn-dot${connection.connected ? " on" : ""}`;
    $("connDot").title = connection.message || "";

    const parts = [];
    if (snap.stale && snap.message) parts.push(snap.message);
    if (snap.match) {
      if (snap.match.map) parts.push(snap.match.map);
      // One label, resolved server-side: the queue name when Riot gives one,
      // otherwise the mode with its internal asset suffixes stripped.
      const label = snap.match.label || snap.match.modeName;
      if (label) parts.push(label);
      if (snap.state === "PREGAME" && snap.match.enemyTeamSize) {
        parts.push(`enemy locked ${snap.match.enemyLockCount}/${snap.match.enemyTeamSize}`);
      }
    } else if (snap.message) {
      parts.push(snap.message);
    }
    if (connection.region) parts.push(connection.region.toUpperCase());
    $("matchLine").textContent = parts.join("  ·  ");
  }

  function renderAlerts(alerts) {
    const host = $("alerts");
    host.replaceChildren();
    alerts.forEach((alert) => {
      const node = el("div", `alert alert-${alert.level}`);
      node.append(el("strong", null, alert.title));
      node.append(el("span", "alert-text", alert.text));
      if (alert.dodgeable) node.append(el("span", "dodge-hint", "DODGE WINDOW OPEN"));
      host.append(node);
    });
  }

  function renderWinProbability(wp) {
    const section = $("winprob");
    if (!wp) { section.classList.add("hidden"); return; }
    section.classList.remove("hidden");
    $("wpAllyPct").textContent = `${wp.percent}%`;
    $("wpEnemyPct").textContent = `${100 - wp.percent}%`;
    $("wpAllyLabel").textContent = wp.allyLabel;
    $("wpEnemyLabel").textContent = wp.enemyLabel;
    $("wpFill").style.width = `${wp.percent}%`;
    $("wpNote").textContent = `${wp.note} (confidence: ${wp.confidence})`;
  }

  function renderBoard(snap) {
    const board = $("board");
    board.replaceChildren();

    const teams = snap.teams || [];
    if (!teams.some((t) => t.players.length)) {
      const empty = el("div", "empty");
      empty.append(el("h2", null, snap.state === "MENUS" ? "In menus" : "No lobby yet"));
      empty.append(el("p", null, snap.message ||
        "SpikeSight watches your local Riot Client. Queue up and the scoreboard appears the moment agent select loads."));
      board.append(empty);
      return;
    }

    teams.forEach((team) => {
      if (!team.players.length && snap.state !== "PREGAME") return;
      board.append(renderTeam(team, snap));
    });
  }

  function renderTeam(team, snap) {
    const section = el("section", `team team-${team.side}`);

    const head = el("div", "team-head");
    head.append(el("span", null, team.label));
    const known = team.players.filter((p) => p.rank.ranked).length;
    head.append(el("span", "team-avg",
      `${team.players.length} player${team.players.length === 1 ? "" : "s"} · ${known} ranked`));
    section.append(head);

    // Rows live in their own scroller so a narrow window scrolls sideways
    // instead of dropping columns. A hidden smurf flag is worse than a bar.
    const scroller = el("div", "team-scroll");
    const header = el("div", "row-head");
    COLUMNS.forEach(([cls, label, hint]) => {
      const cell = el("span", cls, label);
      const tooltip = cls === "col-kd" ? kdHeaderHint() : hint;
      if (tooltip) cell.title = tooltip;
      header.append(cell);
    });
    scroller.append(header);
    section.append(scroller);

    if (!team.players.length) {
      const size = snap.match && snap.match.enemyTeamSize;
      scroller.append(el("div", "enemy-pending",
        size ? `Enemy team hidden during agent select (${size} players connected).`
             : "Enemy team is not visible until the match starts."));
      return section;
    }

    team.players.forEach((player) => scroller.append(renderRow(player)));
    return section;
  }

  function smurfTooltip(smurf) {
    const head = smurf.flagged
      ? `Smurf score ${smurf.score}/100 — flagged`
      : `Smurf score ${smurf.score}/100 — below the flag threshold`;
    const lines = smurf.signals.map(
      (sig) => `  +${sig.weight}  ${sig.label}: ${sig.detail}`);
    const tail = smurf.levelHidden
      ? ["", "Account level is hidden by this player, so it is not scored."]
      : [];
    return [head, "", ...(lines.length ? lines : ["  no signals"]), ...tail].join("\n");
  }

  // Peak and previous-act share a shape: the rank as the headline, the act it
  // happened in as a caption underneath, or a stated reason for the blank.
  function rankStackCell(block, actRankHidden, tooltip, extraClass) {
    const cell = el("div", `rank-stack${extraClass ? " " + extraClass : ""}`);
    if (actRankHidden) {
      cell.append(el("span", "dash", "hidden"));
      cell.title = "This player hid their act rank badge, so SpikeSight does not show it";
      return cell;
    }
    if (!block) {
      cell.append(el("span", "dash", "—"));
      cell.title = "No ranked history to show here";
      return cell;
    }
    cell.append(rankChip(block.rank));
    cell.append(el("span", "act", block.act ? block.act.short : ""));
    cell.title = block.act
      ? `${tooltip}\n${block.rank.name} — ${block.act.full}`
      : `${tooltip}\n${block.rank.name}`;
    return cell;
  }

  // The single most confusable column: it is recent form, not lifetime and not
  // the act. Say so, with the real depth from the config.
  function kdHeaderHint() {
    const depth = (state.meta && state.meta.matchHistoryDepth) || 5;
    const queue = (state.meta && state.meta.statsQueue) || "competitive";
    return `RECENT FORM, not lifetime and not this act.

`
      + `Kills divided by deaths across each player's last ${depth} `
      + `${queue} matches, fetched when this lobby loaded. The smaller number `
      + `beside it is average combat score over the same matches.`;
  }

  function rankChip(rank) {
    const chip = el("span", `rank-chip${rank.ranked ? "" : " unranked"}`, rank.short);
    if (rank.ranked) chip.style.background = rank.color;
    chip.title = rank.name;
    return chip;
  }

  function agentCell(agent) {
    const cell = el("div", "agent");
    if (agent.icon) {
      const img = el("img", "agent-img");
      img.src = agent.icon;
      img.alt = agent.name || "";
      img.title = agent.name || "";
      img.loading = "lazy";
      // If the portrait is missing, fall back to the name rather than a
      // broken-image icon.
      img.addEventListener("error", () => {
        img.replaceWith(el("span", null, agent.name || "—"));
      });
      cell.append(img);
    } else {
      const label = el("span", null, agent.name || (agent.id ? "—" : "Picking…"));
      if (!agent.name) cell.classList.add("pending");
      cell.append(label);
    }
    return cell;
  }

  function trackerLink(url, label) {
    const link = el("a", "btn btn-link", label || "tracker.gg ↗");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = "Open this player's tracker.gg profile in a new tab";
    link.addEventListener("click", (event) => event.stopPropagation());
    return link;
  }

  function renderRow(player) {
    const row = el("div", "row");
    if (player.isSelf) row.classList.add("is-self");
    if (player.notes.count) row.classList.add("is-flagged");
    row.dataset.puuid = player.puuid;
    row.addEventListener("click", (event) => {
      if (event.target.closest(".flag-btn") || event.target.closest("a")) return;
      openDrawer(player.puuid);
    });

    /* party */
    const chip = el("span", "party-chip");
    if (player.party) {
      const color = PARTY_COLORS[(player.party.group - 1) % PARTY_COLORS.length];
      chip.style.color = color;
      if (player.party.confidence === "likely") {
        // Striped = inferred from recent matches rather than a live party id.
        chip.style.background =
          `repeating-linear-gradient(135deg, ${color} 0 4px, transparent 4px 8px)`;
        chip.classList.add("likely");
      } else {
        chip.style.background = color;
      }
      chip.title = `${player.party.size}-stack · ${player.party.confidence} · ${player.party.evidence}`;
    }
    row.append(chip);

    row.append(agentCell(player.agent));

    /* name */
    const who = el("div", "who");
    const name = el("span", "name", player.name.display);
    // The column ellipsises long riot IDs, so keep the full one on hover.
    name.title = player.name.hidden ? player.name.reason : player.name.display;
    if (player.name.hidden) name.classList.add("hidden-name");
    who.append(name);
    if (player.name.hiddenId) hiddenIdChip(player.name.hiddenId, who);
    if (player.isSelf) who.append(el("span", "tag-you", "YOU"));

    const met = player.encounters || {};
    if (met.count) {
      const badge = el("span", "badge badge-priv", `×${met.count}`);
      badge.title = `Seen ${met.count} time${met.count === 1 ? "" : "s"} before `
        + `(${met.enemy} as enemy, ${met.ally} as ally). First: ${fmtDay(met.firstSeen)}`;
      who.append(badge);
    }
    if (player.notes.count) {
      const badge = el("span", "badge badge-note",
        player.notes.maxSeverity >= 3 ? "DODGE" : player.notes.maxSeverity === 2 ? "AVOID" : "WATCH");
      badge.title = player.notes.items.map((n) => n.body || n.tags.join(", ")).join("\n");
      who.append(badge);
    }
    if (player.smurf.flagged) {
      // Sits next to the name rather than in the Flags column, so it stays
      // visible no matter how narrow the window gets.
      const badge = el("span", "badge badge-smurf", `SMURF ${player.smurf.score}/100`);
      badge.title = smurfTooltip(player.smurf);
      who.append(badge);
    }
    row.append(who);

    /* rank */
    const rankCell = el("div", "rank-cell");
    rankCell.append(rankChip(player.rank));
    if (player.leaderboardRank) {
      rankCell.append(el("span", "rank-lb", `#${player.leaderboardRank}`));
    } else if (player.rank.ranked && player.rank.rr !== null) {
      rankCell.append(el("span", "rank-rr", `${player.rank.rr} RR`));
    }
    row.append(rankCell);

    row.append(rankStackCell(
      player.peak,
      player.privacy.actRankHidden,
      "Peak rank — the highest they have ever reached",
    ));
    row.append(rankStackCell(
      player.previousAct,
      player.privacy.actRankHidden,
      "Where they finished the previous act",
      "col-prev",
    ));

    /* act record */
    const act = el("div", "small");
    if (player.act.hidden) {
      const hidden = el("span", "dash", "hidden");
      hidden.title = "Player hid their act rank badge";
      act.append(hidden);
    } else if (player.act.games) {
      act.append(el("span", null, `${player.act.wins}-${player.act.losses} `));
      const wr = el("span", player.act.winrate >= 0.55 ? "wr-good"
        : player.act.winrate < 0.45 ? "wr-bad" : "");
      wr.textContent = `${Math.round(player.act.winrate * 100)}%`;
      act.append(wr);
    } else {
      act.append(el("span", "dash", "—"));
    }
    row.append(act);

    /* level */
    const level = el("div", "small");
    if (player.levelHidden) {
      const hidden = el("span", "dash", "hidden");
      hidden.title = "Player hid their account level; excluded from smurf scoring";
      level.append(hidden);
    } else {
      level.append(el("span", null, player.level != null ? String(player.level) : "—"));
    }
    row.append(level);

    /* K/D */
    const kd = el("div", "kd");
    if (player.stats && player.stats.matches) {
      kd.append(el("span", null, player.stats.kd.toFixed(2)));
      if (player.stats.acs) kd.append(el("span", "acs", ` ${player.stats.acs} acs`));
      kd.title = `Recent form over the last ${player.stats.matches} matches
`
        + `${player.stats.wins}W ${player.stats.losses}L · `
        + `${player.stats.kills}/${player.stats.deaths}/${player.stats.assists} · `
        + `${player.stats.acs || "—"} ACS

Not lifetime, and not this act.`;
    } else {
      const blank = el("span", "dash", state.progress ? "…" : "—");
      blank.title = state.progress
        ? "Still fetching this player's recent matches"
        : "No recent match data (deep stats are off, or none could be fetched)";
      kd.append(blank);
    }
    row.append(kd);

    /* flags */
    const flags = el("div", "col-flags");
    if (!player.smurf.flagged && player.smurf.score > 0) {
      // Under the flag threshold but not nothing. "/100" makes it read as a
      // score rather than a count of something.
      const badge = el("span", "badge badge-score");
      badge.append(el("span", null, "smurf "));
      badge.append(el("b", null, String(player.smurf.score)));
      badge.append(el("span", null, "/100"));
      badge.title = smurfTooltip(player.smurf);
      flags.append(badge);
    }
    if (player.party && player.party.size >= 3) {
      flags.append(el("span", "badge badge-priv", `${player.party.size}-STACK`));
    }
    // No badge when the name is hidden - the italic agent name in place of a
    // riot ID already says it, and the name's own tooltip explains why. The
    // badge only earns its space when the name IS shown, which happens for a
    // party member and cannot be inferred from the row.
    if (player.privacy.incognito && !player.name.hidden) {
      const badge = el("span", "badge badge-priv", "STREAMER");
      badge.title = "Streamer Mode is on for this player. They are in your party, "
        + "so the name comes from your own Riot Client - it was never requested "
        + "from Riot, and nobody outside your party is shown this way.";
      flags.append(badge);
    }
    row.append(flags);

    /* actions */
    const actions = el("div", "row-actions");
    if (player.trackerUrl) actions.append(trackerLink(player.trackerUrl, "↗"));

    const button = el("button", `flag-btn${player.notes.count ? " active" : ""}`, "⚑");
    button.type = "button";
    button.title = player.notes.count ? `${player.notes.count} flag(s)` : "Flag this player";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDrawer(player.puuid);
    });
    actions.append(button);
    row.append(actions);

    return row;
  }

  function renderProgress() {
    const host = $("progress");
    if (!state.progress) {
      const snap = state.snapshot;
      host.textContent = snap && snap.stats && snap.stats.matchesExamined
        ? `Stats from ${snap.stats.matchesExamined} matches (${snap.stats.matchesFetched} fetched)`
        : "";
      return;
    }
    host.textContent = `Fetching match history… ${state.progress.done}/${state.progress.total}`;
  }

  /* ---------------- history view ---------------- */

  async function loadHistory(force) {
    const host = $("historyBody");
    if (state.history && !force) { renderHistory(state.history); return; }
    host.replaceChildren(el("p", "muted", "Loading your recent matches…"));
    try {
      state.history = await api(`/api/history${force ? "?refresh=true" : ""}`);
      renderHistory(state.history);
    } catch (error) {
      host.replaceChildren(el("p", "muted",
        `Could not load history: ${String(error.message || error)}`));
    }
  }

  function statBlock(label, value, cls) {
    const stat = el("div", "stat");
    stat.append(el("span", `stat-value ${cls || ""}`, value));
    stat.append(el("span", "stat-label", label));
    return stat;
  }

  function renderHistory(data) {
    const host = $("historyBody");
    host.replaceChildren();

    /* rank + summary cards */
    const top = el("div", "hist-top");

    const rankCard = el("div", "hist-card");
    rankCard.append(el("h4", null, "Current rank"));
    const rankLine = el("div", "hist-rank");
    rankLine.append(rankChip(data.rank));
    rankLine.append(el("span", "rank-name", data.rank.name));
    if (data.rank.leaderboardRank) {
      rankLine.append(el("span", "rank-lb", `#${data.rank.leaderboardRank}`));
    } else if (data.rank.rr !== null && data.rank.rr !== undefined) {
      rankLine.append(el("span", "rank-rr", `${data.rank.rr} RR`));
    }
    rankCard.append(rankLine);
    if (data.act && data.act.games) {
      rankCard.append(el("div", "hist-sub",
        `${data.act.act ? data.act.act.short + ": " : ""}`
        + `${data.act.wins}W ${data.act.losses}L · ${Math.round(data.act.winrate * 100)}%`));
    }
    top.append(rankCard);

    if (data.peak) {
      const peakCard = el("div", "hist-card");
      peakCard.append(el("h4", null, "Peak rank"));
      const line = el("div", "hist-rank");
      line.append(rankChip(data.peak.rank));
      line.append(el("span", "rank-name", data.peak.rank.name));
      peakCard.append(line);
      if (data.peak.act) peakCard.append(el("div", "hist-sub", data.peak.act.full));
      top.append(peakCard);
    }

    if (data.previousAct) {
      const prevCard = el("div", "hist-card");
      prevCard.append(el("h4", null, "Previous act"));
      const line = el("div", "hist-rank");
      line.append(rankChip(data.previousAct.rank));
      line.append(el("span", "rank-name", data.previousAct.rank.name));
      prevCard.append(line);
      prevCard.append(el("div", "hist-sub",
        `${data.previousAct.act.short} · ${data.previousAct.wins}W `
        + `${data.previousAct.losses}L`));
      top.append(prevCard);
    }

    const sum = data.summary || {};
    const summaryCard = el("div", "hist-card");
    summaryCard.append(el("h4", null, `Last ${sum.matches || 0} matches`));
    const grid = el("div", "stat-grid");
    grid.append(statBlock("W-L", `${sum.wins || 0}-${sum.losses || 0}`));
    grid.append(statBlock("Win rate",
      sum.winrate === null || sum.winrate === undefined ? "—"
        : `${Math.round(sum.winrate * 100)}%`,
      sum.winrate >= 0.5 ? "pos" : "neg"));
    grid.append(statBlock("K/D", sum.kd != null ? sum.kd.toFixed(2) : "—"));
    grid.append(statBlock("ACS", sum.acs != null ? String(sum.acs) : "—"));
    if (sum.rrNet !== null && sum.rrNet !== undefined) {
      grid.append(statBlock("Net RR", `${sum.rrNet > 0 ? "+" : ""}${sum.rrNet}`,
        sum.rrNet >= 0 ? "pos" : "neg"));
    }
    summaryCard.append(grid);
    top.append(summaryCard);
    host.append(top);

    /* RR sparkline */
    const timeline = (data.rrTimeline || []).filter((p) => p.points != null);
    if (timeline.length >= 2) host.append(renderSparkline(timeline));

    /* match list */
    if (!data.matches || !data.matches.length) {
      host.append(el("p", "muted", "No recent matches found."));
      return;
    }

    const list = el("div", "match-list");

    const head = el("div", "match-head");
    MATCH_COLUMNS.forEach(([cls, label, hint]) => {
      const cell = el("span", cls, label);
      if (hint) cell.title = hint;
      head.append(cell);
    });
    list.append(head);

    data.matches.forEach((match) => list.append(renderMatchRow(match)));
    host.append(list);
  }

  function renderSparkline(points) {
    const wrap = el("div", "spark-wrap");
    wrap.append(el("h4", null, `Rating over the last ${points.length} ranked matches`));

    const width = 1000;
    const height = 60;
    const values = points.map((p) => p.points);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(1, max - min);
    const step = points.length > 1 ? width / (points.length - 1) : width;

    const coords = points.map((p, i) => {
      const x = i * step;
      const y = height - ((p.points - min) / span) * (height - 10) - 5;
      return [x, y];
    });

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");

    const line = document.createElementNS(svg.namespaceURI, "polyline");
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#4a86c8");
    line.setAttribute("stroke-width", "2");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("points", coords.map(([x, y]) => `${x},${y}`).join(" "));
    svg.append(line);

    coords.forEach(([x, y], i) => {
      const dot = document.createElementNS(svg.namespaceURI, "circle");
      dot.setAttribute("cx", String(x));
      dot.setAttribute("cy", String(y));
      dot.setAttribute("r", "3");
      const earned = points[i].earned;
      dot.setAttribute("fill", earned >= 0 ? "#2fb3a4" : "#e0483d");
      const title = document.createElementNS(svg.namespaceURI, "title");
      title.textContent = `${earned > 0 ? "+" : ""}${earned} RR → ${points[i].rr} RR`;
      dot.append(title);
      svg.append(dot);
    });

    wrap.append(svg);
    return wrap;
  }

  function renderMatchRow(match) {
    const row = el("div", "match-row");

    const outcome = match.won === true ? "win" : match.won === false ? "loss" : "draw";
    row.append(el("div", `match-stripe ${outcome}`));
    row.append(agentCell(match.agent));

    const where = el("div");
    where.append(el("div", "match-map", match.map || "Unknown map"));
    where.append(el("div", "match-meta", match.queue || ""));
    row.append(where);

    const score = el("div", `match-score ${outcome === "win" ? "pos" : outcome === "loss" ? "neg" : ""}`);
    score.textContent = match.roundsWon != null && match.roundsLost != null
      ? `${match.roundsWon}–${match.roundsLost}` : "—";
    row.append(score);

    row.append(el("div", "match-kda",
      match.kills == null ? "—" : `${match.kills}/${match.deaths}/${match.assists}`));
    row.append(el("div", "match-num", match.kd != null ? match.kd.toFixed(2) : "—"));
    row.append(el("div", "match-num", match.acs != null ? String(match.acs) : "—"));

    const rr = el("div", "match-rr");
    if (match.rrEarned === null || match.rrEarned === undefined) {
      rr.append(el("span", "dash", "—"));
      rr.title = "Unranked queue: no RR change";
    } else {
      const delta = el("span", match.rrEarned >= 0 ? "pos" : "neg",
        `${match.rrEarned > 0 ? "+" : ""}${match.rrEarned}`);
      rr.append(delta);
      if (match.rank) {
        rr.append(el("span", "match-meta", `→ ${match.rank.short} ${match.rrAfter}`));
      }
      if (match.movement) {
        const promoted = match.movement === "promoted";
        const badge = el("span", `badge ${promoted ? "badge-promote" : "badge-demote"}`,
          promoted ? "UP" : "DOWN");
        badge.title = promoted ? "Ranked up in this match" : "Ranked down in this match";
        rr.append(badge);
      }
    }
    row.append(rr);

    row.append(el("div", "match-when", fmtDate(match.startedMs)));
    return row;
  }

  /* ---------------- encounters view ---------------- */

  let encTimer = null;

  function loadEncountersDebounced() {
    clearTimeout(encTimer);
    encTimer = setTimeout(loadEncounters, 220);
  }

  async function loadEncounters() {
    const host = $("encountersBody");
    const params = new URLSearchParams({
      q: $("encSearch").value.trim(),
      sort: $("encSort").value,
    });
    if ($("encFlagged").checked) params.set("flagged", "true");
    try {
      state.encounters = await api(`/api/encounters?${params}`);
      renderEncounters(state.encounters);
    } catch (error) {
      host.replaceChildren(el("p", "muted",
        `Could not load encounters: ${String(error.message || error)}`));
    }
  }

  function renderEncounters(data) {
    const host = $("encountersBody");
    host.replaceChildren();

    const rows = data.players || [];
    if (!rows.length) {
      host.append(el("p", "muted",
        "Nothing here yet. Every player you load into a match with is recorded "
        + "automatically, so this fills up as you play."));
    } else {
      const table = el("div", "enc-table");
      const head = el("div", "enc-head");
      [
        ["Player", "Riot ID as last seen. Players in Streamer Mode are never "
          + "named - they get a permanent local code instead, so your notes "
          + "still follow the right person. Search works on either."],
        ["Seen", "How many completed matches you have shared with this player."],
        ["As enemy / ally", "How that splits between playing against them and "
          + "playing with them."],
        ["Last seen", "The most recent match you shared."],
        ["Last match", "Map and agent from that most recent match."],
        ["", "Open their tracker.gg profile."],
      ].forEach(([label, hint]) => {
        const cell = el("span", null, label);
        if (hint) cell.title = hint;
        head.append(cell);
      });
      table.append(head);

      rows.forEach((row) => {
        const item = el("div", `enc-item${row.severity ? " flagged" : ""}`);
        item.addEventListener("click", (event) => {
          if (event.target.closest("a")) return;
          openDrawer(row.puuid, {
            display: row.name ? `${row.name}#${row.tag || ""}` : "Hidden player",
            hiddenId: row.hiddenId,
            sub: `Seen ${row.count} time${row.count === 1 ? "" : "s"}`,
            trackerUrl: row.name
              ? `https://tracker.gg/valorant/profile/riot/${encodeURIComponent(
                  `${row.name}#${row.tag || ""}`)}/overview`
              : null,
          });
        });

        const who = el("div", "who");
        const label = el("span", "name", row.name ? `${row.name}#${row.tag || ""}`
          : "Hidden player");
        if (!row.name) label.classList.add("hidden-name");
        who.append(label);
        if (row.hiddenId) hiddenIdChip(row.hiddenId, who);
        if (row.severity) {
          const badge = el("span", "badge badge-note", row.severityLabel.toUpperCase());
          badge.style.color = row.severityColor;
          who.append(badge);
        }
        item.append(who);

        item.append(el("span", "enc-count", String(row.count)));
        item.append(el("span", "enc-split", `${row.enemy} vs · ${row.ally} with`));
        item.append(el("span", "enc-split", fmtDay(row.lastSeen)));
        item.append(el("span", "match-meta",
          [row.lastMap, row.lastAgent].filter(Boolean).join(" · ") || "—"));

        const actions = el("div", "enc-actions");
        if (row.name) {
          actions.append(trackerLink(
            `https://tracker.gg/valorant/profile/riot/${encodeURIComponent(
              `${row.name}#${row.tag || ""}`)}/overview`));
        }
        item.append(actions);
        table.append(item);
      });
      host.append(table);
    }

    const storage = data.storage || {};
    const note = el("div", "storage-note");
    note.append(document.createTextNode(
      `${storage.players || 0} players and ${storage.encounters || 0} encounters `
      + `recorded, ${storage.notes || 0} flags on ${storage.flaggedPlayers || 0} players. `
      + "This is saved to disk as you play and is kept between sessions: "));
    note.append(el("code", null, storage.path || ""));
    note.append(document.createTextNode(
      ". A copy is made at every startup, and you can export a JSON backup from "
      + "the diagnostics panel."));
    host.append(note);
  }

  /* ---------------- drawer ---------------- */

  function findPlayer(puuid) {
    const snap = state.snapshot;
    if (!snap) return null;
    for (const team of snap.teams || []) {
      const found = team.players.find((p) => p.puuid === puuid);
      if (found) return found;
    }
    return null;
  }

  // A player in Streamer Mode has no riot ID we are allowed to show, so they
  // get a short code derived from their account id instead. It is the same
  // code every time, which is what makes a flag on them mean anything - two
  // hidden Brimstones are two different codes, and only the one you flagged
  // is flagged.
  function hiddenIdChip(code, where) {
    const chip = el("span", "hidden-id", code);
    chip.title = "This player hides their riot ID. SpikeSight gives them a "
      + `permanent local code (${code}) so your notes and encounter count `
      + "follow the right person. The code comes from their account id, means "
      + "nothing on its own, and never leaves this PC.";
    if (where) where.append(chip);
    return chip;
  }

  async function openDrawer(puuid, fallback) {
    state.drawerPuuid = puuid;
    state.drawerFallback = fallback || null;
    state.draft = { severity: 2, tags: new Set(), body: "" };

    const player = findPlayer(puuid);
    const drawerName = $("drawerName");
    drawerName.textContent = player ? player.name.display
      : (fallback && fallback.display) || "Player";
    const drawerCode = player ? player.name.hiddenId
      : (fallback && fallback.hiddenId);
    if (drawerCode) hiddenIdChip(drawerCode, drawerName);
    $("drawerSub").textContent = player
      ? [player.rank.name, player.agent.name,
         player.side === "ally" ? "your team" : "enemy team"].filter(Boolean).join("  ·  ")
      : (fallback && fallback.sub) || "";

    const trackerUrl = player ? player.trackerUrl : (fallback && fallback.trackerUrl);
    const trackerButton = $("drawerTracker");
    trackerButton.classList.toggle("hidden", !trackerUrl);
    if (trackerUrl) trackerButton.href = trackerUrl;

    $("noteBody").value = "";
    $("noteStatus").textContent = "";
    renderSeverityButtons();
    renderTagButtons();
    renderDrawerStats(player);
    renderDrawerPrivacy(player);

    $("drawer").classList.add("open");
    $("scrim").classList.add("on");

    try {
      const profile = await api(`/api/player/${puuid}`);
      renderExistingNotes(profile.notes || []);
      renderEncounterHistory(profile);
    } catch {
      $("existingNotes").replaceChildren(el("p", "muted", "Could not load history."));
    }
  }

  function closeDrawer() {
    $("drawer").classList.remove("open");
    $("scrim").classList.remove("on");
    state.drawerPuuid = null;
  }

  function renderSeverityButtons() {
    const host = $("severityRow");
    host.replaceChildren();
    const severities = (state.meta && state.meta.severities) || {
      1: { label: "Watch", color: "#d8a13a" },
      2: { label: "Avoid", color: "#e0722f" },
      3: { label: "Dodge", color: "#d13c4b" },
    };
    Object.entries(severities).forEach(([value, info]) => {
      const button = el("button", "sev-btn", info.label);
      button.type = "button";
      if (Number(value) === state.draft.severity) {
        button.classList.add("selected");
        button.style.background = info.color;
        button.style.borderColor = info.color;
      }
      button.addEventListener("click", () => {
        state.draft.severity = Number(value);
        renderSeverityButtons();
      });
      host.append(button);
    });
  }

  function renderTagButtons() {
    const host = $("tagRow");
    host.replaceChildren();
    ((state.meta && state.meta.suggestedTags) || []).forEach((tag) => {
      const button = el("button", "tag-btn", tag);
      button.type = "button";
      if (state.draft.tags.has(tag)) button.classList.add("selected");
      button.addEventListener("click", () => {
        if (state.draft.tags.has(tag)) state.draft.tags.delete(tag);
        else state.draft.tags.add(tag);
        renderTagButtons();
      });
      host.append(button);
    });
  }

  function renderExistingNotes(notes) {
    const host = $("existingNotes");
    host.replaceChildren();
    if (!notes.length) { host.append(el("p", "muted", "None yet.")); return; }

    notes.forEach((note) => {
      const item = el("div", "note-item");
      item.style.borderLeftColor = note.severityColor;

      const meta = el("div", "note-meta");
      const sev = el("span", null, note.severityLabel.toUpperCase());
      sev.style.color = note.severityColor;
      sev.style.fontWeight = "700";
      meta.append(sev);
      meta.append(el("span", null, new Date(note.createdAt).toLocaleString()));
      const del = el("button", "note-del", "delete");
      del.type = "button";
      del.addEventListener("click", async () => {
        await api(`/api/notes/${note.id}`, { method: "DELETE" });
        openDrawer(state.drawerPuuid, state.drawerFallback);
      });
      meta.append(del);
      item.append(meta);

      if (note.body) item.append(el("div", "note-body", note.body));
      if (note.tags.length) {
        const tagRow = el("div", "note-tags");
        note.tags.forEach((tag) => tagRow.append(el("span", null, tag)));
        item.append(tagRow);
      }
      host.append(item);
    });
  }

  function renderEncounterHistory(profile) {
    const host = $("encounterList");
    host.replaceChildren();
    const encounters = profile.encounters || [];
    if (!encounters.length) {
      host.append(el("p", "muted", "First time seeing this player."));
      return;
    }
    host.append(el("p", "muted",
      `Seen ${profile.encounterCount} time${profile.encounterCount === 1 ? "" : "s"}, `
      + `first on ${fmtDay(profile.firstSeen)}.`));
    encounters.slice(0, 20).forEach((enc) => {
      const row = el("div", "enc-row");
      row.append(el("span", `enc-rel ${enc.relation}`, enc.relation === "enemy" ? "vs" : "with"));
      row.append(el("span", null, fmtDay(enc.seen_at)));
      row.append(el("span", null, [enc.map_name, enc.agent].filter(Boolean).join(" · ")));
      host.append(row);
    });
  }

  function renderDrawerStats(player) {
    const host = $("drawerStats");
    host.replaceChildren();

    if (player && player.smurf.signals.length) {
      const title = el("div", "note-meta");
      title.append(el("span", null, `Smurf score ${player.smurf.score}/100`));
      host.append(title);
      player.smurf.signals.forEach((signal) => {
        const row = el("div", "signal-row");
        row.append(el("span", null, `${signal.label} — ${signal.detail}`));
        row.append(el("span", "sig-weight", `+${signal.weight}`));
        host.append(row);
      });
    }

    if (!player || !player.stats || !player.stats.recent.length) {
      host.append(el("p", "muted", "No recent match data fetched."));
      return;
    }
    const strip = el("div", "match-strip");
    player.stats.recent.forEach((match) => {
      const pip = el("div",
        `match-pip ${match.won === true ? "win" : match.won === false ? "loss" : ""}`,
        `${match.kills}/${match.deaths}/${match.assists}`);
      pip.title = new Date(match.startedMs).toLocaleString();
      strip.append(pip);
    });
    host.append(strip);
  }

  function renderDrawerPrivacy(player) {
    const host = $("drawerPrivacy");
    host.replaceChildren();
    if (!player) { host.append(el("p", "muted", "—")); return; }
    const reasons = player.privacy.reasons || [];
    if (!reasons.length) {
      host.append(el("p", "muted", "No privacy flags set by this player."));
      return;
    }
    reasons.forEach((reason) => host.append(el("div", "signal-row", reason)));
  }

  /* ---------------- diagnostics ---------------- */

  // [key, label, description, choices?]. The description matters more than
  // usual here: these change how the app behaves when it is not in front of
  // you. Rows with choices render as a dropdown instead of a checkbox.
  const SETTING_ROWS = [
    ["minimizeDuringMatch", "Minimize after agent select",
      "Hides the window when the match starts and brings it back at the end. "
      + "Off, because the enemy team only appears once the match begins and "
      + "that is when a lot of people want the board. Turn it on if you would "
      + "rather have the frames: in Windowed Fullscreen, any visible window "
      + "stops VALORANT drawing straight to the screen."],
    ["overlay", "Pre-match overlay",
      "Shows your team and your best agents for the map over the game during "
      + "agent select, then gets out of the way. It can't be clicked, so it "
      + "never blocks a pick (use Move overlay below to reposition it). "
      + "Ctrl+Shift+O hides it for the current match. "
      + "Needs VALORANT in Windowed Fullscreen - exclusive fullscreen draws over "
      + "everything."],
    ["overlaySide", "Overlay position",
      "Which side of the screen the overlay docks to. The right is clear "
      + "during agent select - your team is down the left, and the agent grid "
      + "runs through the middle. Use Move overlay below to put it anywhere.",
      [["right", "Right edge"], ["left", "Left edge"]]],
    ["minimizeToTray", "Minimize to the notification area",
      "Minimizing hides the window instead of leaving a taskbar button. "
      + "Click the SpikeSight icon by the clock to bring it back."],
    ["closeToTray", "Close to the notification area",
      "Closing the window leaves SpikeSight running so it keeps following your "
      + "matches. Quit it from the tray icon's menu."],
    ["startWithWindows", "Start with Windows",
      "Launches SpikeSight when you sign in. Adds one entry under your own user "
      + "account - no admin rights, no service, no scheduled task."],
    ["deepStats", "Fetch recent-match stats",
      "Powers the K/D column and part of the smurf score. Turning it off makes "
      + "SpikeSight much lighter, at the cost of those two things."],
    ["smurfDetection", "Smurf detection",
      "Scores each player for how likely they are to be smurfing."],
  ];

  function settingsSection(settings) {
    const section = el("div", "settings-list");
    SETTING_ROWS.forEach(([key, label, description, choices]) => {
      if (choices) {
        section.append(choiceRow(settings, key, label, description, choices));
        if (key === "overlaySide") section.append(overlayMoveRow(settings));
        return;
      }
      const row = el("label", "setting-row");
      const box = el("input");
      box.type = "checkbox";
      box.checked = !!settings[key];
      box.addEventListener("change", async () => {
        box.disabled = true;
        try {
          const result = await api("/api/settings", {
            method: "POST",
            body: JSON.stringify({ [key]: box.checked }),
          });
          // The server is the authority: Windows can refuse the startup entry.
          box.checked = !!result.settings[key];
        } catch (error) {
          box.checked = !box.checked;
          alert(`Could not save that setting: ${error.message || error}`);
        } finally {
          box.disabled = false;
        }
      });
      const text = el("div", "setting-text");
      text.append(el("div", "setting-label", label));
      text.append(el("div", "setting-desc", description));
      row.append(box);
      row.append(text);
      section.append(row);
    });
    return section;
  }

  // Move mode: the overlay shows up and can be dragged until Done is pressed
  // (on the overlay or here). Its state comes back over the socket.
  function overlayMoveRow(settings) {
    const row = el("div", "setting-row setting-actions");
    const move = el("button", "btn", "Move overlay");
    move.type = "button";
    const reset = el("button", "btn btn-ghost", "Reset position");
    reset.type = "button";
    const text = el("div", "setting-text");
    const hint = el("div", "setting-desc");
    text.append(hint);

    const describe = () => {
      if (state.overlayMoving) {
        move.textContent = "Done moving";
        hint.textContent = "Drag the overlay wherever you want it. It stays put "
          + "until you press Done - here or on the overlay itself.";
      } else {
        move.textContent = "Move overlay";
        hint.textContent = settings.overlayMoved
          ? "Using a spot you dragged it to. Picking a side above, or Reset, "
            + "docks it again."
          : "Shows the overlay so you can drag it somewhere else - handy if it "
            + "covers something.";
      }
    };
    state.onOverlayMoving = (fresh) => {
      if (fresh) settings.overlayMoved = !!fresh.overlayMoved;
      describe();
    };
    describe();

    move.addEventListener("click", async () => {
      move.disabled = true;
      try {
        await api("/api/overlay/edit", {
          method: "POST",
          body: JSON.stringify({ on: !state.overlayMoving }),
        });
      } catch (error) {
        alert(error.message || String(error));
      } finally {
        move.disabled = false;
      }
    });
    reset.addEventListener("click", async () => {
      try {
        await api("/api/overlay/reset", { method: "POST", body: "{}" });
        settings.overlayMoved = false;
        describe();
      } catch (error) {
        alert(`Could not reset: ${error.message || error}`);
      }
    });

    const buttons = el("div", "setting-buttons");
    buttons.append(move, reset);
    row.append(buttons, text);
    return row;
  }

  function choiceRow(settings, key, label, description, choices) {
    const row = el("label", "setting-row");
    const select = el("select", "setting-select");
    choices.forEach(([value, text]) => {
      const option = el("option", null, text);
      option.value = value;
      select.append(option);
    });
    select.value = settings[key] || choices[0][0];
    let saved = select.value;
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        const result = await api("/api/settings", {
          method: "POST",
          body: JSON.stringify({ [key]: select.value }),
        });
        saved = result.settings[key];
        select.value = saved;
      } catch (error) {
        select.value = saved;
        alert(`Could not save that setting: ${error.message || error}`);
      } finally {
        select.disabled = false;
      }
    });
    const text = el("div", "setting-text");
    text.append(el("div", "setting-label", label));
    text.append(el("div", "setting-desc", description));
    row.append(select);
    row.append(text);
    return row;
  }

  // Frame-rate complaints are common and almost never about SpikeSight's own
  // load, so this shows the settings that actually decide it, and a test that
  // tells you whether SpikeSight is involved at all.
  function performanceSection(perf) {
    const host = el("div", "perf");
    if (!perf) return host;

    const facts = el("div", "perf-facts");
    const fact = (label, value, tone) => {
      const row = el("div", "perf-fact");
      row.append(el("span", "perf-label", label));
      row.append(el("span", `perf-value${tone ? ` ${tone}` : ""}`, value));
      facts.append(row);
    };

    const game = perf.game || {};
    fact("VALORANT display mode", game.available ? game.displayMode : "not found",
      game.displayMode === "Windowed Fullscreen" ? "warn" : null);
    if (game.available) {
      fact("VALORANT frame limit", game.frameRateLimit === "none"
        ? "none" : `${game.frameRateLimit} fps`,
        game.frameRateLimit === "none" ? null : "warn");
      fact("VALORANT vsync", game.vsync === "True" ? "on" : "off");
    }
    (perf.monitors || []).forEach((monitor, index) => {
      fact(`Monitor ${index + 1}${monitor.primary ? " (main)" : ""}`,
        `${monitor.width}×${monitor.height} at ${monitor.refresh}Hz`,
        perf.mixedRefreshRates ? "warn" : null);
    });
    const graphics = perf.graphics || {};
    fact("Hardware GPU scheduling", graphics.hardwareGpuScheduling || "unknown");
    fact("Multi-plane overlays", graphics.multiPlaneOverlays || "unknown");
    host.append(facts);

    (perf.notes || []).forEach((note) => host.append(el("p", "perf-note", note)));

    host.append(el("h4", "perf-head", "If your frame rate drops"));
    const steps = el("ol", "perf-steps");
    [
      "Turn on VALORANT's own FPS counter (Settings → Video → Stats → Client "
        + "FPS) so you are reading a number, not a feeling.",
      "In a match, open Notepad over the game. If the frame rate drops the "
        + "same way, this is Windows compositing the desktop and has nothing "
        + "to do with SpikeSight - any window does it.",
      "Close the window again. If the frame rate does NOT come back, alt-tab "
        + "out of VALORANT and back in. That remakes how the game draws and "
        + "usually restores it.",
      "Still slow after that? Restart VALORANT, and tell whoever gave you "
        + "SpikeSight what the numbers were at each step.",
    ].forEach((step) => steps.append(el("li", null, step)));
    host.append(steps);
    return host;
  }

  // The changelog is written in markdown; this understands the small subset it
  // uses. Built as DOM nodes rather than innerHTML - it is our own file, but
  // there is no reason to open that door.
  function inlineMarkdown(text) {
    const fragment = document.createDocumentFragment();
    const pattern = /(\*\*[\s\S]+?\*\*|`[^`]+`|\*[^*`]+?\*)/g;
    let index = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > index) fragment.append(text.slice(index, match.index));
      const token = match[0];
      if (token.startsWith("**")) {
        // Recurse: bold routinely wraps `code`, and a flat pass would emit the
        // backticks as literal text.
        const bold = el("strong");
        bold.append(inlineMarkdown(token.slice(2, -2)));
        fragment.append(bold);
      } else if (token.startsWith("`")) {
        // Code spans are literal all the way down.
        fragment.append(el("code", null, token.slice(1, -1)));
      } else {
        const italic = el("em");
        italic.append(inlineMarkdown(token.slice(1, -1)));
        fragment.append(italic);
      }
      index = match.index + token.length;
    }
    if (index < text.length) fragment.append(text.slice(index));
    return fragment;
  }

  function renderChangelog(data) {
    const host = el("div", "changelog");
    (data.releases || []).forEach((release) => {
      const entry = el("section", "release");

      const head = el("div", "release-head");
      const version = el("span", "release-version", release.version);
      if (release.version === data.version) {
        version.classList.add("is-current");
        version.title = "The version you are running";
      }
      head.append(version);
      if (release.title) head.append(el("span", "release-title", release.title));
      entry.append(head);

      (release.intro || []).forEach((paragraph) => {
        const node = el("p", "release-intro");
        node.append(inlineMarkdown(paragraph));
        entry.append(node);
      });

      (release.sections || []).forEach((section) => {
        entry.append(el("h4", "release-section", section.heading));
        const list = el("ul", "release-items");
        section.items.forEach((item) => {
          const bullet = el("li");
          bullet.append(inlineMarkdown(item));
          list.append(bullet);
        });
        entry.append(list);
      });

      host.append(entry);
    });
    if (!host.children.length) {
      host.append(el("p", "muted", "No release notes are available in this build."));
    }
    return host;
  }

  async function openChangelog() {
    const data = await api("/api/changelog");
    $("modalTitle").textContent = "What's new";
    const body = $("modalBody");
    body.replaceChildren();

    const back = el("button", "btn", "← Back to settings");
    back.type = "button";
    back.addEventListener("click", openDiagnostics);
    body.append(back);
    body.append(renderChangelog(data));

    body.scrollTop = 0;
    $("modal").classList.add("open");
    $("scrim").classList.add("on");
  }

  async function openDiagnostics() {
    const [data, settingsResponse, overlayEdit] = await Promise.all([
      api("/api/diagnostics"),
      api("/api/settings").catch(() => ({ settings: {} })),
      api("/api/overlay/edit").catch(() => ({ on: false })),
    ]);
    state.overlayMoving = !!overlayEdit.on;
    $("modalTitle").textContent = "Settings";
    const body = $("modalBody");
    body.replaceChildren();

    const banner = el("div", "version-banner");
    banner.append(el("span", "version-name", `SpikeSight ${data.version}`));
    const whatsNew = el("button", "btn btn-link", "What's new →");
    whatsNew.type = "button";
    whatsNew.addEventListener("click", openChangelog);
    banner.append(whatsNew);
    body.append(banner);

    body.append(settingsSection(settingsResponse.settings || {}));
    body.append(el("h3", "modal-section", "Performance"));
    body.append(performanceSection(data.performance));
    body.append(el("h3", "modal-section", "Diagnostics"));

    const rows = [
      ["Version", data.version],
      ["Connection", data.connection.message || ""],
      ["Region / shard", `${data.connection.region || "?"} / ${data.connection.shard || "?"}`],
      ["Client version", data.connection.clientVersion || "?"],
      ["Current act", data.connection.currentAct ? data.connection.currentAct.full : "?"],
      ["Content source", data.connection.contentSource || "?"],
      ["Requests sent this session", String(data.requestsSent)],
      ["Rate limit", `${data.rateLimit.perSecond}/s, burst ${data.rateLimit.burst}, ${data.rateLimit.concurrency} concurrent`],
      ["Response cache", `${data.cache.entries || 0} entries, ${data.cache.hits || 0} hits / ${data.cache.misses || 0} misses`],
      ["Cached match details", String(data.cachedMatches)],
      ["Notes database", `${data.notes.notes} notes on ${data.notes.flaggedPlayers} players, ${data.notes.encounters} encounters`],
      ["Database file", data.notes.path],
      ["Startup backup", data.notes.backup || "—"],
      ["Lockfile", data.paths.lockfile],
      ["Game log", data.paths.gameLog],
      ["SpikeSight log", data.paths.appLog || "(console)"],
    ];
    const list = el("dl", "diag-grid");
    rows.forEach(([term, value]) => {
      list.append(el("dt", null, term));
      list.append(el("dd", null, value));
    });
    body.append(list);

    const actions = el("div", "drawer-actions");

    const shortcutBtn = el("button", "btn", "Create desktop shortcut");
    shortcutBtn.type = "button";
    shortcutBtn.addEventListener("click", async () => {
      shortcutBtn.disabled = true;
      const original = shortcutBtn.textContent;
      try {
        await api("/api/desktop-shortcut", { method: "POST" });
        shortcutBtn.textContent = "Shortcut created";
      } catch (error) {
        shortcutBtn.textContent = "Could not create it";
        console.error(error);
      } finally {
        setTimeout(() => {
          shortcutBtn.textContent = original;
          shortcutBtn.disabled = false;
        }, 2500);
      }
    });
    actions.append(shortcutBtn);

    const exportBtn = el("button", "btn", "Export notes + encounters (JSON)");
    exportBtn.type = "button";
    exportBtn.addEventListener("click", async () => {
      const payload = await api("/api/notes/export");
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `spikesight-notes-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    });
    actions.append(exportBtn);
    body.append(actions);

    $("modal").classList.add("open");
    $("scrim").classList.add("on");
  }

  function closeModal() {
    $("modal").classList.remove("open");
    if (!$("drawer").classList.contains("open")) $("scrim").classList.remove("on");
  }

  /* ---------------- wiring ---------------- */

  $("tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) switchView(tab.dataset.view);
  });

  $("drawerClose").addEventListener("click", closeDrawer);
  $("modalClose").addEventListener("click", closeModal);
  $("scrim").addEventListener("click", () => { closeDrawer(); closeModal(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeDrawer(); closeModal(); }
  });

  $("btnRefresh").addEventListener("click", () => {
    if (state.view === "history") loadHistory(true);
    else if (state.view === "encounters") loadEncounters();
    else api("/api/refresh", { method: "POST" });
  });
  $("btnDiag").addEventListener("click", openDiagnostics);
  $("historyRefresh").addEventListener("click", () => loadHistory(true));

  $("encSearch").addEventListener("input", loadEncountersDebounced);
  $("encSort").addEventListener("change", loadEncounters);
  $("encFlagged").addEventListener("change", loadEncounters);

  $("saveNote").addEventListener("click", async () => {
    if (!state.drawerPuuid) return;
    const body = $("noteBody").value.trim();
    const tags = [...state.draft.tags];
    if (!body && !tags.length) {
      $("noteStatus").textContent = "Add a tag or a note first.";
      return;
    }
    const snap = state.snapshot;
    await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        puuid: state.drawerPuuid,
        severity: state.draft.severity,
        tags,
        body,
        matchId: snap && snap.match ? snap.match.id : null,
      }),
    });
    $("noteStatus").textContent = "Saved.";
    $("noteBody").value = "";
    state.draft.tags.clear();
    renderTagButtons();
    openDrawer(state.drawerPuuid, state.drawerFallback);
    if (state.view === "encounters") loadEncounters();
  });

  (async () => {
    initDensity();
    initTheme();
    // #history / #encounters opens straight to that tab.
    switchView(location.hash.replace("#", "") || "lobby");
    try { state.meta = await api("/api/meta"); } catch { /* non-fatal */ }
    try {
      const initial = await api("/api/state");
      state.snapshot = initial.snapshot;
      render();
    } catch { /* the websocket will deliver it */ }
    connect();
  })();
})();
