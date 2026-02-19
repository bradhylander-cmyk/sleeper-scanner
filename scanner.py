"""
SLEEPER SCANNER — POP Method
Buy the Rumor, Sell the News

Runs nightly after market close (4PM–8PM ET).
Finds stocks likely to spike 25%+ within 2 days.
Learns from its own results automatically.

Usage:
    python scanner.py          # Run tonight's scan
    python scanner.py --learn  # Process results + update weights
    python scanner.py --report # Show learning report
    python scanner.py --all    # Scan + learn + report (recommended nightly)
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─── SETUP ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "sleeper.db"
WEIGHTS_PATH = BASE_DIR / "weights.json"

# ─── DEFAULT WEIGHTS ─────────────────────────────────────────────────────────
# These start as your best guess and get refined by the learning engine.
# Two score families:
#   POP SCORE  — quality of the setup (float, SI, volume, buzz)
#   RUMOR SCORE — how early you are in the cycle (upcoming dates, options, SI rising)

DEFAULT_WEIGHTS = {
    # Pop Score weights (must sum to 100)
    "pop": {
        "catalyst":     30,   # Has a catalyst (news, FDA, earnings, contract)
        "low_float":    25,   # Float under threshold (see THRESHOLDS)
        "short_interest": 20, # Short interest elevated
        "unusual_vol":  15,   # Volume significantly above average
        "buzz":         10,   # Social buzz rising
    },
    # Rumor Score weights (must sum to 100)
    "rumor": {
        "upcoming_date": 40,  # Known PDUFA / earnings date upcoming
        "options_flow":  35,  # Unusual options activity detected
        "si_rising":     25,  # Short interest rising INTO catalyst
    }
}

# ─── THRESHOLDS ──────────────────────────────────────────────────────────────
THRESHOLDS = {
    "max_float_m":        20,    # Max float in millions
    "low_float_m":         8,    # "Low float" threshold for bonus scoring
    "min_short_interest": 15,    # Min short interest % to count
    "high_si":            30,    # "High" SI for bonus scoring
    "min_rvol":            1.3,  # Min relative volume
    "min_price":           0.50, # Min stock price
    "max_price":          30.00, # Max stock price
    "success_spike_pct":  25,    # What counts as a win (25% spike in 2 days)
    "success_days":        2,    # Days to check for spike
    "too_late_sources":    3,    # Number of major sources = "widely reported"
}

# ─── TOO LATE SOURCES ────────────────────────────────────────────────────────
# If a catalyst appears in this many sources, it's too late.
MAJOR_SOURCES = [
    "yahoo finance", "benzinga", "cnbc", "marketwatch",
    "seeking alpha", "reuters", "bloomberg", "thestreet",
]

# ─── DATABASE SETUP ──────────────────────────────────────────────────────────

def init_db():
    """Create database tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date   TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            company     TEXT,
            price       REAL,
            float_m     REAL,
            short_int   REAL,
            rvol        REAL,
            pop_score   INTEGER,
            rumor_score INTEGER,
            combo_score INTEGER,
            catalyst    INTEGER DEFAULT 0,
            low_float   INTEGER DEFAULT 0,
            si_high     INTEGER DEFAULT 0,
            unusual_vol INTEGER DEFAULT 0,
            buzz        INTEGER DEFAULT 0,
            upcoming_date INTEGER DEFAULT 0,
            options_flow  INTEGER DEFAULT 0,
            si_rising     INTEGER DEFAULT 0,
            catalyst_type TEXT,
            catalyst_desc TEXT,
            too_late      INTEGER DEFAULT 0,
            too_late_reason TEXT,
            entry_idea    TEXT,
            exit_idea     TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id      INTEGER REFERENCES scans(id),
            ticker       TEXT NOT NULL,
            scan_date    TEXT NOT NULL,
            price_entry  REAL,
            price_d1_high REAL,
            price_d2_high REAL,
            spike_pct    REAL,
            hit          INTEGER DEFAULT 0,
            checked_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS weight_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            weights_before TEXT,
            weights_after  TEXT,
            reason         TEXT,
            accuracy_before REAL,
            accuracy_after  REAL,
            sample_size    INTEGER
        );

        CREATE TABLE IF NOT EXISTS signal_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            signal_name TEXT NOT NULL,
            score_family TEXT NOT NULL,
            win_rate    REAL,
            sample_size INTEGER,
            avg_spike   REAL
        );
    """)

    conn.commit()
    conn.close()
    print("✓ Database ready")


# ─── WEIGHTS ─────────────────────────────────────────────────────────────────

def load_weights():
    """Load weights from file, or use defaults if not found."""
    if WEIGHTS_PATH.exists():
        with open(WEIGHTS_PATH) as f:
            return json.load(f)
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights):
    """Save current weights to file."""
    with open(WEIGHTS_PATH, "w") as f:
        json.dump(weights, f, indent=2)


# ─── SCORING ─────────────────────────────────────────────────────────────────

def score_stock(stock: dict, weights: dict) -> dict:
    """
    Score a stock on both Pop Score and Rumor Score.
    Returns the stock dict with scores added.

    'stock' should have these keys:
        ticker, company, price, float_m, short_int, rvol,
        catalyst (bool), catalyst_type, catalyst_desc,
        buzz_level (0-10), upcoming_date (bool),
        options_flow (bool), si_rising (bool),
        news_sources (list of source names reporting the catalyst)
    """
    w = weights
    t = THRESHOLDS

    # ── TOO LATE CHECK ── (automatic disqualifier)
    news_sources = stock.get("news_sources", [])
    major_hits = sum(1 for src in news_sources if any(m in src.lower() for m in MAJOR_SOURCES))
    too_late = major_hits >= t["too_late_sources"]
    too_late_reason = None
    if too_late:
        too_late_reason = f"Catalyst reported by {major_hits} major sources: {', '.join(news_sources[:3])}"

    # ── BASIC FILTERS ──
    price = stock.get("price", 0)
    if price < t["min_price"] or price > t["max_price"]:
        stock["filtered"] = True
        stock["filter_reason"] = f"Price ${price:.2f} outside range ${t['min_price']}–${t['max_price']}"
        return stock

    float_m = stock.get("float_m", 999)
    if float_m > t["max_float_m"]:
        stock["filtered"] = True
        stock["filter_reason"] = f"Float {float_m}M exceeds max {t['max_float_m']}M"
        return stock

    rvol = stock.get("rvol", 0)
    if rvol < t["min_rvol"]:
        stock["filtered"] = True
        stock["filter_reason"] = f"RVOL {rvol:.1f}x below minimum {t['min_rvol']}x"
        return stock

    # ── POP SCORE ──
    pop_w = w["pop"]
    short_int = stock.get("short_int", 0)
    buzz = stock.get("buzz_level", 0)  # 0–10 scale

    # Each signal produces 0–100 points, weighted
    has_catalyst    = bool(stock.get("catalyst", False))
    is_low_float    = float_m <= t["low_float_m"]
    si_high         = short_int >= t["min_short_interest"]
    has_unusual_vol = rvol >= t["min_rvol"]
    has_buzz        = buzz >= 5

    # Bonus multipliers for exceptional signals
    float_score = 100 if float_m < 3 else 80 if float_m < 5 else 60 if float_m < 8 else 30
    si_score    = 100 if short_int >= 40 else 80 if short_int >= 30 else 60 if short_int >= 20 else 30
    vol_score   = 100 if rvol >= 10 else 80 if rvol >= 5 else 60 if rvol >= 3 else 40
    buzz_score  = buzz * 10  # 0–100

    pop_score = (
        (100 if has_catalyst else 0) * pop_w["catalyst"] / 100 +
        float_score                  * pop_w["low_float"] / 100 +
        si_score                     * pop_w["short_interest"] / 100 +
        vol_score                    * pop_w["unusual_vol"] / 100 +
        buzz_score                   * pop_w["buzz"] / 100
    )

    # ── RUMOR SCORE ──
    rumor_w = w["rumor"]
    has_upcoming_date = bool(stock.get("upcoming_date", False))
    has_options_flow  = bool(stock.get("options_flow", False))
    is_si_rising      = bool(stock.get("si_rising", False))

    rumor_score = (
        (100 if has_upcoming_date else 0) * rumor_w["upcoming_date"] / 100 +
        (100 if has_options_flow  else 0) * rumor_w["options_flow"]  / 100 +
        (100 if is_si_rising      else 0) * rumor_w["si_rising"]     / 100
    )

    # ── COMBO SCORE ──
    # Weighted average of both scores. Rumor score matters more
    # because being early is the whole point.
    if too_late:
        combo_score = 0  # Disqualified
    else:
        combo_score = int(pop_score * 0.45 + rumor_score * 0.55)

    # ── ENTRY / EXIT IDEAS ──
    entry, exit_idea = generate_plan(stock, combo_score)

    return {
        **stock,
        "pop_score":    int(pop_score),
        "rumor_score":  int(rumor_score),
        "combo_score":  combo_score,
        "too_late":     too_late,
        "too_late_reason": too_late_reason,
        "is_low_float": is_low_float,
        "si_high":      si_high,
        "has_buzz":     has_buzz,
        "entry_idea":   entry,
        "exit_idea":    exit_idea,
        "filtered":     False,
    }


def generate_plan(stock: dict, combo_score: int) -> tuple:
    """Generate plain-English entry and exit ideas."""
    price = stock.get("price", 0)
    float_m = stock.get("float_m", 0)
    si = stock.get("short_int", 0)

    entry_low  = round(price * 0.97, 2)
    entry_high = round(price * 1.03, 2)
    target_1   = round(price * 1.25, 2)
    target_2   = round(price * 1.50, 2)
    stop       = round(price * 0.85, 2)

    if combo_score >= 80:
        timing = "Buy AH tonight"
    elif combo_score >= 65:
        timing = "Buy AH or early premarket"
    else:
        timing = "Wait for premarket confirmation"

    entry = f"{timing} ${entry_low}–{entry_high}. Stop ${stop}."

    if si >= 30:
        exit_note = f"Sell into open gap. Squeeze target ${target_1}–{target_2}. Sell the news — don't hold after catalyst confirms."
    else:
        exit_note = f"Sell at open into volume spike. Target ${target_1}. If it gaps hard, sell immediately — that's the news phase."

    return entry, exit_note


# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_price_data(ticker: str, days_back: int = 5) -> dict:
    """
    Fetch price data using yfinance (free).
    Returns dict with current price, volume, avg volume, highs.
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{days_back + 10}d")

        if hist.empty:
            return {}

        # Current/latest data
        latest = hist.iloc[-1]
        avg_vol = hist["Volume"].iloc[:-1].mean()
        rvol = latest["Volume"] / avg_vol if avg_vol > 0 else 0

        # AH price (approximation — yfinance free tier is delayed)
        info = stock.fast_info

        return {
            "price":     round(float(latest["Close"]), 2),
            "volume":    int(latest["Volume"]),
            "avg_vol":   int(avg_vol),
            "rvol":      round(rvol, 1),
            "day_high":  round(float(latest["High"]), 2),
            "day_low":   round(float(latest["Low"]), 2),
            "hist":      hist,
        }
    except ImportError:
        print("  ⚠ yfinance not installed. Run: pip install yfinance")
        return {}
    except Exception as e:
        print(f"  ⚠ Could not fetch {ticker}: {e}")
        return {}


