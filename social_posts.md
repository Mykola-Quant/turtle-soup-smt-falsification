# Social material — Turtle Soup + SMT falsification

Tone note: honest, methodology-forward, anti-hype. The whole brand is "I do not
fool myself." Resist any urge to oversell. Numbers are real and reproducible.

================================================================================
REDDIT  (r/algotrading — title + body)
================================================================================

TITLE:
I pre-registered 4 tests of the "Turtle Soup + SMT" setup (CRT, killzones, the
whole ICT package) and falsified all 4. Here's the full method and code.

BODY:

I kept seeing the "4H→15m CRT, Turtle Soup, SMT, killzones, Model #1" playbook
presented as a complete system, so I made it fully mechanical and tested the one
thing that actually matters: **does the SMT divergence filter add real edge, or
does it just shrink the sample so the win rate looks better?**

I tested it like I was trying to *kill* it, not confirm it:

- Fixed every threshold (sweep buffer, costs, the pass criterion) BEFORE looking
  at any result. No tuning against the test set.
- Real costs: 0.5 pip RT on FX, broker-style on metals, a conservative 0.13% RT
  on crypto.
- Permutation test (10k label shuffles) for the SMT effect, bootstrap CI on net.
- The decisive one, specified in advance: split the trades at the calendar
  midpoint and require the SMT effect to hold in BOTH halves (delta > 0, p < 0.10).
  If it only shows up in one half, it's a regime artifact, not an edge.

Results across gold/silver, gold vs DXY (proxy and full synthetic index), and
BTC/SOL:

| Test | full-sample p | out-of-sample |
|---|---|---|
| XAU/XAG 2020–2024 | 0.46 | no effect |
| XAU vs DXY-proxy 2025 | 0.013 | H1 fails (p=0.20) |
| XAU vs synthetic DXY 2025 | 0.025 | H1 fails (p=0.13) |
| BTC/SOL 2025–2026 | 0.78 | no effect |

All four falsified. The interesting part isn't "it doesn't work" — it's *how* it
fails. Gold vs the dollar genuinely tested significant in-sample (p≈0.013–0.025).
But the entire signal lived in the **second half of 2025**. On five untouched
prior years it was gone (p=0.46). On crypto, gone (p=0.78). It's the fingerprint
of one regime, not a stable edge — which is exactly the kind of thing that
backtests beautifully and then blows up live.

Two takeaways I'd defend:
1. A single in-sample p=0.02 is not evidence. Three of these tests hit
   "significant" at some stage and all three dissolved out of sample.
2. On a 15-minute sweep, costs subtract ~0.57R/trade on crypto. The move is
   smaller than the friction. No filter fixes that.

Full code (backtest engine, HistData converter, DXY builders), the figures, and
a write-up are in the repo: [LINK]

Happy to be told what I got wrong — that's the point of posting it.

================================================================================
X / TWITTER  (thread)
================================================================================

1/
I made the "Turtle Soup + SMT" setup fully mechanical — CRT range, liquidity
sweep, cross-asset divergence filter, killzones, the whole ICT package — and
pre-registered 4 tests to see if the SMT filter adds real edge.

All 4 falsified. But *how* they failed is the interesting part. 🧵

2/
The honest question wasn't "does ICT work." It was narrow:

Does the SMT divergence filter add expectancy, or does it just shrink the sample
so the win rate looks nicer?

Everything was built to answer that one thing.

3/
Rules fixed BEFORE seeing results:
• real costs (0.13% RT on crypto, 0.5 pip on FX)
• 10k-shuffle permutation test for the SMT effect
• bootstrap CI on net expectancy
• the decider: effect must hold in BOTH halves of the data (split in advance)

4/
Results:

XAU/XAG 2020–24 → p=0.46, nothing
XAU vs DXY 2025 → p=0.013 ✅ ... but
XAU vs synth-DXY → p=0.025 ✅ ... but
BTC/SOL → p=0.78, nothing

The two "✅" are the trap.

5/
Those gold-vs-dollar effects were REAL in-sample. But the entire signal lived in
H2-2025.

First half of 2025: not significant.
2020–2024 (5 untouched years): p=0.46.
Crypto: p=0.78.

It's a regime fingerprint, not an edge.

6/
This is why one in-sample p=0.02 is not a discovery. Three tests here hit
"significant" at some stage. All three dissolved the moment I applied an
out-of-sample split I'd specified in advance.

The pre-registered rule did its job: it stopped me fooling myself.

7/
And costs. On a 15-min sweep, 0.13% round-trip subtracts ~0.57R per trade. The
move is just smaller than the friction. No clever filter rescues that — same
lesson that ended my earlier crypto order-flow work.

8/
Full method, code (backtest engine + data tooling), and figures here. Negative
result, released in full so anyone can check it or break it:

[LINK]

Tell me what I got wrong.

================================================================================
ONE-LINER (bio / pinned)
================================================================================
Pre-registered 4 tests of Turtle Soup + SMT across FX, metals & crypto. All 4
falsified — and the "significant" ones failed out-of-sample. Code + write-up: [LINK]
