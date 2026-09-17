/* The pre-match overlay page.

Runs inside a click-through, always-on-top window during agent select. It
reads the same snapshot as the main window over the same WebSocket (marked as
the overlay, so it never keeps the app alive), shows your own team only, and
tells the backend how tall it is so the window can be fitted around it.
*/
(() => {
  "use strict";

  const PARTY_COLORS = ["#3ba7e0", "#e0a13b", "#8f5be0", "#3be08f", "#e05b8f", "#5be0d8"];
  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  const pct = (value) => (value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`);

  /* ---------------- teammates ---------------- */

  function portrait(agent, selectionState) {
    if (agent && agent.icon) {
      const img = el("img", "ov-portrait");
      img.src = agent.icon;
      img.alt = agent.name || "";
      if (selectionState !== "locked") img.classList.add("hovering");
      img.addEventListener("error", () => img.replaceWith(el("div", "ov-portrait ov-portrait-empty", "?")));
      return img;
    }
    return el("div", "ov-portrait ov-portrait-empty", "?");
  }

  function rankChip(rank) {
    const chip = el("span", `rank-chip${rank && rank.ranked ? "" : " unranked"}`,
      rank ? rank.short : "—");
    if (rank && rank.ranked) chip.style.background = rank.color;
    return chip;
  }

  function stat(label, value, tone) {
    const wrap = el("span");
    const number = el("b", tone || null, value);
    wrap.append(number, ` ${label}`);
    return wrap;
  }

  function card(player) {
    const severity = (player.notes && player.notes.count) ? player.notes.maxSeverity : 0;
    const node = el("div", "ov-card");
    if (player.isSelf) node.classList.add("is-self");
    if (severity) node.classList.add(`sev-${severity}`);

    if (player.party && player.party.group) {
      const stripe = el("span", "ov-party");
      stripe.style.background = PARTY_COLORS[(player.party.group - 1) % PARTY_COLORS.length];
      if (player.party.confidence !== "confirmed") stripe.classList.add("likely");
      node.append(stripe);
    }

    node.append(portrait(player.agent, player.selectionState));

    /* line 1: who, and anything worth shouting about */
    const top = el("div", "ov-line");
    const name = el("span", "ov-name", player.name.display);
    if (player.name.hidden) name.classList.add("hidden-name");
    top.append(name);
    if (player.name.hiddenId) top.append(el("span", "hidden-id", player.name.hiddenId));
    if (player.isSelf) top.append(el("span", "tag-you", "YOU"));
    if (severity) {
      top.append(el("span", "badge badge-note",
        severity >= 3 ? "DODGE" : severity === 2 ? "AVOID" : "WATCH"));
    }
    if (player.smurf && player.smurf.flagged) {
      top.append(el("span", "badge badge-smurf", "SMURF"));
    }
    if (player.encounters && player.encounters.count) {
      top.append(el("span", "badge badge-priv", `×${player.encounters.count}`));
    }
    node.append(top);

    /* line 2: rank and pick */
    const mid = el("div", "ov-line");
    mid.append(rankChip(player.rank));
    const sub = el("span", "ov-sub");
    if (player.rank && player.rank.ranked && player.rank.rr !== null && player.rank.rr !== undefined) {
      sub.append(`${player.rank.rr} RR · `);
    }
    if (player.agent && player.agent.name) {
      if (player.selectionState === "locked") {
        sub.append(el("span", "locked", player.agent.name));
      } else {
        sub.append(`${player.agent.name} (hovering)`);
      }
    } else {
      sub.append("Picking…");
    }
    mid.append(sub);
    node.append(mid);

    /* line 3: numbers */
    const bottom = el("div", "ov-stats");
    const act = player.act || {};
    if (act.hidden) {
      bottom.append(stat("act", "hidden"));
    } else if (act.games) {
      const tone = act.winrate >= 0.55 ? "good" : act.winrate < 0.45 ? "bad" : null;
      bottom.append(stat(`act (${act.games})`, pct(act.winrate), tone));
    }
    const recent = player.stats || null;
    if (recent && recent.matches) {
      const tone = recent.kd >= 1.2 ? "good" : recent.kd < 0.85 ? "bad" : null;
      bottom.append(stat("K/D", (recent.kd ?? 0).toFixed(2), tone));
      if (recent.acs) bottom.append(stat("ACS", recent.acs));
    }
    if (!player.levelHidden && player.level) bottom.append(stat("lvl", player.level));

    if (recent && recent.recent && recent.recent.length) {
      const pips = el("span", "ov-pips");
      pips.title = "Recent matches, newest first";
      recent.recent.slice(0, 5).forEach((match) => {
        pips.append(el("span", `ov-pip${match.won === true ? " win" : match.won === false ? " loss" : ""}`));
      });
      bottom.append(pips);
    }
    node.append(bottom);
    return node;
  }

  function renderTeam(snapshot) {
    const host = $("ovTeam");
    host.replaceChildren();
    // Your own team only. The enemy team is hidden in agent select, and the
    // overlay does not try to show it.
    const ally = (snapshot.teams || []).find((team) => team.side === "ally");
    const players = ally ? ally.players.filter((p) => !p.isCoach) : [];
    if (!players.length) {
      host.append(el("div", "ov-empty", "Waiting for your team…"));
      return;
    }
    players.forEach((player) => host.append(card(player)));
  }

  /* ---------------- top agents ---------------- */

  let poolFor = null;
  let poolTimer = null;

  function renderPool(pool) {
    const rows = $("ovPoolRows");
    rows.replaceChildren();
    $("ovScope").textContent = "";
    if (!pool || !pool.ready) {
      rows.append(el("div", "ov-empty", "Reading your recent matches…"));
      return;
    }
    if (!pool.agents || !pool.agents.length) {
      rows.append(el("div", "ov-empty", "No recent matches in this mode yet."));
      return;
    }
    $("ovScope").textContent = pool.scope === "map"
      ? `${pool.map || "This map"} · ${pool.matches} games`
      : `All maps · ${pool.matches} games`;

    const head = el("div", "ov-pool-row head");
    head.append(el("span"), el("span", null, "Agent"));
    ["Games", "Win", "K/D", "ACS"].forEach((label) => head.append(el("span", "head-num", label)));
    rows.append(head);

    pool.agents.forEach((agent) => {
      const row = el("div", "ov-pool-row");
      const img = el("img");
      if (agent.icon) img.src = agent.icon;
      img.alt = "";
      row.append(img, el("span", "ov-pool-name", agent.name || "Unknown"));
      row.append(el("span", "num", agent.matches));
      const win = el("span", "num", pct(agent.winrate));
      if (agent.winrate !== null) {
        win.style.color = agent.winrate >= 0.55 ? "var(--ally)"
          : agent.winrate < 0.45 ? "var(--enemy)" : "";
      }
      row.append(win);
      row.append(el("span", "num", agent.kd.toFixed(2)));
      row.append(el("span", "num", agent.acs ?? "—"));
      rows.append(row);
    });
  }

  async function loadPool(matchId, attempt) {
    clearTimeout(poolTimer);
    if (matchId !== poolFor) return;
    let pool = null;
    try {
      const response = await fetch("/api/overlay/agents", { cache: "no-store" });
      if (response.ok) pool = await response.json();
    } catch { /* retried below */ }
    if (matchId !== poolFor) return;
    renderPool(pool);
    reportSize();
    if ((!pool || !pool.ready) && attempt < 12) {
      poolTimer = setTimeout(() => loadPool(matchId, attempt + 1), 5000);
    }
  }

  /* ---------------- snapshot ---------------- */

  // Stand-in rows for move mode outside agent select, so the panel is the
  // size it will really be while you place it.
  function renderSample() {
    $("ovMatch").textContent = "Position preview";
    const host = $("ovTeam");
    host.replaceChildren();
    for (let i = 0; i < 5; i += 1) {
      const node = el("div", "ov-card sample");
      node.append(el("div", "ov-portrait ov-portrait-empty", "?"));
      const top = el("div", "ov-line");
      top.append(el("span", "ov-name", `Teammate ${i + 1}`));
      node.append(top);
      const mid = el("div", "ov-line");
      mid.append(rankChip(null), el("span", "ov-sub", "Picking…"));
      node.append(mid);
      node.append(el("div", "ov-stats", "Win rate · K/D · ACS · level"));
      host.append(node);
    }
    renderPool({ ready: true, agents: [] });
    $("ovPoolRows").replaceChildren(el("div", "ov-empty", "Your best agents for the map go here."));
    reportSize();
  }

  let lastSnapshot = null;

  function render(snapshot) {
    if (!snapshot) return;
    lastSnapshot = snapshot;
    if (snapshot.settings && snapshot.settings.theme) {
      window.SpikeSightTheme.apply(snapshot.settings.theme);
    }
    if (snapshot.state !== "PREGAME") {
      poolFor = null;
      if (moving) renderSample();
      return;
    }
    const match = snapshot.match || {};
    $("ovMatch").textContent = [match.map, match.label || match.modeName]
      .filter(Boolean).join(" · ") || "Agent select";
    renderTeam(snapshot);

    const matchId = match.id || "";
    if (matchId !== poolFor) {
      poolFor = matchId;
      renderPool(null);
      loadPool(matchId, 0);
    }
    reportSize(true);
  }

  /* ---------------- sizing ---------------- */

  let lastReport = "";
  // `force` after a render: the app waits to hear from the page before it
  // puts the panel on screen, and a render that happens to come out the same
  // height as last time still needs to say so.
  function reportSize(force) {
    const metrics = {
      contentHeight: Math.ceil($("panel").getBoundingClientRect().height),
      outerWidth: window.outerWidth,
      innerWidth: window.innerWidth,
      outerHeight: window.outerHeight,
      innerHeight: window.innerHeight,
    };
    const key = JSON.stringify(metrics);
    if (key === lastReport && !force) return;
    lastReport = key;
    fetch("/api/overlay/metrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: key,
    }).catch(() => { lastReport = ""; });
  }

  new ResizeObserver(reportSize).observe($("panel"));
  window.addEventListener("resize", reportSize);

  /* ---------------- move mode ---------------- */

  // Only in move mode does this window take the mouse at all. Dragging
  // reports how far the pointer has travelled; the app moves the window.
  let moving = false;
  let drag = null;

  const post = (url, body) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

  function setMoving(on) {
    moving = on;
    $("ovMove").hidden = !on;
    $("panel").classList.toggle("moving", on);
    if (!on) {
      drag = null;
      $("panel").classList.remove("dragging");
    }
    if (on && (!lastSnapshot || lastSnapshot.state !== "PREGAME")) renderSample();
    if (!on && lastSnapshot) render(lastSnapshot);
    reportSize();
  }

  function sendMove(final) {
    if (!drag) return;
    const body = { phase: final ? "end" : "move", dx: drag.dx, dy: drag.dy };
    if (final) {
      drag = null;
      post("/api/overlay/drag", body).catch(() => {});
      return;
    }
    if (drag.busy) { drag.pending = true; return; }
    drag.busy = true;
    post("/api/overlay/drag", body).catch(() => {}).finally(() => {
      if (!drag) return;
      drag.busy = false;
      if (drag.pending) { drag.pending = false; sendMove(false); }
    });
  }

  // Listen on the whole document: the window is clipped to the panel, so any
  // press that reaches this page is a press on the panel.
  const surface = document.documentElement;
  surface.addEventListener("pointerdown", async (event) => {
    if (!moving || event.button !== 0 || event.target.closest("button")) return;
    event.preventDefault();
    surface.setPointerCapture(event.pointerId);
    $("panel").classList.add("dragging");
    drag = { x: event.screenX, y: event.screenY, dx: 0, dy: 0, busy: true, pending: false };
    try { await post("/api/overlay/drag", { phase: "start" }); } catch { /* ignore */ }
    if (drag) { drag.busy = false; if (drag.pending) sendMove(false); }
  });

  const track = (event) => {
    drag.dx = event.screenX - drag.x;
    drag.dy = event.screenY - drag.y;
  };

  surface.addEventListener("pointermove", (event) => {
    if (!drag) return;
    track(event);
    sendMove(false);
  });

  const finishDrag = (event) => {
    if (!drag) return;
    if (event.type === "pointerup") track(event);
    $("panel").classList.remove("dragging");
    sendMove(true);
  };
  surface.addEventListener("pointerup", finishDrag);
  surface.addEventListener("pointercancel", finishDrag);

  $("ovDone").addEventListener("click", () => {
    post("/api/overlay/edit", { on: false }).catch(() => {});
  });
  $("ovReset").addEventListener("click", () => {
    post("/api/overlay/reset").catch(() => {});
  });

  async function syncMoving() {
    try {
      const response = await fetch("/api/overlay/edit", { cache: "no-store" });
      if (response.ok) setMoving(!!(await response.json()).on);
    } catch { /* the socket will tell us */ }
  }

  /* ---------------- transport ---------------- */

  let retry = 0;
  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws?role=overlay`);
    socket.addEventListener("open", () => { retry = 0; syncMoving(); });
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "snapshot") render(message.snapshot);
        else if (message.type === "overlay-edit") setMoving(!!message.on);
      } catch { /* ignore malformed frames */ }
    });
    socket.addEventListener("close", () => {
      retry = Math.min(retry + 1, 6);
      setTimeout(connect, 500 * retry);
    });
  }

  window.SpikeSightTheme.sync();
  renderPool(null);
  reportSize();
  connect();
})();