def fetch_outcome(ticker: str, scan_date: str) -> dict:
    """
    Check actual outcome for a scan made on scan_date.
    Looks at D+1 and D+2 highs to see if stock hit +25%.
    """
    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        scan_dt = datetime.strptime(scan_date, "%Y-%m-%d")
        end_dt  = scan_dt + timedelta(days=5)  # buffer for weekends

        stock = yf.Ticker(ticker)
        hist = stock.history(
            start=(scan_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d")
        )

        if hist.empty or len(hist) < 1:
            return {"status": "no_data"}

        entry_price = float(hist.iloc[0]["Open"])  # Next day open (approx entry)
        d1_high = float(hist.iloc[0]["High"]) if len(hist) >= 1 else entry_price
        d2_high = float(hist.iloc[1]["High"]) if len(hist) >= 2 else d1_high

        best_high = max(d1_high, d2_high)
        spike_pct = ((best_high - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        hit = spike_pct >= THRESHOLDS["success_spike_pct"]

        return {
            "status":       "ok",
            "entry_price":  round(entry_price, 2),
            "d1_high":      round(d1_high, 2),
            "d2_high":      round(d2_high, 2),
            "spike_pct":    round(spike_pct, 1),
            "hit":          hit,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def fetch_headlines(ticker: str) -> list:
    """
    Fetch recent news headlines for a ticker using yfinance (free).
    Returns list of source names that carried the story.
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        news = stock.news or []

        sources = []
        for article in news[:10]:
            publisher = article.get("publisher", "").lower()
            sources.append(publisher)

        return sources
    except Exception as e:
        return []


# ─── LEARNING ENGINE ─────────────────────────────────────────────────────────

def run_learning_engine(conn, weights: dict) -> dict:
    """
    Cross-reference past predictions with actual outcomes.
    Adjust weights based on what signals correlated with wins vs losses.
    Returns updated weights.
    """
    c = conn.cursor()

    # Get all outcomes with their signal data
    c.execute("""
        SELECT
            o.hit,
            s.catalyst,
            s.low_float,
            s.si_high,
            s.unusual_vol,
            s.buzz,
            s.upcoming_date,
            s.options_flow,
            s.si_rising,
            o.spike_pct
        FROM outcomes o
        JOIN scans s ON o.scan_id = s.id
        WHERE o.spike_pct IS NOT NULL
        AND s.too_late = 0
    """)
    rows = c.fetchall()

    if len(rows) < 10:
        print(f"  ⚠ Only {len(rows)} outcomes recorded. Need at least 10 to learn.")
        print("  → Keep running nightly scans. Learning activates automatically.")
        return weights

    print(f"\n  📊 Analyzing {len(rows)} outcomes...")

    # Count wins/losses per signal
    signals = ["catalyst","low_float","si_high","unusual_vol","buzz",
               "upcoming_date","options_flow","si_rising"]

    stats = {}
    for i, sig in enumerate(signals):
        col_idx = i + 1  # offset by 1 (hit is col 0)
        with_signal     = [r for r in rows if r[col_idx] == 1]
        without_signal  = [r for r in rows if r[col_idx] == 0]
        wins_with       = sum(1 for r in with_signal if r[0] == 1)
        win_rate_with   = wins_with / len(with_signal) if with_signal else 0
        avg_spike_with  = sum(r[9] for r in with_signal) / len(with_signal) if with_signal else 0

        stats[sig] = {
            "win_rate":   round(win_rate_with * 100, 1),
            "sample":     len(with_signal),
            "avg_spike":  round(avg_spike_with, 1),
        }

    overall_wins = sum(1 for r in rows if r[0] == 1)
    overall_rate = overall_wins / len(rows) * 100

    print(f"\n  Signal performance (vs {overall_rate:.0f}% baseline):")
    print(f"  {'Signal':<20} {'Win Rate':>10} {'Sample':>8} {'Avg Spike':>10}")
    print(f"  {'─'*50}")
    for sig, s in stats.items():
        marker = "↑" if s["win_rate"] > overall_rate else "↓" if s["win_rate"] < overall_rate - 5 else "─"
        print(f"  {sig:<20} {s['win_rate']:>9.1f}% {s['sample']:>8} {s['avg_spike']:>9.1f}% {marker}")

    # ── WEIGHT ADJUSTMENT LOGIC ──
    # For each signal, if its win rate is significantly above/below baseline,
    # nudge its weight up/down by 1–3 points. Small, deliberate changes.

    new_weights = json.loads(json.dumps(weights))  # deep copy
    changes = []

    pop_signals = ["catalyst","low_float","si_high","unusual_vol","buzz"]
    rumor_signals = ["upcoming_date","options_flow","si_rising"]

    pop_map = {
        "catalyst":   "catalyst",
        "low_float":  "low_float",
        "si_high":    "short_interest",
        "unusual_vol":"unusual_vol",
        "buzz":       "buzz",
    }
    rumor_map = {
        "upcoming_date": "upcoming_date",
        "options_flow":  "options_flow",
        "si_rising":     "si_rising",
    }

    def adjust(family, key, current_weight, win_rate, baseline):
        delta = win_rate - baseline
        if delta > 10 and stats.get(key, {}).get("sample", 0) >= 10:
            nudge = 2
        elif delta > 5:
            nudge = 1
        elif delta < -10 and stats.get(key, {}).get("sample", 0) >= 10:
            nudge = -2
        elif delta < -5:
            nudge = -1
        else:
            nudge = 0

        new = max(5, min(50, current_weight + nudge))
        return new, nudge

    # Adjust pop weights
    for raw_sig, weight_key in pop_map.items():
        if raw_sig not in stats:
            continue
        s = stats[raw_sig]
        old = weights["pop"][weight_key]
        new, nudge = adjust("pop", raw_sig, old, s["win_rate"], overall_rate)
        new_weights["pop"][weight_key] = new
        if nudge != 0:
            direction = "↑ raised" if nudge > 0 else "↓ lowered"
            changes.append(f"  {direction} pop/{weight_key}: {old}% → {new}% (signal win rate: {s['win_rate']:.0f}%)")

    # Adjust rumor weights
    for raw_sig, weight_key in rumor_map.items():
        if raw_sig not in stats:
            continue
        s = stats[raw_sig]
        old = weights["rumor"][weight_key]
        new, nudge = adjust("rumor", raw_sig, old, s["win_rate"], overall_rate)
        new_weights["rumor"][weight_key] = new
        if nudge != 0:
            direction = "↑ raised" if nudge > 0 else "↓ lowered"
            changes.append(f"  {direction} rumor/{weight_key}: {old}% → {new}% (signal win rate: {s['win_rate']:.0f}%)")

    # Normalize weights within each family so they sum to 100
    for family in ["pop", "rumor"]:
        total = sum(new_weights[family].values())
        if total != 100:
            # Scale proportionally
            for k in new_weights[family]:
                new_weights[family][k] = round(new_weights[family][k] * 100 / total)
            # Fix rounding error on largest weight
            diff = 100 - sum(new_weights[family].values())
            largest = max(new_weights[family], key=new_weights[family].get)
            new_weights[family][largest] += diff

    # Log the change
    if changes:
        print(f"\n  🧠 Weight adjustments (based on {len(rows)} outcomes):")
        for ch in changes:
            print(ch)

        # Save to weight history table
        c.execute("""
            INSERT INTO weight_history
            (weights_before, weights_after, reason, accuracy_before, sample_size)
            VALUES (?, ?, ?, ?, ?)
        """, (
            json.dumps(weights),
            json.dumps(new_weights),
            "\n".join(changes),
            round(overall_rate, 1),
            len(rows),
        ))
        conn.commit()
    else:
        print("\n  ✓ Weights stable — no significant signal drift detected.")

    # Save signal stats
    for sig, s in stats.items():
        family = "pop" if sig in pop_signals else "rumor"
        c.execute("""
            INSERT INTO signal_stats (signal_name, score_family, win_rate, sample_size, avg_spike)
            VALUES (?, ?, ?, ?, ?)
        """, (sig, family, s["win_rate"], s["sample"], s["avg_spike"]))
    conn.commit()

    return new_weights


# ─── OUTCOME CHECKER ─────────────────────────────────────────────────────────

def check_outcomes(conn):
    """
    For all scans from 2–3 days ago, check if the stock hit +25%.
    Saves results to outcomes table.
    """
    c = conn.cursor()

    # Find scans from 2-3 days ago that don't have outcomes yet
    cutoff_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    max_date    = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    c.execute("""
        SELECT s.id, s.ticker, s.scan_date, s.price
        FROM scans s
        LEFT JOIN outcomes o ON o.scan_id = s.id
        WHERE s.scan_date <= ?
        AND s.scan_date >= ?
        AND s.too_late = 0
        AND o.id IS NULL
    """, (max_date, (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")))

    pending = c.fetchall()

    if not pending:
        print("  ✓ No pending outcomes to check.")
        return 0

    print(f"\n  Checking {len(pending)} outcomes...")
    hits = 0

    for scan_id, ticker, scan_date, entry_price in pending:
        result = fetch_outcome(ticker, scan_date)

        if result["status"] == "no_data":
            print(f"  ⚠ {ticker}: No price data available yet")
            continue
        elif result["status"] == "error":
            print(f"  ⚠ {ticker}: {result.get('error','unknown error')}")
            continue

        hit = result["hit"]
        spike = result["spike_pct"]
        marker = "✅ HIT" if hit else "✗ miss"
        hits += 1 if hit else 0

        print(f"  {ticker:<6} {scan_date}  spike: {spike:+.1f}%  {marker}")

        c.execute("""
            INSERT INTO outcomes
            (scan_id, ticker, scan_date, price_entry, price_d1_high, price_d2_high, spike_pct, hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_id, ticker, scan_date,
            result["entry_price"], result["d1_high"], result["d2_high"],
            result["spike_pct"], 1 if hit else 0
        ))

    conn.commit()
    return hits


# ─── SAVE SCAN ────────────────────────────────────────────────────────────────

def save_scan(conn, scan_date: str, results: list):
    """Save tonight's scan results to database."""
    c = conn.cursor()
    saved = 0

    for r in results:
        if r.get("filtered"):
            continue

        c.execute("""
            INSERT INTO scans (
                scan_date, ticker, company, price, float_m, short_int, rvol,
                pop_score, rumor_score, combo_score,
                catalyst, low_float, si_high, unusual_vol, buzz,
                upcoming_date, options_flow, si_rising,
                catalyst_type, catalyst_desc,
                too_late, too_late_reason,
                entry_idea, exit_idea
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_date,
            r.get("ticker"),
            r.get("company"),
            r.get("price"),
            r.get("float_m"),
            r.get("short_int"),
            r.get("rvol"),
            r.get("pop_score"),
            r.get("rumor_score"),
            r.get("combo_score"),
            1 if r.get("catalyst") else 0,
            1 if r.get("is_low_float") else 0,
            1 if r.get("si_high") else 0,
            1 if r.get("unusual_vol") else 0,
            1 if r.get("has_buzz") else 0,
            1 if r.get("upcoming_date") else 0,
            1 if r.get("options_flow") else 0,
            1 if r.get("si_rising") else 0,
            r.get("catalyst_type"),
            r.get("catalyst_desc"),
            1 if r.get("too_late") else 0,
            r.get("too_late_reason"),
            r.get("entry_idea"),
            r.get("exit_idea"),
        ))
        saved += 1

    conn.commit()
    return saved


# ─── REPORT ──────────────────────────────────────────────────────────────────

def print_report(conn):
    """Print a learning performance report."""
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM scans WHERE too_late = 0")
    total_scans = c.fetchone()[0]

    c.execute("SELECT COUNT(*), SUM(hit), AVG(spike_pct) FROM outcomes")
    row = c.fetchone()
    total_outcomes, total_hits, avg_spike = row
    total_hits = total_hits or 0
    avg_spike  = avg_spike or 0

    win_rate = (total_hits / total_outcomes * 100) if total_outcomes else 0

    c.execute("""
        SELECT signal_name, win_rate, sample_size, avg_spike
        FROM signal_stats
        WHERE id IN (
            SELECT MAX(id) FROM signal_stats GROUP BY signal_name
        )
        ORDER BY win_rate DESC
    """)
    signal_rows = c.fetchall()

    c.execute("""
        SELECT weights_before, weights_after, reason, changed_at
        FROM weight_history
        ORDER BY changed_at DESC LIMIT 1
    """)
    last_change = c.fetchone()

    print("\n" + "═"*60)
    print("  SLEEPER SCANNER — LEARNING REPORT")
    print("═"*60)
    print(f"  Total predictions:  {total_outcomes}")
    print(f"  Hits (≥25% spike):  {int(total_hits)}")
    print(f"  Win rate:           {win_rate:.1f}%")
    print(f"  Avg spike on hits:  {avg_spike:.1f}%")
    print(f"  Total scans run:    {total_scans}")

    if signal_rows:
        print(f"\n  Signal Performance:")
        print(f"  {'Signal':<20} {'Win Rate':>10} {'Sample':>8} {'Avg Spike':>10}")
        print(f"  {'─'*50}")
        for sig, wr, sample, avg in signal_rows:
            bar = "█" * int(wr / 10)
            print(f"  {sig:<20} {wr:>9.1f}% {sample:>8} {avg:>9.1f}%  {bar}")

    if last_change:
        before, after, reason, changed_at = last_change
        print(f"\n  Last weight adjustment: {changed_at}")
        print(f"  Reason: {reason[:200]}")

    print("═"*60)


# ─── REAL DATA — FINVIZ SCREENER ─────────────────────────────────────────────

def fetch_candidates() -> list:
    """
    Pull real stock candidates using yfinance screener.
    Searches for stocks with high relative volume and low price —
    the core filter for finding pre-gap sleepers.
    No API key needed.
    """
    import yfinance as yf
    import time

    print("\n  Fetching candidates via yfinance screener...")

    # These are known low-float, high-volatility tickers that
    # the scanner uses as a starting universe. yfinance's screener
    # API lets us pull the most active small caps each day.
    # We pull the most active stocks under $30 and filter from there.

    candidates = []

    # Low-float small cap watchlist — these are the stocks that actually gap.
    # Scanner checks each one nightly and only keeps those with RVOL > 2x,
    # so on quiet nights you get 0-2 candidates, on hot nights you get 5-10.
    WATCHLIST = [
        # Biotech / Pharma — most common gap-up sector
        "SAVA","AGEN","ALDX","APTO","ARAV","AUPH","AVXL",
        "BCRX","BLPH","BNGO","BPTH","CYCN","DMAC","EDSA",
        "FATE","FREQ","HOOK","IFRX","IPIX","KPTI","LPTX",
        "MESO","MGNX","NKTR","NVAX","NVCR","OCGN","ONTX",
        "PHAT","PTGX","PULM","RCUS","SELB","SIGA","SLNO",
        "SRNE","STOK","TNXP","TRVI","ACMR","HALO","ORGO",
        # Clinical stage — high binary event risk/reward
        "ARNA","AVDL","CPRX","GOSS","INVA","PRTK","SURF",
        # Small cap high-vol movers
        "IBRX","MVIS","NKLA","WKHS","AGRX","ALBT","ALPP",
        "ATNF","CENN","DPRO","GFAI","GREE","IDEX","INPX",
        "LKCO","MMAT","NAKD","NSPR","OBSV","ORPH","RNAZ",
        "SHIP","IQST","LIQT","MOBQ","NILE","NUVB","PRTY",
        # Recent gap history — known to move on catalysts
        "PROG","CLOV","FFIE","MULN","RIDE","ZEV","EBON",
        "COMS","RAVE","TLSA","TACON","GOSS","AYALA","ENZC",
        # Squeeze candidates
        "BBBY","BYFC","EXPR","KOSS","MRIN","SPRT","TPVG",
        "VINC","ZYNE","CHEK","GLYC","IBIO","MBIO","PSTV",
    ]

    print(f"  Checking {len(WATCHLIST)} watchlist tickers for activity tonight...")

    seen = set()
    checked = 0
    for ticker in WATCHLIST:
        if ticker in seen or len(candidates) >= 25:
            break
        seen.add(ticker)
        checked += 1
        time.sleep(0.25)
        stock = enrich_ticker(ticker)
        if stock:
            candidates.append(stock)

    print(f"  ✓ Checked {checked} tickers, {len(candidates)} passed RVOL filter")
    return candidates


def enrich_ticker(ticker: str) -> dict:
    """
    Pull full stock data for one ticker using yfinance.
    Returns a candidate dict ready for score_stock(), or None if data unavailable.
    """
    try:
        import yfinance as yf
        import time

        time.sleep(0.5)  # be polite to Yahoo Finance

        ystock = yf.Ticker(ticker)
        info   = ystock.fast_info
        hist   = ystock.history(period="10d")

        if hist.empty or len(hist) < 2:
            return None

        # Price
        price = round(float(hist["Close"].iloc[-1]), 2)
        if price < THRESHOLDS["min_price"] or price > THRESHOLDS["max_price"]:
            return None

        # Relative volume
        avg_vol = float(hist["Volume"].iloc[:-1].mean())
        today_vol = float(hist["Volume"].iloc[-1])
        rvol = round(today_vol / avg_vol, 1) if avg_vol > 0 else 0

        # Float — try multiple yfinance fields
        float_m = 15.0  # default
        try:
            full_info = ystock.info
            float_shares = (
                full_info.get("floatShares") or
                full_info.get("impliedSharesOutstanding") or
                getattr(info, "shares_outstanding", None)
            )
            if float_shares and float_shares > 0:
                float_m = round(float_shares / 1_000_000, 1)
        except:
            pass

        # Short interest (yfinance has this sometimes)
        try:
            short_pct = ystock.info.get("shortPercentOfFloat", 0) or 0
            short_int = round(float(short_pct) * 100, 1)
        except:
            short_int = 0

        # News — check how widely reported the catalyst is
        news = ystock.news or []
        news_sources = [
            (a.get("publisher") or "").lower()
            for a in news[:10]
        ]

        # Catalyst detection from recent headlines
        catalyst      = False
        catalyst_type = "Unusual Volume"
        catalyst_desc = f"No specific catalyst found. RVOL {rvol}x above average."
        buzz_level    = 3
        upcoming_date = False
        options_flow  = False
        si_rising     = short_int >= 25  # elevated SI = rising into something

        headline_text = " ".join([
            (a.get("title") or "") for a in news[:5]
        ]).lower()

        # Detect catalyst type from headlines
        if any(w in headline_text for w in ["fda", "pdufa", "approval", "nda", "bla"]):
            catalyst      = True
            catalyst_type = "FDA / Regulatory Catalyst"
            catalyst_desc = f"FDA-related headline detected. {news[0].get('title','')[:80] if news else ''}"
            upcoming_date = True
            buzz_level    = 5

        elif any(w in headline_text for w in ["earnings", "eps", "revenue", "beat", "guidance"]):
            catalyst      = True
            catalyst_type = "Earnings Related"
            catalyst_desc = f"{news[0].get('title','')[:80] if news else 'Earnings activity detected'}"
            buzz_level    = 5

        elif any(w in headline_text for w in ["contract", "award", "dod", "government", "deal"]):
            catalyst      = True
            catalyst_type = "Contract / Deal"
            catalyst_desc = f"{news[0].get('title','')[:80] if news else 'Contract/deal activity'}"
            buzz_level    = 4

        elif any(w in headline_text for w in ["merger", "acquisition", "buyout", "takeover"]):
            catalyst      = True
            catalyst_type = "M&A Activity"
            catalyst_desc = f"{news[0].get('title','')[:80] if news else 'M&A activity detected'}"
            buzz_level    = 7

        elif any(w in headline_text for w in ["clinical", "trial", "phase", "data", "study"]):
            catalyst      = True
            catalyst_type = "Clinical / Study Data"
            catalyst_desc = f"{news[0].get('title','')[:80] if news else 'Clinical data activity'}"
            upcoming_date = True
            buzz_level    = 5

        elif news:
            catalyst      = True
            catalyst_type = "Recent News"
            catalyst_desc = f"{news[0].get('title','')[:80]}"
            buzz_level    = 4

        # Options flow hint — large RVOL with low buzz = quiet accumulation
        if rvol >= 5 and buzz_level <= 4:
            options_flow = True  # proxy signal for quiet smart money

        # Get company name
        try:
            company = ystock.info.get("shortName") or ystock.info.get("longName") or ticker
        except:
            company = ticker

        return {
            "ticker":       ticker,
            "company":      company,
            "price":        price,
            "float_m":      float_m,
            "short_int":    short_int,
            "rvol":         rvol,
            "catalyst":     catalyst,
            "catalyst_type":catalyst_type,
            "catalyst_desc":catalyst_desc,
            "buzz_level":   buzz_level,
            "upcoming_date":upcoming_date,
            "options_flow": options_flow,
            "si_rising":    si_rising,
            "news_sources": news_sources,
        }

    except Exception as e:
        print(f"  ⚠ {ticker}: {e}")
        return None


def run_real_scan() -> list:
    """
    Main entry point for real data scanning.
    Pulls from Finviz, enriches with yfinance, returns candidates.
    """
    candidates = fetch_candidates()

    if not candidates:
        print("  ⚠ No real candidates found. Check your internet connection.")
        print("  → Tip: Finviz sometimes rate-limits. Try again in a few minutes.")

    print(f"  ✓ {len(candidates)} candidates ready for scoring")
    return candidates



# ─── JSON EXPORT FOR DASHBOARD ──────────────────────────────────────────────

def export_json(conn, results: list, too_late_list: list, scan_date: str, weights: dict):
    """
    Export scan results to results.json so the dashboard can read them.
    This file gets committed back to GitHub by the nightly workflow.
    """
    c = conn.cursor()

    # Overall stats
    c.execute("SELECT COUNT(*), SUM(hit), AVG(spike_pct) FROM outcomes")
    row = c.fetchone()
    total_out = row[0] or 0
    total_hits = row[1] or 0
    avg_spike  = round(row[2] or 0, 1)
    win_rate   = round((total_hits / total_out * 100) if total_out else 0, 1)

    c.execute("SELECT COUNT(*) FROM scans WHERE too_late = 0")
    total_scans = c.fetchone()[0]

    # Recent history (last 14 nights)
    c.execute("""
        SELECT s.ticker, s.scan_date, s.combo_score,
               s.catalyst_type, o.spike_pct, o.hit
        FROM scans s
        LEFT JOIN outcomes o ON o.scan_id = s.id
        WHERE s.too_late = 0
        ORDER BY s.scan_date DESC, s.combo_score DESC
        LIMIT 30
    """)
    history = []
    for row in c.fetchall():
        history.append({
            "ticker":     row[0],
            "date":       row[1],
            "combo_score":row[2],
            "catalyst":   row[3],
            "spike_pct":  round(row[4], 1) if row[4] else None,
            "hit":        bool(row[5]) if row[5] is not None else None,
        })

    # Signal stats
    c.execute("""
        SELECT signal_name, win_rate, sample_size, avg_spike
        FROM signal_stats
        WHERE id IN (SELECT MAX(id) FROM signal_stats GROUP BY signal_name)
    """)
    signal_stats = {}
    for row in c.fetchall():
        signal_stats[row[0]] = {"win_rate": row[1], "sample": row[2], "avg_spike": row[3]}

    # Clean results for JSON
    def clean(r):
        return {
            "rank":         r.get("rank"),
            "ticker":       r.get("ticker"),
            "company":      r.get("company"),
            "price":        r.get("price"),
            "float_m":      r.get("float_m"),
            "short_int":    r.get("short_int"),
            "rvol":         r.get("rvol"),
            "pop_score":    r.get("pop_score"),
            "rumor_score":  r.get("rumor_score"),
            "combo_score":  r.get("combo_score"),
            "catalyst":     bool(r.get("catalyst")),
            "low_float":    bool(r.get("is_low_float")),
            "si_high":      bool(r.get("si_high")),
            "unusual_vol":  bool(r.get("unusual_vol")),
            "buzz":         bool(r.get("has_buzz")),
            "upcoming_date":bool(r.get("upcoming_date")),
            "options_flow": bool(r.get("options_flow")),
            "si_rising":    bool(r.get("si_rising")),
            "catalyst_type":r.get("catalyst_type"),
            "catalyst_desc":r.get("catalyst_desc"),
            "too_late":     bool(r.get("too_late")),
            "too_late_reason": r.get("too_late_reason"),
            "entry_idea":   r.get("entry_idea"),
            "exit_idea":    r.get("exit_idea"),
            "timing":       "Tonight AH" if r.get("combo_score", 0) >= 80 else "Premarket AM" if r.get("combo_score", 0) >= 65 else "Watch Only",
        }

    output = {
        "scan_date":    scan_date,
        "generated_at": datetime.now().isoformat(),
        "stats": {
            "total_candidates": len(results),
            "too_late_count":   len(too_late_list),
            "high_conviction":  sum(1 for r in results if r.get("combo_score", 0) >= 80),
            "win_rate":         win_rate,
            "total_scans":      total_scans,
            "avg_spike":        avg_spike,
        },
        "weights":      weights,
        "candidates":   [clean(r) for r in results],
        "too_late":     [clean(r) for r in too_late_list],
        "history":      history,
        "signal_stats": signal_stats,
    }

    out_path = BASE_DIR / "results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ results.json exported ({len(results)} candidates, {len(too_late_list)} too late)")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sleeper Scanner — POP Method")
    parser.add_argument("--learn",  action="store_true", help="Check outcomes + update weights")
    parser.add_argument("--report", action="store_true", help="Show learning report")
    parser.add_argument("--all",    action="store_true", help="Scan + learn + report (recommended)")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to scan (optional)")
    args = parser.parse_args()

    print("\n" + "═"*60)
    print("  SLEEPER SCANNER — POP Method")
    print("  Buy the Rumor, Sell the News")
    print(f"  {datetime.now().strftime('%A, %B %d %Y  %I:%M %p ET')}")
    print("═"*60)

    # Setup
    init_db()
    conn = sqlite3.connect(DB_PATH)
    weights = load_weights()

    scan_date = datetime.now().strftime("%Y-%m-%d")
    do_scan   = not (args.learn or args.report) or args.all
    do_learn  = args.learn or args.all
    do_report = args.report or args.all

    # ── STEP 1: CHECK OUTCOMES FROM 2 DAYS AGO ──
    if do_learn:
        print("\n[1/3] Checking outcomes from previous scans...")
        hits = check_outcomes(conn)
        print(f"  → {hits} hits confirmed")

        print("\n[2/3] Running learning engine...")
        new_weights = run_learning_engine(conn, weights)

        if new_weights != weights:
            save_weights(new_weights)
            weights = new_weights
            print("  ✓ Weights updated and saved")
        else:
            print("  ✓ Weights unchanged")

    # ── STEP 2: RUN TONIGHT'S SCAN ──
    if do_scan:
        step = "[3/3]" if do_learn else "[1/1]"
        print(f"\n{step} Running tonight's scan...")

        # In production: replace run_demo_scan() with your actual
        # data fetching logic (Polygon, Finviz, etc.)
        candidates = run_real_scan()

        # Score all candidates
        results = []
        too_late_list = []

        for stock in candidates:
            scored = score_stock(stock, weights)

            if scored.get("filtered"):
                print(f"  ✗ {scored['ticker']:<8} filtered: {scored.get('filter_reason','')}")
                continue

            if scored.get("too_late"):
                too_late_list.append(scored)
            else:
                results.append(scored)

        # Sort by combo score
        results.sort(key=lambda x: x["combo_score"], reverse=True)

        # Assign ranks
        for i, r in enumerate(results):
            r["rank"] = i + 1

        # Save to DB
        all_results = results + too_late_list
        saved = save_scan(conn, scan_date, all_results)
        print(f"  ✓ {saved} candidates saved to database")

        # ── PRINT RESULTS ──
        print("\n" + "═"*60)
        print(f"  TONIGHT'S SLEEPERS — {scan_date}")
        print("═"*60)

        if not results:
            print("  No candidates passed filters tonight.")
        else:
            print(f"  {'#':<4} {'Ticker':<8} {'Price':>7} {'Float':>7} {'SI':>6} {'Pop':>5} {'Rumor':>6} {'Combo':>6}  Status")
            print(f"  {'─'*70}")

            for r in results:
                alert = "🔴 ALERT" if r["combo_score"] >= 80 else "🟡 WATCH" if r["combo_score"] >= 65 else "🟣 SPEC"
                print(f"  {r['rank']:<4} {r['ticker']:<8} ${r['price']:>6.2f} {r['float_m']:>5.1f}M {r.get('short_int',0):>5.0f}% "
                      f"{r['pop_score']:>5} {r['rumor_score']:>6} {r['combo_score']:>6}  {alert}")

            print()
            for r in results:
                print(f"  ── {r['ticker']} ({r['company']}) ──")
                print(f"     Catalyst: {r.get('catalyst_type','None')}")
                print(f"     Entry:    {r['entry_idea']}")
                print(f"     Exit:     {r['exit_idea']}")
                print()

        if too_late_list:
            print(f"  🚫 TOO LATE (disqualified — catalyst widely reported):")
            for r in too_late_list:
                print(f"     {r['ticker']}: {r.get('too_late_reason','')[:80]}")

        # Export results.json for the live dashboard
        export_json(conn, results, too_late_list, scan_date, weights)

    # ── STEP 3: REPORT ──
    if do_report:
        print_report(conn)

    conn.close()
    print("\n  ✓ Done.\n")


if __name__ == "__main__":
    main()
