#!/usr/bin/env python3
"""
generate_site_data.py
Merges data from Warren (papers.json), Mark (knowledge/*.md), and watchlist.json
→ generates data/stocks.json and knowledge.js for the public website.

Usage:  python3 scripts/generate_site_data.py
"""

import json
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).parent.parent
OAT_OS         = ROOT.parent
INVESTMENT_DIR = OAT_OS / "investment-system"
WATCHLIST_JSON = OAT_OS / "kim-line-bot/config/watchlist.json"
PAPERS_JSON    = INVESTMENT_DIR / "portfolio/papers.json"
KNOWLEDGE_DIR  = OAT_OS / "oat-investment-knowledge/knowledge"

STOCKS_BASE    = ROOT / "data/stocks_base.json"
OUT_STOCKS     = ROOT / "data/stocks.json"
OUT_KNOWLEDGE  = ROOT / "knowledge.js"

# ── Mappings ──────────────────────────────────────────────────────────────────
CONVICTION_MAP = {
    "VERY HIGH": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "VERY LOW": 1,
}

ACTION_MAP = {
    "BUY":   ("buy",   "Buy"),
    "SELL":  ("sell",  "Sell"),
    "HOLD":  ("hold",  "Hold"),
    "WATCH": ("watch", "Watch"),
}

