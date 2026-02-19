Run python scanner.py --all
HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"Quote not found for symbol: DEMO1"}}}
$DEMO1: possibly delisted; no timezone found
HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"Quote not found for symbol: DEMO2"}}}
$DEMO2: possibly delisted; no timezone found
$DEMO3: possibly delisted; no timezone found
$SPEC1: possibly delisted; no timezone found
$ARAV: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")
$BLPH: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")
$FREQ: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")
$LPTX: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")
$ONTX: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")
$SELB: possibly delisted; no price data found  (period=10d) (Yahoo error = "No data found, symbol may be delisted")

════════════════════════════════════════════════════════════
  SLEEPER SCANNER — POP Method
  Buy the Rumor, Sell the News
  Thursday, February 19 2026  09:10 PM ET
════════════════════════════════════════════════════════════
✓ Database ready

[1/3] Checking outcomes from previous scans...

  Checking 4 outcomes...
  ⚠ DEMO1: No price data available yet
  ⚠ DEMO2: No price data available yet
  ⚠ DEMO3: No price data available yet
  ⚠ SPEC1: No price data available yet
  → 0 hits confirmed

[2/3] Running learning engine...
  ⚠ Only 0 outcomes recorded. Need at least 10 to learn.
  → Keep running nightly scans. Learning activates automatically.
  ✓ Weights unchanged

[3/3] Running tonight's scan...

  Fetching candidates via yfinance screener...
  Checking 105 watchlist tickers for activity tonight...
  ✓ Checked 39 tickers, 25 passed RVOL filter
  ✓ 25 candidates ready for scoring
  ✗ SAVA     filtered: Float 43.0M exceeds max 20M
  ✗ AGEN     filtered: Float 31.3M exceeds max 20M
  ✗ ALDX     filtered: Float 47.6M exceeds max 20M
  ✗ AUPH     filtered: Float 128.0M exceeds max 20M
  ✗ AVXL     filtered: Float 89.8M exceeds max 20M
  ✗ BCRX     filtered: Float 183.3M exceeds max 20M
  ✗ BNGO     filtered: RVOL 0.5x below minimum 1.3x
  ✗ CYCN     filtered: RVOL 0.3x below minimum 1.3x
  ✗ DMAC     filtered: Float 24.3M exceeds max 20M
  ✗ FATE     filtered: Float 100.8M exceeds max 20M
  ✗ HOOK     filtered: RVOL 0.3x below minimum 1.3x
  ✗ IFRX     filtered: Float 55.5M exceeds max 20M
  ✗ KPTI     filtered: RVOL 0.6x below minimum 1.3x
  ✗ MESO     filtered: Float 852.6M exceeds max 20M
  ✗ MGNX     filtered: Float 55.7M exceeds max 20M
  ✗ NVAX     filtered: Float 140.2M exceeds max 20M
  ✗ NVCR     filtered: Float 92.5M exceeds max 20M
  ✗ OCGN     filtered: Float 307.9M exceeds max 20M
  ✗ PHAT     filtered: Float 57.2M exceeds max 20M
  ✗ PULM     filtered: RVOL 1.1x below minimum 1.3x
  ✗ RCUS     filtered: Float 76.0M exceeds max 20M
  ✗ SIGA     filtered: Float 40.6M exceeds max 20M
  ✗ TNXP     filtered: RVOL 0.9x below minimum 1.3x
  ✗ TRVI     filtered: Float 103.8M exceeds max 20M
  ✓ 1 candidates saved to database

════════════════════════════════════════════════════════════
  TONIGHT'S SLEEPERS — 2026-02-19
════════════════════════════════════════════════════════════
  #    Ticker     Price   Float     SI   Pop  Rumor  Combo  Status
  ──────────────────────────────────────────────────────────────────────
  1    EDSA     $  0.90   4.7M     0%    69      0     31  🟣 SPEC

  ── EDSA (Edesa Biotech, Inc.) ──
     Catalyst: Recent News
     Entry:    Wait for premarket confirmation $0.87–0.93. Stop $0.77.
     Exit:     Sell at open into volume spike. Target $1.12. If it gaps hard, sell immediately — that's the news phase.

  ✓ results.json exported (1 candidates, 0 too late)

════════════════════════════════════════════════════════════
  SLEEPER SCANNER — LEARNING REPORT
════════════════════════════════════════════════════════════
  Total predictions:  0
  Hits (≥25% spike):  0
  Win rate:           0.0%
  Avg spike on hits:  0.0%
  Total scans run:    5
════════════════════════════════════════════════════════════

  ✓ Done.
