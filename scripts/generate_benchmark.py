"""
generate_benchmark.py
Reads trade history from Google Sheet via Service Account,
calculates Portfolio vs VOO vs QQQM using yfinance prices,
and writes data/benchmark.json.

Requires: google-api-python-client google-auth
Credential file: /Users/wittayanan/DATA/Credentials/oat-portfolio-dd6c8e0730ab.json
"""

import io
import json
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID    = "1jlSF2S6e6wSkf2KnnfmC9-zIx-RjRg8y_L69FUnL_90"
CRED_FILE   = os.environ.get(
    "GOOGLE_CRED_FILE",
    "/Users/wittayanan/DATA/Credentials/oat-portfolio-dd6c8e0730ab.json"
)

OUT_FILE    = Path(__file__).parent.parent / "data" / "benchmark.json"

# ── Helpers ───────────────────────────────────────────────────────────────────
THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4,
    "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.":  7, "ส.ค.":  8,
    "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}


def clean_val(s) -> str:
    """Strip leading apostrophe (Google Sheets text-prefix) and whitespace."""
    s = str(s).strip() if s is not None else ""
    return s.lstrip("'").strip()


def clean_num(s) -> float | None:
    s = clean_val(s)
    if not s or s.lower() == "nan":
        return None
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s) -> date | None:
    s = clean_val(s)
    if not s or s.lower() == "nan":
        return None

    # Thai short format: "26 ธ.ค. 2025"
    m = re.match(r"(\d{1,2})\s+(\S+)\s+(\d{4})", s)
    if m:
        day, mon_str, yr = int(m[1]), m[2], int(m[3])
        if mon_str in THAI_MONTHS:
            if yr > 2500:
                yr -= 543
            try:
                return date(yr, THAI_MONTHS[mon_str], day)
            except ValueError:
                pass

    # Standard formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # D/M/YYYY with Buddhist Era
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mo, yr = int(m[1]), int(m[2]), int(m[3])
        if yr > 2500:
            yr -= 543
        try:
            return date(yr, mo, d)
        except ValueError:
            pass

    return None


