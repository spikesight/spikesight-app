/* Theme, applied before the first paint.

Loaded synchronously in <head> so a light-theme user never sees a dark flash.
The server holds the real setting (the overlay window runs in its own browser
profile and cannot see this one's storage); localStorage is only a cache of it
so the very first frame is already right.
*/
(() => {
  const THEMES = ["dark", "light", "system"];
  const KEY = "spikesight.theme";
  const media = window.matchMedia("(prefers-color-scheme: light)");
  let chosen = "dark";

  function resolve(name) {
    if (name === "system") return media.matches ? "light" : "dark";
    return name === "light" ? "light" : "dark";
  }

  function apply(name) {
    chosen = THEMES.includes(name) ? name : "dark";
    document.documentElement.dataset.theme = resolve(chosen);
    try { localStorage.setItem(KEY, chosen); } catch { /* ignore */ }
  }

  media.addEventListener("change", () => {
    if (chosen === "system") apply("system");
  });

  let cached = null;
  try { cached = localStorage.getItem(KEY); } catch { /* ignore */ }
  apply(cached || "dark");

  window.SpikeSightTheme = {
    apply,
    get current() { return chosen; },
    // Pull the saved setting from the server; it wins over the cache.
    async sync() {
      try {
        const response = await fetch("/api/settings", { cache: "no-store" });
        if (!response.ok) return chosen;
        const data = await response.json();
        if (data.settings && data.settings.theme) apply(data.settings.theme);
      } catch { /* offline: keep the cached one */ }
      return chosen;
    },
  };
})();
