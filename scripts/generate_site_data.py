#!/usr/bin/env python3
"""
generate_site_data.py
Merges data from Warren (watchlist_valuations.json), Mark (knowledge/*.md), and watchlist.json
→ generates data/stocks.json and knowledge.js for the public website.

Usage:  python3 scripts/generate_site_data.py
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── Paths — support env var overrides for CI/GitHub Actions ───────────────────
ROOT   = Path(__file__).parent.parent
OAT_OS = ROOT.parent

_INVESTMENT_DIR = OAT_OS / "investment-system"
_KNOWLEDGE_REPO = OAT_OS / "oat-investment-knowledge"

WATCHLIST_JSON = Path(os.environ.get(
    "WATCHLIST_JSON",
    str(OAT_OS / "kim-line-bot/config/watchlist.json"),
))
WATCHLIST_VALUATIONS_JSON = Path(os.environ.get(
    "WATCHLIST_VALUATIONS_JSON",
    str(_INVESTMENT_DIR / "portfolio/watchlist_valuations.json"),
))
KNOWLEDGE_DIR = Path(os.environ.get(
    "KNOWLEDGE_DIR",
    str(_KNOWLEDGE_REPO / "knowledge"),
))
CHARLIE_WATCHLIST_JSON = Path(os.environ.get(
    "CHARLIE_WATCHLIST_JSON",
    str(_INVESTMENT_DIR / "portfolio/charlie_watchlist_reviews.json"),
))
PAPERS_JSON = Path(os.environ.get(
    "PAPERS_JSON",
    str(_INVESTMENT_DIR / "portfolio/papers.json"),
))
CHARLIE_REVIEWS_JSON = Path(os.environ.get(
    "CHARLIE_REVIEWS_JSON",
    str(_INVESTMENT_DIR / "portfolio/charlie_reviews.json"),
))

OUT_STOCKS     = ROOT / "data/stocks.json"
OUT_KNOWLEDGE  = ROOT / "knowledge.js"
OUT_PAPERS     = ROOT / "data/papers.json"
OUT_CHARLIE    = ROOT / "data/charlie_reviews.json"

# ── Mappings ──────────────────────────────────────────────────────────────────
CONVICTION_MAP = {
    "VERY HIGH": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "VERY LOW": 1,
}

ACTION_MAP = {
    "BUY":     ("buy",     "Buy"),
    "STARTER": ("starter", "Starter"),
    "SELL":    ("sell",    "Sell"),
    "HOLD":    ("hold",    "Hold"),
    "WATCH":   ("watch",   "Watch"),
    "STUDY":   ("study",   "Study"),
}

CATEGORY_MAP = {
    "semiconductor":    ("Semiconductor", "semi"),
    "ai_semiconductor": ("Semiconductor", "semi"),
    "big_tech":         ("Big Tech",      "bigtech"),
    "ev_energy":        ("Consumer",      "consumer"),
    "entertainment":    ("Consumer",      "consumer"),
    "consumer":         ("Consumer",      "consumer"),
    "diversified":      ("Diversified",   "diversified"),
    "infrastructure":   ("Infrastructure","infra"),
    "software":         ("Software",      "software"),
    "materials":        ("Materials",     "materials"),
    "defense":          ("Defense",       "defense"),
    "pre_ipo":          ("Space & AI",    "space"),
    "space":            ("Space & AI",    "space"),
    "ai_cloud":         ("AI Cloud",      "aicloud"),
    "fintech":          ("Fintech",       "fintech"),
    "healthcare":       ("Healthcare",    "healthcare"),
    "energy":           ("Energy",        "energy"),
    "public_safety":    ("Public Safety", "publicsafety"),
    "index":            None,   # skip ETF indices
    "private":          None,   # skip private ecosystem-watch only
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

def parse_wiki_valuation(content):
    """Extract FV/MoS/Price/PEG/FwdPE/WAF/scores/action/conviction from ## Valuation Range section.
    Handles two formats:
      Old (paragraph): **ราคาปัจจุบัน:** $XXX, **Weighted Fair Value:** $XXX, **WAF Score:** X.XX | **Conviction:** HIGH
      New (table):     **Price:** $XXX, | Fair Value (Weighted) | $XXX |, | **WAF Total** | **X.XX** | | **HIGH** |
    """
    m = re.search(r'## Valuation Range\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if not m:
        return {}
    section = m.group(1)
    if not section.strip() or re.match(r'^\s*-\s*$', section.strip()):
        return {}

    out = {}

    # Last Updated
    mm = re.search(r'\*\*Last Updated:\*\*\s*([\d-]+)', section)
    if not mm:
        mm = re.search(r'\*\*ประเมิน:\*\*\s*([\d-]+)', section)
    if mm:
        out['updated'] = mm.group(1)

    # Price — old: **ราคาปัจจุบัน:** / **Price ณ วันนั้น:** / new: **Price:** XXX (handles ~ prefix)
    for pat in [r'\*\*ราคาปัจจุบัน:\*\*\s*~?\$?([\d,]+\.?\d*)',
                r'\*\*Price ณ วันนั้น:\*\*\s*~?\$?([\d,]+\.?\d*)',
                r'\*\*Price:\*\*\s*~?\$?([\d,]+\.?\d*)',
                r'\|\s*Current Price\s*\|\s*\$?([\d,]+\.?\d*)\s*\|']:
        mm = re.search(pat, section)
        if mm:
            out['price'] = float(mm.group(1).replace(',', ''))
            break

    # Fair Value Weighted — old: **Weighted Fair Value:** / new: | Fair Value (Weighted) | $XXX |
    # / Watchlist Refresh: | Fair Value Base | $XXX |
    for pat in [r'\*\*Weighted Fair Value:\*\*\s*~?\$?([\d,]+\.?\d*)',
                r'\|\s*Fair Value \(Weighted\)\s*\|\s*\$?([\d,]+\.?\d*)\s*\|',
                r'\|\s*Fair Value Base\s*\|\s*\$?([\d,]+\.?\d*)\s*\|']:
        mm = re.search(pat, section)
        if mm:
            out['fv'] = float(mm.group(1).replace(',', ''))
            break

    # Margin of Safety — old: **Margin of Safety:** / **MoS vs XXX:** / new: | **Margin of Safety** | **XX.X%** |
    for pat in [r'\*\*Margin of Safety:\*\*\s*([+\-−]?[\d.]+)%',
                r'\*\*MoS vs [^:]*:\*\*\s*([+\-−]?[\d.]+)%',
                r'\|\s*\*\*Margin of Safety\*\*\s*\|\s*\*\*([+\-−]?[\d.]+)%\*\*']:
        mm = re.search(pat, section)
        if mm:
            raw = mm.group(1).replace('−', '-')
            out['mos_pct'] = float(raw)
            break

    # PEG — old: **PEG:** / **PEG Ratio:** / new: | **PEG** | **X.XX** |
    for pat in [r'\*\*PEG:\*\*\s*([\d.]+)',
                r'\*\*PEG Ratio:\*\*\s*([\d.]+)',
                r'\|\s*\*\*PEG\*\*\s*\|\s*\*\*([\d.]+)\*\*']:
        mm = re.search(pat, section)
        if mm:
            out['peg'] = float(mm.group(1))
            break

    # Forward P/E — old: Fwd P/E XX.X / new: | Forward P/E | XX.X× |
    for pat in [r'Fwd P/E\s+([\d.]+)',
                r'Forward PE[^\d]*([\d.]+)',
                r'\|\s*Forward P/E\s*\|\s*([\d.]+)×?']:
        mm = re.search(pat, section)
        if mm:
            out['forward_pe'] = float(mm.group(1))
            break

    # WAF Score — old: **WAF Score:** X.XX / new: | **WAF Total** | **X.XX** |
    for pat in [r'\*\*WAF Score:\*\*\s*([\d.]+)',
                r'\|\s*\*\*WAF Total\*\*\s*\|\s*\*\*([\d.]+)\*\*']:
        mm = re.search(pat, section)
        if mm:
            out['waf'] = float(mm.group(1))
            break

    # Conviction — old: **Conviction:** HIGH / new: | **WAF Total** | **X.XX** | | **HIGH** |
    for pat in [r'\*\*Conviction:\*\*\s*([A-Z\s]+?)(?:\s*\||$|\n)',
                r'\*\*WAF Total\*\*[^\n]*?\|\s*\|\s*\*\*([A-Z\s]+?)\*\*']:
        mm = re.search(pat, section)
        if mm:
            conv = mm.group(1).strip()
            if conv:
                out['conviction'] = conv
                break

    # Action — old: **Action:** WATCH/BUY/STUDY / new: **Action:** ✅ BUY / ⏸ WATCH / ❌ AVOID
    mm = re.search(r'\*\*Action:\*\*\s*[✅⏸❌🚫🌓]?\s*(BUY|WATCH\+?|STARTER|HOLD|SELL|STUDY|AVOID|DO NOT BUY)', section)
    if mm:
        act = mm.group(1).strip()
        # Normalize: WATCH+ → WATCH, DO NOT BUY → AVOID
        if act == 'WATCH+':
            act = 'WATCH'
        if act == 'DO NOT BUY':
            act = 'AVOID'
        out['action'] = act

    # BQ/GP/VA/RA scores from WAF Score Breakdown table (new format)
    # | BQ (Business Quality) | 9.5 | 30% | 2.85 |
    for label, key in [('BQ', 'bq'), ('GP', 'gp'), ('VA', 'va'), ('RA', 'ra')]:
        mm = re.search(rf'\|\s*{label}\s*\([^)]*\)\s*\|\s*([\d.]+)\s*\|', section)
        if mm:
            out[f'score_{key}'] = float(mm.group(1))

    # Investment Idea / Thesis / Thesis Risk (new format)
    mm = re.search(r'\*\*Investment Idea:\*\*\s*(.+?)(?=\n\n|\*\*Thesis|\Z)', section, re.DOTALL)
    if mm:
        out['investment_idea'] = mm.group(1).strip()
    mm = re.search(r'\*\*Thesis:\*\*\s*(.+?)(?=\n\n|\*\*Thesis Risk|\*\*Action|\Z)', section, re.DOTALL)
    if mm:
        out['thesis'] = mm.group(1).strip()
    mm = re.search(r'\*\*Thesis Risk:\*\*\s*(.+?)(?=\n\n|\*\*Action|\Z)', section, re.DOTALL)
    if mm:
        out['thesis_risk'] = mm.group(1).strip()

    return out

def parse_wiki_story_gate(content):
    """Extract Story Gate (WHAT/WHY NOW/IF WRONG/Status) from ## Story Gate section.
    Handles two formats:
      New: **WHAT:** content  /  **WHY NOW:** content  /  **Status:** PASS
      Old: **WHAT — Thai label?**\n content  /  **ผลการพิจารณา: PASS**
    Also handles WHY NOW variants: **WHY NOW (Second-Level):** etc.
    """
    m = re.search(r'## Story Gate\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if not m:
        return None
    section = m.group(1)
    if not section.strip():
        return None

    out = {}

    # Status — new: **Status:** PASS/FAIL  |  old: **ผลการพิจารณา: PASS**
    mm = re.search(r'\*\*Status:\*\*\s*(PASS|FAIL)', section)
    if not mm:
        mm = re.search(r'\*\*ผลการพิจารณา:\s*(PASS|FAIL)', section)
    if mm:
        out['passed'] = mm.group(1) == 'PASS'
        out['status'] = mm.group(1)

    # Last Updated — new: **Last Updated:** DATE  |  old: **ประเมิน:** DATE
    mm = re.search(r'\*\*(?:Last Updated|ประเมิน):\*\*\s*([\d-]+)', section)
    if mm:
        out['updated'] = mm.group(1)

    # Tier — **Tier:** 🏛️ Inevitable / 🚀 Pre-Inevitable / 🌱 Fast Grower / 🔁 Cyclical / ⚠️ Turnaround / ❌ Avoid
    # Look in Story Gate section first; fallback to ## Valuation Range (warren_watchlist writes it there)
    mm = re.search(r'\*\*Tier:\*\*\s*([^\n|]+?)(?:\s*\||$|\n)', section)
    if not mm:
        mm = re.search(r'\*\*Tier:\*\*\s*([^\n|]+?)(?:\s*\||$|\n)', content)
    if mm:
        tier_raw = mm.group(1).strip()
        out['tier'] = tier_raw
        # Extract slug for CSS class — strip emoji, lowercase, dash
        tier_text = re.sub(r'[^\w\s-]', '', tier_raw).strip().lower().replace(' ', '-')
        out['tier_slug'] = tier_text

    # WHAT — new: **WHAT:** inline  |  old: **WHAT — label?**\n content
    mm = re.search(r'\*\*WHAT:\*\*\s*(.+?)(?=\n\n|\*\*WHY|\Z)', section, re.DOTALL)
    if not mm:
        mm = re.search(r'\*\*WHAT[^*]+\*\*\s*\n+(.+?)(?=\n\n|\*\*WHY|\Z)', section, re.DOTALL)
    if mm:
        out['what'] = mm.group(1).strip()

    # WHY NOW — variants: **WHY NOW:** / **WHY NOW (...):** / **WHY NOW — label?**\n content
    mm = re.search(r'\*\*WHY NOW[^*]*:\*\*\s*(.+?)(?=\n\n|\*\*IF|\Z)', section, re.DOTALL)
    if not mm:
        mm = re.search(r'\*\*WHY NOW[^*]+\*\*\s*\n+(.+?)(?=\n\n|\*\*IF|\Z)', section, re.DOTALL)
    if mm:
        out['why'] = mm.group(1).strip()

    # IF WRONG — new: **IF WRONG:** inline  |  old: **IF WRONG — label?**\n content
    mm = re.search(r'\*\*IF WRONG:\*\*\s*(.+?)(?=\n\n|\Z)', section, re.DOTALL)
    if not mm:
        mm = re.search(r'\*\*IF WRONG[^*]+\*\*\s*\n+(.+?)(?=\n\n|\Z)', section, re.DOTALL)
    if mm:
        out['risk'] = mm.group(1).strip()

    return out if out else None

def load_wiki_valuations():
    """Return {TICKER: {valuation_dict, story_gate_dict}} parsed from Wiki Card markdown files."""
    result = {}
    story_gates = {}
    if not KNOWLEDGE_DIR.exists():
        return result, story_gates

    for md_file in sorted((KNOWLEDGE_DIR / "stocks").glob("*.md")):
        if md_file.stem.startswith("_"):
            continue
        ticker = md_file.stem.upper()
        content = md_file.read_text(encoding="utf-8")
        val = parse_wiki_valuation(content)
        if val:
            result[ticker] = val
            print(f"[wiki-val] {ticker}: FV={val.get('fv')} MoS={val.get('mos_pct')}% Price={val.get('price')}")
            # Format-drift alarm: section clearly has valuation data (price/MoS parsed)
            # but FV regex found nothing — likely a new table label the parser doesn't know yet.
            if val.get('fv') is None and (val.get('price') is not None or val.get('mos_pct') is not None):
                print(f"  ⚠️  [wiki-val] {ticker}: Valuation section parsed but FV is None — "
                      f"card may use a new Fair Value row label; check parse_wiki_valuation() patterns")
        sg = parse_wiki_story_gate(content)
        if sg:
            story_gates[ticker] = sg
            print(f"[story-gate] {ticker}: {sg.get('status','?')} — {(sg.get('what') or '')[:60]}")

    return result, story_gates

# ── Load sources ──────────────────────────────────────────────────────────────
def load_papers(papers_data):
    """Return {TICKER: recommendation_dict} carrying forward the most recent
    rec per ticker across ALL batches.

    A watchlist run may cover only a subset of tickers (a partial re-run).
    Reading only the single latest date would drop every ticker not in that
    batch back to stale wiki-card data — that is how STARTER tickers analysed
    on an earlier date silently disappeared. Instead iterate oldest→newest
    across all batches so each ticker keeps its most recent Warren rec; the
    rec is stamped with its own batch date (_date) for an accurate timestamp.
    The returned date is the overall latest (used only as a fallback).
    """
    if not papers_data:
        return {}, ""
    sorted_p = sorted(papers_data, key=lambda p: p.get("date", ""))  # oldest→newest
    date   = sorted_p[-1].get("date", "")
    result = {}
    for entry in sorted_p:
        edate = entry.get("date", "")
        for rec in entry.get("recommendations", []):
            t = (rec.get("ticker") or "").upper().strip()
            if t:
                rec = dict(rec)
                rec["_date"] = edate
                result[t] = rec  # newer batch overrides older per ticker
    print(f"[papers] merged {len(sorted_p)} batches, latest: {date}, tickers: {sorted(result)}")
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

def load_charlie_watchlist(reviews_data):
    """Return {TICKER: charlie_review_dict} from latest charlie watchlist review."""
    if not reviews_data:
        return {}
    sorted_r = sorted(reviews_data, key=lambda r: r.get("date", ""), reverse=True)
    latest = sorted_r[0]
    ticker_reviews = latest.get("ticker_reviews", {})
    result = {}
    for ticker, rev in ticker_reviews.items():
        result[ticker.upper()] = {
            "verdict":   rev.get("verdict", ""),
            "summary":   rev.get("summary", ""),
            "risks":     rev.get("risks", []),
            "yf_price":  rev.get("yf_price"),
            "warren_fv": rev.get("warren_fv"),
            "date":      rev.get("data_date") or latest.get("date", ""),
        }
    if result:
        print(f"[charlie-wl] latest: {latest.get('date')}, tickers: {sorted(result)}")
    return result

# ── Build individual stock entry ──────────────────────────────────────────────
def build_entry(ticker, base, paper_rec, watchlist_info, paper_date, wiki_val=None, charlie_rev=None, story_gate=None):
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

        # price/fv/mos stored at scores level (not scores.va) in corrected entries
        price = va.get("price") or s.get("price")
        fv    = va.get("fair_value_base") or s.get("fair_value_base")
        mos   = va.get("mos_pct") if va.get("mos_pct") is not None else s.get("mos_pct")
        fpe   = va.get("forward_pe") or s.get("forward_pe")
        peg   = va.get("peg") or s.get("peg")

        entry.update({
            "action":       action,
            "actionLabel":  albl,
            "conviction":   conviction,
            "waf":          waf,
            "wafBadge":     badge,
            "wafComposite": comp,
            "price":        price,
            "fv":           fv,
            "mos":          mos,
            "fpe":          fpe,
            "peg":          peg,
            "scores":       make_scores(bq_s, gp_s, va_s, ra_s),
            "idea":         paper_rec.get("investment_idea") or entry.get("idea", ""),
            "updated":      paper_rec.get("_date") or paper_date,
        })

        # Fill bull/risk from thesis if base doesn't have them yet
        thesis      = paper_rec.get("thesis", "")
        thesis_risk = paper_rec.get("thesis_risk", "")
        if thesis and not entry.get("bull"):
            entry["bull"] = [thesis]
        if thesis_risk and not entry.get("risk"):
            entry["risk"] = [thesis_risk]

    # Wiki Card valuation as fallback — fills fields missing from watchlist_valuations.json
    if wiki_val:
        if 'price'      in wiki_val and entry.get('price') is None:   entry['price']   = wiki_val['price']
        if 'fv'         in wiki_val and entry.get('fv') is None:       entry['fv']       = wiki_val['fv']
        if 'mos_pct'    in wiki_val and entry.get('mos') is None:      entry['mos']      = wiki_val['mos_pct']
        if 'peg'        in wiki_val and entry.get('peg') is None:      entry['peg']      = wiki_val['peg']
        if 'forward_pe' in wiki_val and entry.get('fpe') is None:      entry['fpe']      = wiki_val['forward_pe']
        if 'updated'    in wiki_val and entry.get('updated') is None:  entry['updated']  = wiki_val['updated']

        # WAF + Conviction + Action fallback (for SKIP tickers not in watchlist_valuations)
        if 'waf' in wiki_val and entry.get('waf') is None:
            entry['waf'] = wiki_val['waf']
            badge, comp = waf_badge_composite(wiki_val['waf'])
            entry['wafBadge'] = badge
            entry['wafComposite'] = comp
        if 'conviction' in wiki_val and entry.get('conviction') is None:
            # Normalize: MED → MEDIUM
            conv_norm = wiki_val['conviction'].upper().strip()
            if conv_norm == 'MED':
                conv_norm = 'MEDIUM'
            entry['conviction'] = CONVICTION_MAP.get(conv_norm, 3)
        if 'action' in wiki_val and entry.get('action') is None:
            act_str = wiki_val['action'].upper()
            action, albl = ACTION_MAP.get(act_str, ("hold", "Hold"))
            entry['action'] = action
            entry['actionLabel'] = albl
        elif entry.get('action') is None and wiki_val.get('waf') is not None:
            # Derive action from WAF + MoS if not stated explicitly
            waf = wiki_val['waf']
            mos = wiki_val.get('mos_pct', 0)
            if waf >= 6.5 and mos >= 15:
                entry['action'], entry['actionLabel'] = 'buy', 'Buy'
            elif waf >= 6.5:
                entry['action'], entry['actionLabel'] = 'watch', 'Watch'
            elif waf >= 5:
                entry['action'], entry['actionLabel'] = 'hold', 'Hold'
            else:
                entry['action'], entry['actionLabel'] = 'study', 'Study'

        # Scores fallback
        if entry.get('scores') is None and any(f'score_{k}' in wiki_val for k in ['bq','gp','va','ra']):
            entry['scores'] = make_scores(
                wiki_val.get('score_bq'),
                wiki_val.get('score_gp'),
                wiki_val.get('score_va'),
                wiki_val.get('score_ra'),
            )

        # Investment idea fallback
        if 'investment_idea' in wiki_val and not entry.get('idea'):
            entry['idea'] = wiki_val['investment_idea']
        if 'thesis' in wiki_val and not entry.get('bull'):
            entry['bull'] = [wiki_val['thesis']]
        if 'thesis_risk' in wiki_val and not entry.get('risk'):
            entry['risk'] = [wiki_val['thesis_risk']]

    # Story Gate (from Wiki Card ## Story Gate section)
    if story_gate:
        entry["story_gate"] = story_gate

    # Top-level tier — prefer watchlist_valuations rec tier (latest Warren analysis,
    # same source price/fv/mos/action already trust), fall back to Story Gate tier
    # from the wiki card only when this ticker has no rec yet (e.g. brand new ticker).
    # Story Gate section is updated less often than the JSON, so preferring it first
    # caused stale tier badges (e.g. NFLX stuck at Pre-Inevitable after a 07-12 rec
    # upgrade to Inevitable that never got written back into NFLX.md).
    if paper_rec and paper_rec.get("tier"):
        entry["tier"] = paper_rec["tier"]
    elif story_gate and story_gate.get("tier"):
        entry["tier"] = story_gate["tier"]

    # Charlie Watchlist Review
    if charlie_rev:
        entry["charlie"] = charlie_rev

    # Fill metadata from watchlist if not already set by paper
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
def generate_stocks_json(papers, paper_date, watchlist, wiki_vals=None, charlie_wl=None, story_gates=None):
    stocks = {}
    wiki_vals    = wiki_vals    or {}
    charlie_wl   = charlie_wl   or {}
    story_gates  = story_gates  or {}

    # All stocks from watchlist.json (single source of truth)
    for ticker, wl_entry in watchlist.items():
        cat = wl_entry.get("category", "")
        if CATEGORY_MAP.get(cat) is None:
            continue  # skip index ETFs
        paper_rec   = papers.get(ticker)
        wiki_val    = wiki_vals.get(ticker)
        charlie_rev = charlie_wl.get(ticker)
        story_gate  = story_gates.get(ticker)
        entry = build_entry(ticker, wl_entry, paper_rec, wl_entry, paper_date, wiki_val, charlie_rev, story_gate)
        stocks[ticker] = entry
        src = "+".join(filter(None, [
            "watchlist",
            "paper"   if paper_rec   else None,
            "wiki"    if wiki_val    else None,
            "charlie" if charlie_rev else None,
        ]))
        print(f"[stock] {ticker:6}  ({src})")

    # Stocks in papers.json not yet in watchlist (e.g. SGOV)
    for ticker, paper_rec in papers.items():
        if ticker in stocks:
            continue
        print(f"[stock] {ticker:6}  (skip — not in watchlist)")

    # Sort by WAF descending
    sorted_stocks = dict(
        sorted(stocks.items(), key=lambda x: (x[1].get("waf") or 0), reverse=True)
    )

    OUT_STOCKS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STOCKS.write_text(json.dumps(sorted_stocks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ stocks.json → {len(sorted_stocks)} stocks")

# ── Generate knowledge.js ─────────────────────────────────────────────────────
def generate_knowledge_js(watchlist_tickers=None):
    entries = {}

    if not KNOWLEDGE_DIR.exists():
        print(f"[warn] knowledge dir not found: {KNOWLEDGE_DIR}")
        return

    for md_file in sorted((KNOWLEDGE_DIR / "stocks").glob("*.md")):
        if md_file.stem.startswith("_"):
            continue
        ticker  = md_file.stem.upper()
        if watchlist_tickers and ticker not in watchlist_tickers:
            continue  # skip tickers not in watchlist
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

    # Update ?v=timestamp in all HTML files that load knowledge.js
    version = datetime.now().strftime('%Y%m%d%H%M%S')
    for fname in ['stock.html', 'stocks.html', 'index.html']:
        p = ROOT / fname
        if not p.exists():
            continue
        txt = p.read_text(encoding='utf-8')
        new_txt = re.sub(r'knowledge\.js(?:\?v=\d+)?', f'knowledge.js?v={version}', txt)
        if new_txt != txt:
            p.write_text(new_txt, encoding='utf-8')
            print(f'[cache-bust] {fname} → knowledge.js?v={version}')

# ── Sync STOCKS_INLINE in stock.html ─────────────────────────────────────────
def generate_stocks_inline():
    """Regenerate the STOCKS_INLINE fallback in stock.html from current stocks.json."""
    stock_html = ROOT / "stock.html"
    if not stock_html.exists() or not OUT_STOCKS.exists():
        return

    stocks = json.loads(OUT_STOCKS.read_text(encoding="utf-8"))

    def js_str(v):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        # Escape single quotes and backslashes for single-quoted JS string
        return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"

    def js_arr(lst):
        return "[" + ", ".join(js_str(x) for x in (lst or [])) + "]"

    lines = ["const STOCKS_INLINE = {\n"]
    for ticker, s in stocks.items():
        jn = lambda v: "null" if v is None else repr(v)  # js number/null
        sc = s.get("scores") or {}
        bq = sc.get("bq", [None, "amber", 50])
        gp = sc.get("gp", [None, "amber", 50])
        va = sc.get("va", [None, "amber", 50])
        ra = sc.get("ra", [None, "amber", 50])
        scores_js = (
            f"{{ bq: [{jn(bq[0])},{js_str(bq[1])},{jn(bq[2])}], "
            f"gp: [{jn(gp[0])},{js_str(gp[1])},{jn(gp[2])}], "
            f"va: [{jn(va[0])},{js_str(va[1])},{jn(va[2])}], "
            f"ra: [{jn(ra[0])},{js_str(ra[1])},{jn(ra[2])}] }}"
        )
        chips_js = js_arr(s.get("chips", []))
        bull_js  = js_arr(s.get("bull", []))
        risk_js  = js_arr(s.get("risk", []))
        sg = s.get("story_gate") or {}
        sg_js = "null"
        if sg:
            sg_js = (
                f"{{ passed: {'true' if sg.get('passed') else 'false'}, "
                f"status: {js_str(sg.get('status'))}, "
                f"tier: {js_str(sg.get('tier'))}, "
                f"tier_slug: {js_str(sg.get('tier_slug'))}, "
                f"what: {js_str(sg.get('what'))}, "
                f"why: {js_str(sg.get('why'))}, "
                f"risk: {js_str(sg.get('risk'))}, "
                f"updated: {js_str(sg.get('updated'))} }}"
            )
        lines.append(
            f"  {ticker}: {{\n"
            f"    name: {js_str(s.get('name'))}, sector: {js_str(s.get('sector'))}, sectorSlug: {js_str(s.get('sectorSlug'))},\n"
            f"    chips: {chips_js},\n"
            f"    action: {js_str(s.get('action'))}, actionLabel: {js_str(s.get('actionLabel'))}, conviction: {s.get('conviction', 3)},\n"
            f"    waf: {jn(s.get('waf'))}, wafBadge: {js_str(s.get('wafBadge'))}, wafComposite: {js_str(s.get('wafComposite'))},\n"
            f"    price: {jn(s.get('price'))}, fv: {jn(s.get('fv'))}, mos: {jn(s.get('mos'))}, fpe: {jn(s.get('fpe'))}, peg: {jn(s.get('peg'))},\n"
            f"    scores: {scores_js},\n"
            f"    story_gate: {sg_js},\n"
            f"    idea: {js_str(s.get('idea'))},\n"
            f"    bull: {bull_js},\n"
            f"    risk: {risk_js},\n"
            f"    updated: {js_str(s.get('updated'))}\n"
            f"  }},\n"
        )
    lines.append("};\n")
    new_inline = "".join(lines)

    html = stock_html.read_text(encoding="utf-8")
    new_html = re.sub(
        r'const STOCKS_INLINE = \{.*?\};\n',
        new_inline,
        html,
        flags=re.DOTALL
    )
    if new_html != html:
        stock_html.write_text(new_html, encoding="utf-8")
        print(f"[inline-sync] stock.html STOCKS_INLINE updated ({len(stocks)} tickers)")
    else:
        print("[inline-sync] stock.html STOCKS_INLINE already up to date")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("generate_site_data.py")
    print("=" * 60)

    papers_raw    = load_json(WATCHLIST_VALUATIONS_JSON, "watchlist_valuations.json") or []
    watchlist_raw = load_json(WATCHLIST_JSON, "watchlist.json")   or {}

    charlie_wl_raw = load_json(CHARLIE_WATCHLIST_JSON, "charlie_watchlist_reviews.json") or []

    papers, paper_date = load_papers(papers_raw)
    watchlist          = load_watchlist(watchlist_raw)

    print(f"\n── Wiki Card Valuations + Story Gates ─────────────────────")
    wiki_vals, story_gates = load_wiki_valuations()
    if not wiki_vals:
        print("[wiki-val] ไม่พบข้อมูลใน Wiki Card ใดเลย")
    if not story_gates:
        print("[story-gate] ยังไม่มี Story Gate ใน Wiki Card — รัน /warren watchlist เพื่อเพิ่ม")

    print(f"\n── Charlie Watchlist Reviews ──────────────────────────────")
    charlie_wl = load_charlie_watchlist(charlie_wl_raw)
    if not charlie_wl:
        print("[charlie-wl] ยังไม่มี charlie_watchlist_reviews.json หรือ array ว่าง")

    print(f"\n── Stocks ─────────────────────────────────────────────────")
    generate_stocks_json(papers, paper_date, watchlist, wiki_vals, charlie_wl, story_gates)

    print(f"\n── Knowledge ──────────────────────────────────────────────")
    generate_knowledge_js(watchlist_tickers=set(watchlist.keys()))

    print(f"\n── Inline Sync ────────────────────────────────────────────")
    generate_stocks_inline()

    print(f"\n── Papers & Charlie Reviews → data/ ───────────────────────")
    for src, dst, label in [
        (PAPERS_JSON, OUT_PAPERS, "papers.json"),
        (CHARLIE_REVIEWS_JSON, OUT_CHARLIE, "charlie_reviews.json"),
    ]:
        if src.exists():
            import shutil
            shutil.copy2(src, dst)
            print(f"[copy] {label} → data/{label}")
        else:
            print(f"[skip] {label} ไม่พบ: {src}")

    print("\n✅ Done")