# ── Fetch sheet rows via Service Account ──────────────────────────────────────
def fetch_sheet_rows() -> list[list[str]]:
    """Return all rows from the main Transaction sheet as list of string lists."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("[fetch] ERROR: google-api-python-client not installed — pip install google-api-python-client google-auth")
        sys.exit(1)

    # Support credentials from env var (GitHub Actions Secret) or file path
    cred_json_str = os.environ.get("GOOGLE_CRED_JSON", "")
    if cred_json_str:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp.write(cred_json_str)
            tmp_path = tmp.name
        creds = service_account.Credentials.from_service_account_file(
            tmp_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        os.unlink(tmp_path)
        print("[fetch] using GOOGLE_CRED_JSON env var")
    elif Path(CRED_FILE).exists():
        creds = service_account.Credentials.from_service_account_file(
            CRED_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        print(f"[fetch] using credential file: {CRED_FILE}")
    else:
        print(f"[fetch] ERROR: no credentials found (GOOGLE_CRED_JSON env or {CRED_FILE})")
        sys.exit(1)
    service = build("sheets", "v4", credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Transaction"
    ).execute()
    raw_rows = result.get("values", [])
    # Pad each row to at least 36 cols and convert all to str
    out = []
    for row in raw_rows:
        padded = [str(c).strip() if c is not None else "" for c in row]
        while len(padded) < 36:
            padded.append("")
        out.append(padded)
    print(f"[fetch] {len(out)} rows from Service Account")
    return out


# ── Fetch trade history ───────────────────────────────────────────────────────
def fetch_trades() -> pd.DataFrame:
    """
    Sheet columns (0-indexed):
      Transactions: N(13)=date  O(14)=ticker  P(15)=action  Q(16)=shares
                    R(17)=price S(18)=fee      T(19)=total
      Deposits:     AI(34)=date AJ(35)=USD amount
    Headers on row index 2, data starts index 3.
    """
    print(f"[fetch] loading sheet via Service Account …")
    rows = fetch_sheet_rows()
    records = []

    for row in rows[3:]:   # skip first 3 rows (summary / headers)
        row = [str(c).strip() if pd.notna(c) else "" for c in row]

        # ── Transactions ──────────────────────────────────────────────────────
        if len(row) >= 20:
            d_raw  = row[13]
            tk_raw = row[14].upper()
            ac_raw = row[15].upper()
            sh_raw = row[16]
            pr_raw = row[17]
            tt_raw = row[19]

            if d_raw and tk_raw and ac_raw in ("BUY", "SELL", "DIVIDEND") \
                    and tk_raw not in ("", "TICKER", "CASH", "NAN"):
                dt = parse_date(d_raw)
                if dt:
                    shares = clean_num(sh_raw) or 0
                    price  = clean_num(pr_raw) or 0
                    amount = clean_num(tt_raw) or shares * price
                    records.append({
                        "date":   dt,
                        "ticker": tk_raw,
                        "action": ac_raw,
                        "shares": shares,
                        "price":  price,
                        "amount": amount,
                    })

        # ── Deposits ──────────────────────────────────────────────────────────
        if len(row) >= 36:
            dd_raw  = row[34]
            amt_raw = row[35]
            if dd_raw and amt_raw:
                dt  = parse_date(dd_raw)
                amt = clean_num(amt_raw)
                if dt and amt and amt > 0:
                    records.append({
                        "date":   dt,
                        "ticker": "",
                        "action": "DEPOSIT",
                        "shares": 0,
                        "price":  0,
                        "amount": amt,
                    })

    if not records:
        print("[fetch] no records found — check sheet structure")
        sys.exit(1)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    print(f"[fetch] {len(df)} records ({df['action'].value_counts().to_dict()})")
    return df


# ── Price loader ──────────────────────────────────────────────────────────────
def _fetch_prices_http(tickers: list[str], start_date) -> dict[str, dict[str, float]]:
    """Fetch closing prices via direct Yahoo Finance v8 API (no yfinance dependency)."""
    import time as _time
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        start_dt = start_date if isinstance(start_date, datetime) else datetime.combine(start_date, datetime.min.time())
        period1  = int(start_dt.timestamp())
        period2  = int(_time.time()) + 86400
    except Exception:
        period1 = 0; period2 = int(_time.time()) + 86400

    result: dict[str, dict[str, float]] = {t: {} for t in tickers}
    for sym in tickers:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={period1}&period2={period2}&interval=1d"
        try:
            import requests as _req
            r = _req.get(url, headers=headers, timeout=15)
            data = r.json()
            chart = data.get("chart", {}).get("result", [{}])[0]
            closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            ts_list = chart.get("timestamp", [])
            for ts_val, c in zip(ts_list, closes):
                if c is not None and not (isinstance(c, float) and c != c):  # not NaN
                    dt_str = datetime.utcfromtimestamp(ts_val).strftime("%Y-%m-%d")
                    result[sym][dt_str] = round(float(c), 4)
        except Exception as ex:
            print(f"[prices-http] {sym}: {ex}")
    return result


def load_prices(tickers: list[str], start_date) -> pd.DataFrame:
    print(f"[prices] downloading {tickers} from {start_date} …")

    # Try 1: yfinance
    try:
        raw = yf.download(tickers, start=str(start_date)[:10],
                          progress=False, auto_adjust=True)
        prices = raw["Close"] if "Close" in raw.columns else raw
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=tickers[0])
        prices.columns = [str(c) for c in prices.columns]
        prices = prices.ffill()
        if not prices.empty:
            print(f"[prices] {len(prices)} trading days via yfinance, cols: {list(prices.columns)}")
            return prices
    except Exception as e:
        print(f"[prices] yfinance failed ({e}), falling back to HTTP …")

    # Try 2: direct Yahoo Finance HTTP
    print(f"[prices] trying direct HTTP …")
    http_data = _fetch_prices_http(tickers, start_date)
    all_dates = sorted(set(dt for sym in http_data.values() for dt in sym.keys()))
    if not all_dates:
        print("[prices] HTTP also empty — aborting")
        return pd.DataFrame()

    idx = pd.to_datetime(all_dates)
    df = pd.DataFrame(index=idx)
    for sym in tickers:
        sym_map = http_data.get(sym, {})
        df[sym] = pd.Series({pd.Timestamp(d): v for d, v in sym_map.items()})
    df = df.ffill()
    print(f"[prices] {len(df)} trading days via HTTP, cols: {list(df.columns)}")
    return df


# ── Core comparison (same logic as Warren's build_comparison) ─────────────────
def build_comparison(df: pd.DataFrame) -> dict:
    start_date = df["date"].min()

    stock_tickers = (
        df[df["action"].isin(["BUY", "SELL"])]["ticker"]
        .dropna().str.strip().str.upper().unique().tolist()
    )
    stock_tickers = [t for t in stock_tickers if t]
    all_tickers   = list(dict.fromkeys(stock_tickers + ["VOO", "QQQM"]))

    prices = load_prices(all_tickers, start_date)
    if prices.empty:
        print("[build] no price data — aborting")
        sys.exit(1)

    port_shares: dict[str, float] = {}
    port_cash    = 0.0
    voo_shares   = 0.0
    qqqm_shares  = 0.0
    cum_deposited = 0.0
    applied: set[int] = set()

    port_vals  = []
    voo_vals   = []
    qqqm_vals  = []
    dep_totals = []
    dates_out  = []

    for trade_date in prices.index:

        pending = df[(df["date"] <= trade_date) & (~df.index.isin(applied))]

        for idx, ev in pending.iterrows():
            applied.add(idx)
            action = str(ev.get("action", "") or "").strip().upper()
            ticker = str(ev.get("ticker", "") or "").strip().upper()
            amount = float(ev.get("amount", 0) or 0)
            shares = float(ev.get("shares", 0) or 0)
            ev_date = ev["date"]

            if action == "DEPOSIT":
                port_cash     += amount
                cum_deposited += amount
                for bm in ["VOO", "QQQM"]:
                    if bm in prices.columns:
                        future = prices[bm].dropna()
                        future = future[future.index >= ev_date]
                        if not future.empty:
                            px = float(future.iloc[0])
                            if px > 0:
                                if bm == "VOO":   voo_shares  += amount / px
                                else:             qqqm_shares += amount / px

            elif action == "DIVIDEND" and ticker:
                port_cash += amount

            elif action == "BUY" and ticker:
                port_cash -= amount
                port_shares[ticker] = port_shares.get(ticker, 0.0) + shares

            elif action == "SELL" and ticker:
                port_cash += amount
                port_shares[ticker] = max(0.0, port_shares.get(ticker, 0.0) - shares)

        # Today's portfolio value
        port_val = max(0.0, port_cash)
        for tk, sh in port_shares.items():
            if sh > 0 and tk in prices.columns:
                px = prices[tk].get(trade_date)
                if px is not None and not pd.isna(px):
                    port_val += sh * float(px)

        voo_val = qqqm_val = 0.0
        if "VOO"  in prices.columns:
            px = prices["VOO"].get(trade_date)
            if px is not None and not pd.isna(px):
                voo_val = voo_shares * float(px)
        if "QQQM" in prices.columns:
            px = prices["QQQM"].get(trade_date)
            if px is not None and not pd.isna(px):
                qqqm_val = qqqm_shares * float(px)

        port_vals.append(round(port_val, 2))
        voo_vals.append(round(voo_val, 2))
        qqqm_vals.append(round(qqqm_val, 2))
        dep_totals.append(round(cum_deposited, 2))
        dates_out.append(trade_date.strftime("%Y-%m-%d"))

    # Remaining events after last trading day
    remaining = df[~df.index.isin(applied)]
    if not remaining.empty and not prices.empty:
        last_px = prices.iloc[-1]
        for _, ev in remaining.iterrows():
            action = str(ev.get("action", "") or "").strip().upper()
            ticker = str(ev.get("ticker", "") or "").strip().upper()
            amount = float(ev.get("amount", 0) or 0)
            shares = float(ev.get("shares", 0) or 0)
            if action == "DEPOSIT":
                port_cash += amount; cum_deposited += amount
                for bm in ["VOO", "QQQM"]:
                    if bm in last_px and not pd.isna(last_px[bm]):
                        px = float(last_px[bm])
                        if px > 0:
                            if bm == "VOO": voo_shares  += amount / px
                            else:           qqqm_shares += amount / px
            elif action == "DIVIDEND" and ticker:
                port_cash += amount
            elif action == "BUY" and ticker:
                port_cash -= amount
                port_shares[ticker] = port_shares.get(ticker, 0.0) + shares
            elif action == "SELL" and ticker:
                port_cash += amount
                port_shares[ticker] = max(0.0, port_shares.get(ticker, 0.0) - shares)

        pv = max(0.0, port_cash)
        for tk, sh in port_shares.items():
            if sh > 0 and tk in last_px and not pd.isna(last_px.get(tk, float("nan"))):
                pv += sh * float(last_px[tk])
        vv = voo_shares  * float(last_px["VOO"])  if "VOO"  in last_px else 0.0
        qv = qqqm_shares * float(last_px["QQQM"]) if "QQQM" in last_px else 0.0

        port_vals.append(round(pv, 2))
        voo_vals.append(round(vv, 2))
        qqqm_vals.append(round(qv, 2))
        dep_totals.append(round(cum_deposited, 2))
        dates_out.append(datetime.now().strftime("%Y-%m-%d"))

    def _ret(vals):
        last_val = next((v for v in reversed(vals) if v > 0), 0)
        dep = cum_deposited
        return round((last_val / dep - 1) * 100, 2) if dep else 0.0

    return {
        "dates":     dates_out,
        "portfolio": port_vals,
        "voo":       voo_vals,
        "qqqm":      qqqm_vals,
        "deposited": dep_totals,
        "summary": {
            "portfolio_pct":   _ret(port_vals),
            "voo_pct":         _ret(voo_vals),
            "qqqm_pct":        _ret(qqqm_vals),
            "total_deposited": round(cum_deposited, 2),
        },
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df   = fetch_trades()
    data = build_comparison(df)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    s = data["summary"]
    print(f"\n✅  benchmark.json written → {OUT_FILE}")
    print(f"   Portfolio {s['portfolio_pct']:+.2f}%  |  VOO {s['voo_pct']:+.2f}%  |  QQQM {s['qqqm_pct']:+.2f}%")
    print(f"   Total deposited: ${s['total_deposited']:,.2f}  |  {len(data['dates'])} data points")