CATEGORY_MAP = {
    "semiconductor":    ("Semiconductor", "semi"),
    "ai_semiconductor": ("Semiconductor", "semi"),
    "big_tech":         ("Big Tech",      "bigtech"),
    "ev_energy":        ("Consumer",      "consumer"),
    "entertainment":    ("Consumer",      "consumer"),
    "consumer":         ("Consumer",      "consumer"),
    "diversified":      ("Diversified",   "diversified"),
    "index":            None,   # skip ETF indices
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def score_color(v):
    if v is None:
        return "amber"
    return "green" if v >= 8.0 else "amber" if v >= 6.5 else "orange"

def waf_badge_composite(waf):
    if waf and waf >= 7.5:
        return "waf-mid", "waf-composite-blue"
    return "waf-low", "waf-composite-amber"

def make_scores(bq, gp, va, ra):
    def e(v):
        if v is None:
            return [0.0, "amber", 0]
        return [round(float(v), 1), score_color(v), round(float(v) * 10)]
    return {"bq": e(bq), "gp": e(gp), "va": e(va), "ra": e(ra)}

def load_json(path, label):
    if not path.exists():
        print(f"[warn] ไม่พบ {label}: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        print(f"[warn] อ่าน {label} ไม่ได้: {ex}")
        return None

# ── Load sources ──────────────────────────────────────────────────────────────
def load_papers(papers_data):
    """Return {TICKER: recommendation_dict} from latest paper."""
    if not papers_data:
        return {}, ""
    sorted_p = sorted(papers_data, key=lambda p: p.get("date", ""), reverse=True)
    latest = sorted_p[0]
    date   = latest.get("date", "")
    result = {}
    for rec in latest.get("recommendations", []):
        t = (rec.get("ticker") or "").upper().strip()
        if t:
            result[t] = rec
    print(f"[papers] latest: {date}, tickers: {sorted(result)}")
    return result, date

def load_watchlist(watchlist_data):
    """Return {TICKER: stock_dict}."""
    if not watchlist_data:
        return {}
    result = {}
    for s in watchlist_data.get("stocks", []):
        sym = (s.get("symbol") or "").upper().strip()
        if sym:
            result[sym] = s
    return result

# ── Build individual stock entry ──────────────────────────────────────────────
def build_entry(ticker, base, paper_rec, watchlist_info, paper_date):
    entry = dict(base) if base else {}

    # Override dynamic fields from Warren's paper
    if paper_rec:
        s = paper_rec.get("scores") or {}
        va = s.get("va") or {}
        bq = s.get("bq") or {}
        gp = s.get("gp") or {}
        ra = s.get("ra") or {}

        action_str   = (paper_rec.get("action") or "HOLD").upper()
        action, albl = ACTION_MAP.get(action_str, ("hold", "Hold"))
        conv_str     = (paper_rec.get("conviction") or "").upper()
        conviction   = CONVICTION_MAP.get(conv_str, 3)
        waf          = paper_rec.get("waf")
        badge, comp  = waf_badge_composite(waf)

        bq_s = bq.get("score")
        gp_s = gp.get("score")
        va_s = va.get("score")
        ra_s = ra.get("score")

        entry.update({
            "action":       action,
            "actionLabel":  albl,
            "conviction":   conviction,
            "waf":          waf,
            "wafBadge":     badge,
            "wafComposite": comp,
            "price":        va.get("price"),
            "fv":           va.get("fair_value_base"),
            "mos":          va.get("mos_pct"),
            "fpe":          va.get("forward_pe"),
            "peg":          va.get("peg"),
            "scores":       make_scores(bq_s, gp_s, va_s, ra_s),
            "idea":         paper_rec.get("investment_idea") or entry.get("idea", ""),
            "updated":      paper_date,
        })

        # Fill bull/risk from thesis if base doesn't have them yet
        thesis      = paper_rec.get("thesis", "")
        thesis_risk = paper_rec.get("thesis_risk", "")
        if thesis and not entry.get("bull"):
            entry["bull"] = [thesis]
        if thesis_risk and not entry.get("risk"):
            entry["risk"] = [thesis_risk]

    # Fill metadata from watchlist for new stocks not in stocks_base.json
    if watchlist_info and not entry.get("name"):
        cat        = watchlist_info.get("category", "")
        sector_info = CATEGORY_MAP.get(cat)
        if sector_info:
            sector, slug = sector_info
            entry.setdefault("name",       watchlist_info.get("display", ticker))
            entry.setdefault("sector",     sector)
            entry.setdefault("sectorSlug", slug)
            entry.setdefault("chips",      [sector])
            entry.setdefault("bull",       [])
            entry.setdefault("risk",       [])

    return entry

# ── Generate stocks.json ──────────────────────────────────────────────────────
def generate_stocks_json(base_data, papers, paper_date, watchlist):
    stocks = {}

    # 1. All stocks from stocks_base.json
    for ticker, base in base_data.items():
        paper_rec     = papers.get(ticker)
        watchlist_info = watchlist.get(ticker)
        entry = build_entry(ticker, base, paper_rec, watchlist_info, paper_date)
        stocks[ticker] = entry
        src = "base+paper" if paper_rec else "base"
        print(f"[stock] {ticker:6}  ({src})")

    # 2. New stocks in papers.json not yet in stocks_base.json
    for ticker, paper_rec in papers.items():
        if ticker in stocks:
            continue
        watchlist_info = watchlist.get(ticker)
        # Skip if not in watchlist (e.g. SGOV cash ETF)
        if not watchlist_info:
            print(f"[stock] {ticker:6}  (skip — not in watchlist)")
            continue
        # Skip index ETFs
        cat = watchlist_info.get("category", "")
        if CATEGORY_MAP.get(cat) is None:
            continue
        entry = build_entry(ticker, None, paper_rec, watchlist_info, paper_date)
        if not entry.get("name"):
            continue
        stocks[ticker] = entry
        print(f"[stock] {ticker:6}  (new from papers+watchlist)")

    # Sort by WAF descending
    sorted_stocks = dict(
        sorted(stocks.items(), key=lambda x: (x[1].get("waf") or 0), reverse=True)
    )

    OUT_STOCKS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STOCKS.write_text(json.dumps(sorted_stocks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ stocks.json → {len(sorted_stocks)} stocks")

# ── Generate knowledge.js ─────────────────────────────────────────────────────
def generate_knowledge_js():
    entries = {}

    if not KNOWLEDGE_DIR.exists():
        print(f"[warn] knowledge dir not found: {KNOWLEDGE_DIR}")
        return

    SKIP_DIRS = {"research"}  # skip non-stock files

    for md_file in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        if md_file.stem.startswith("_"):
            continue
        if md_file.parent.name in SKIP_DIRS:
            continue
        ticker  = md_file.stem.upper()
        content = md_file.read_text(encoding="utf-8")

        # Escape for JS template literal
        content = content.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        entries[ticker] = content
        print(f"[knowledge] {ticker} ({len(content):,} chars)")

    lines = [
        "// Auto-generated by generate_site_data.py — do not edit manually\n",
        "window.STOCK_KNOWLEDGE = {\n\n",
    ]
    for ticker, content in entries.items():
        lines.append(f'"{ticker}": `{content}`,\n\n')
    lines.append("};\n")

    OUT_KNOWLEDGE.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ knowledge.js → {len(entries)} tickers")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("generate_site_data.py")
    print("=" * 60)

    base_data     = load_json(STOCKS_BASE,    "stocks_base.json") or {}
    papers_raw    = load_json(PAPERS_JSON,    "papers.json")      or []
    watchlist_raw = load_json(WATCHLIST_JSON, "watchlist.json")   or {}

    papers, paper_date = load_papers(papers_raw)
    watchlist          = load_watchlist(watchlist_raw)

    print(f"\n── Stocks ─────────────────────────────────────────────────")
    generate_stocks_json(base_data, papers, paper_date, watchlist)

    print(f"\n── Knowledge ──────────────────────────────────────────────")
    generate_knowledge_js()

    print("\n✅ Done")
