/* oatinvestor — shared scripts (theme toggle + nav active state) */

(function () {
  // ---- Theme (default: dark) ----
  const KEY = "oatinvestor.theme";
  const root = document.documentElement;
  const stored = localStorage.getItem(KEY);
  const initial = stored || "dark";
  root.setAttribute("data-theme", initial);

  // ---- Brand accent (synced from tweaks panel on stock-nvda.html) ----
  try {
    const accent = localStorage.getItem("oatinvestor.accent");
    if (accent && /^#[0-9a-f]{6}$/i.test(accent)) {
      const h = accent.replace("#", "");
      const n = parseInt(h, 16);
      const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].join(", ");
      root.style.setProperty("--amber", accent);
      root.style.setProperty("--amber-soft", `rgba(${rgb}, 0.12)`);
      root.style.setProperty("--amber-line", `rgba(${rgb}, 0.28)`);
      root.style.setProperty("--glow", `rgba(${rgb}, 0.22)`);
    }
  } catch (e) {}

  function setTheme(t) {
    root.setAttribute("data-theme", t);
    localStorage.setItem(KEY, t);
    updateToggleIcon(t);
  }
  function updateToggleIcon(t) {
    document.querySelectorAll("[data-theme-toggle]").forEach((el) => {
      el.setAttribute("aria-label", t === "dark" ? "Switch to light" : "Switch to dark");
      const sun = el.querySelector("[data-icon-sun]");
      const moon = el.querySelector("[data-icon-moon]");
      if (sun && moon) {
        sun.style.display = t === "dark" ? "block" : "none";
        moon.style.display = t === "dark" ? "none" : "block";
      }
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    const cur = root.getAttribute("data-theme") || "dark";
    setTheme(cur === "dark" ? "light" : "dark");
  });

  document.addEventListener("DOMContentLoaded", () => {
    updateToggleIcon(initial);

    // Mark active nav link
    const path = window.location.pathname.split("/").pop() || "index.html";
    document.querySelectorAll("[data-nav]").forEach((el) => {
      if (el.getAttribute("data-nav") === path.replace(".html", "") ||
          (path === "" && el.getAttribute("data-nav") === "index")) {
        el.classList.add("is-active");
      }
    });

    // Filter tabs (stocks page) — re-query cards on each click to support dynamic rendering
    document.querySelectorAll("[data-filter-group]").forEach((group) => {
      const tabs = group.querySelectorAll("[data-filter]");
      tabs.forEach((tab) =>
        tab.addEventListener("click", () => {
          tabs.forEach((t) => t.classList.remove("is-active"));
          tab.classList.add("is-active");
          const v = tab.getAttribute("data-filter");
          document.querySelectorAll("[data-sector]").forEach((c) => {
            const s = c.getAttribute("data-sector");
            c.style.display = v === "all" || s === v ? "" : "none";
          });
        })
      );
    });
  });
})();
