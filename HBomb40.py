#!/usr/bin/env python3
"""
============================================================
  ⚾  H-BOMB 12 — Daily DraftKings HRR Dashboard
============================================================

  WHAT'S NEW IN v12 (projection model v5):
  ─────────────────────────────────────────────────────────
  ✦ Vegas implied team total now drives Runs & RBI directly
    (no longer just a flat scoring constant)
  ✦ Statcast xBA blended into the Hits projection
  ✦ Strikeout-rate + pitcher contact suppression on Hits
  ✦ Clean, sortable "Top Projected Plays" table up top
  ✦ Model accuracy tracking — logs projected vs actual
    H/R/RBI to Supabase and reports error (MAE)

  WHAT'S NEW IN v11:
  ─────────────────────────────────────────────────────────
  ✦ Pitcher recent form engine — last 5 starts vs season ERA
    Fatigued/struggling pitchers boost batter scores
    Hot pitchers reduce batter scores
    Short rest & high pitch count flagged automatically
  ✦ HR rate now requires 15+ games before trusting it
    Prevents fluky early-season HR rates distorting grades
  ✦ Pitcher form shown on every card (both Top 10 + slates)
  ✦ card_remark now incorporates pitcher fatigue/form
  ✦ All prior v10 improvements included:
      Weather (Open-Meteo, free), injury flags, last start,
      season normalization, tighter grades/DK thresholds,
      H2H min 10 PA, headshot fix, scout remarks, DK how-to

  SETUP:
    pip install requests schedule
    export GMAIL_APP_PASSWORD="your_password"   (optional)
    export GITHUB_TOKEN="your_token"            (optional)
    python3 HBomb11.py
============================================================
"""

import os
import json
import time
import smtplib
import requests
import schedule
import webbrowser

from concurrent.futures import ThreadPoolExecutor

from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders


# ============================================================
#  CONFIG
# ============================================================

MLB_API              = "https://statsapi.mlb.com/api/v1"
# Script runs automatically at 12:00 PM and 3:30 PM ET daily
MIN_GAME_START_HOUR  = 0
RECENT_GAME_COUNT    = 10
TOP_BATTERS_PER_TEAM = 9
DELAY_BETWEEN_CALLS  = 0.01     # Calls are now parallelized; large per-call
                                # sleeps added minutes across ~3k requests
OUTPUT_FOLDER        = "reports"
SCORES_FILE          = os.path.join(OUTPUT_FOLDER, "last_scores.json")
AUTO_OPEN_BROWSER    = True      # Auto-open HTML in browser after generating

# ── GitHub Pages auto-deploy ─────────────────────────────
# Paste your GitHub Personal Access Token here directly:
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")   # OR just paste it: "ghp_xxxx..."
GITHUB_USER      = "christopher2smithtrade-sketch"
GITHUB_REPO      = "H-Bomb"
GITHUB_BRANCH    = "main"
GITHUB_PAGES_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/"

# ── ntfy push notifications (same app as the trading bots) ───
# Subscribe to this topic in the ntfy app to get HBomb alerts.
# Set NTFY_TOPIC = "metrade" if you'd rather share the trading feed.
NTFY_ENABLED         = True
NTFY_TOPIC           = "hbomb-guru"
NTFY_NOTIFY_SUCCESS  = True   # False = only alert me when something breaks

def notify(title, message, tags="", priority="default"):
    """Send a push via ntfy.sh. Never let a notification failure break a run."""
    if not NTFY_ENABLED:
        return
    try:
        headers = {"Title": title.encode("utf-8"), "Priority": priority}
        if tags:
            headers["Tags"] = tags
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"), headers=headers, timeout=15)
    except Exception as e:
        print(f"   ⚠ Notify error: {e}")


H2H_MIN_PA   = 10
H2H_HOT_AVG  = 0.350
H2H_COLD_AVG = 0.150

# ── The Odds API (free tier — 500 req/month) ─────────────────
# Sign up at https://the-odds-api.com/ for a free key.
# Read from env (Actions secret / local env var) — never hardcode in a public repo.
# If unset, the run simply scores without Vegas totals (graceful).
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

ENABLE_EMAIL   = False
SMTP_SERVER    = "smtp.gmail.com"
SMTP_PORT      = 587
EMAIL_SENDER   = "christopher2smith.trade@gmail.com"
EMAIL_PASSWORD = ""

# ── Add as many friends as you want here ──────────────────
EMAIL_RECIPIENTS = [
    "christopher2smith.trade@gmail.com",
    # "friend2@gmail.com",
    # "friend3@gmail.com",
]

LINEUP_MULTIPLIERS = {
    1: 1.25, 2: 1.20, 3: 1.18, 4: 1.15, 5: 1.08,
    6: 1.00, 7: 0.93, 8: 0.87, 9: 0.82
}
ORDER_PROFILES = {
    1: ("Leadoff",  "Most PAs → best for Runs"),
    2: ("2nd",      "Elite spot → Runs + table-setter"),
    3: ("3rd",      "Best hitter slot → balanced H+R+RBI"),
    4: ("Cleanup",  "Power slot → highest RBI ceiling"),
    5: ("5th",      "Strong RBI support"),
    6: ("6th",      "Middle order — moderate upside"),
    7: ("7th",      "Lower order — fewer PA"),
    8: ("8th",      "Low PA count — lower floor"),
    9: ("9th",      "Fewest opportunities"),
}
HOME_AWAY_BOOST   = 1.08
HOME_AWAY_PENALTY = 0.92

TEAM_COLORS = {
    "Arizona Diamondbacks":  "#A71930",
    "Atlanta Braves":        "#CE1141",
    "Baltimore Orioles":     "#DF4601",
    "Boston Red Sox":        "#BD3039",
    "Chicago Cubs":          "#0E3386",
    "Chicago White Sox":     "#C4CED4",
    "Cincinnati Reds":       "#C6011F",
    "Cleveland Guardians":   "#E31937",
    "Colorado Rockies":      "#8B67BE",
    "Detroit Tigers":        "#FA4616",
    "Houston Astros":        "#EB6E1F",
    "Kansas City Royals":    "#004687",
    "Los Angeles Angels":    "#BA0021",
    "Los Angeles Dodgers":   "#005A9C",
    "Miami Marlins":         "#00A3E0",
    "Milwaukee Brewers":     "#FFC52F",
    "Minnesota Twins":       "#D31145",
    "New York Mets":         "#FF5910",
    "New York Yankees":      "#003087",
    "Oakland Athletics":     "#003831",
    "Las Vegas Athletics":   "#003831",
    "Philadelphia Phillies": "#E81828",
    "Pittsburgh Pirates":    "#FDB827",
    "San Diego Padres":      "#FFC425",
    "San Francisco Giants":  "#FD5A1E",
    "Seattle Mariners":      "#005C5C",
    "St. Louis Cardinals":   "#C41E3A",
    "Tampa Bay Rays":        "#8FBCE6",
    "Texas Rangers":         "#003278",
    "Toronto Blue Jays":     "#134A8E",
    "Washington Nationals":  "#AB0003",
}
DEFAULT_TEAM_COLOR = "#334455"

PARK_FACTORS = {
    "Philadelphia Phillies":  108, "Colorado Rockies":       116,
    "Boston Red Sox":         104, "Cincinnati Reds":        106,
    "Texas Rangers":          103, "Chicago Cubs":           104,
    "Toronto Blue Jays":      103, "Miami Marlins":           99,
    "Tampa Bay Rays":          97, "Detroit Tigers":          97,
    "Oakland Athletics":       98, "New York Mets":           97,
    "San Francisco Giants":    96, "Seattle Mariners":        96,
    "Pittsburgh Pirates":      97, "Baltimore Orioles":      101,
    "Atlanta Braves":         103, "Houston Astros":         100,
    "Los Angeles Dodgers":    101, "New York Yankees":       103,
    "Chicago White Sox":      100, "Cleveland Guardians":     98,
    "Kansas City Royals":     101, "Minnesota Twins":        101,
    "Los Angeles Angels":     100, "San Diego Padres":        99,
    "Arizona Diamondbacks":   103, "St. Louis Cardinals":    100,
    "Milwaukee Brewers":       99, "Washington Nationals":   101,
}
DEFAULT_PARK_FACTOR = 100

CONF_COLORS = {
    "A+": ("#00ff88", "#003322"),
    "A":  ("#00cc66", "#002211"),
    "B+": ("#66aaff", "#001133"),
    "B":  ("#4488dd", "#000e22"),
    "C+": ("#ffaa33", "#221100"),
    "C":  ("#ff6644", "#220800"),
}
MATCH_COLORS = {
    "A+": "#00ff88", "A": "#00cc66",
    "B":  "#66aaff", "C": "#888899",
    "D":  "#ff4444",
}


# ============================================================
#  HELPERS
# ============================================================

def api_get(url, params=None, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            time.sleep(DELAY_BETWEEN_CALLS)
            return r.json()
        except Exception:
            time.sleep(1)
    return None

def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def fmt_avg(avg):
    return f".{int(safe_float(avg) * 1000):03d}" if avg else ".---"

def parse_avg(avg_str):
    try:
        return float("0" + avg_str) if str(avg_str).startswith(".") else float(avg_str)
    except (TypeError, ValueError):
        return 0.0

def format_time(iso_time):
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        dt_et = dt - timedelta(hours=4)
        return dt_et.strftime("%I:%M %p ET")
    except Exception:
        return "TBD"

def esc(s):
    """HTML escape a string."""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


# ============================================================
#  LINEUP CONFIRMATION
# ============================================================

def get_game_lineups(game_pk):
    data = api_get(f"{MLB_API}/game/{game_pk}/boxscore")
    result = {"confirmed": False}
    if not data:
        return result
    try:
        teams = data.get("teams", {})
        any_confirmed = False
        for side in ["away", "home"]:
            team_data = teams.get(side, {})
            team_id   = team_data.get("team", {}).get("id")
            players   = team_data.get("players", {})
            order_map = {}
            for pid_key, pdata in players.items():
                order  = pdata.get("battingOrder")
                status = pdata.get("gameStatus", {}).get("isOnBench", True)
                if order and not status:
                    batting_pos = int(str(order).strip()) // 100
                    if 1 <= batting_pos <= 9:
                        player_id = pdata.get("person", {}).get("id")
                        if player_id:
                            order_map[player_id] = batting_pos
                            any_confirmed = True
            if team_id:
                result[team_id] = order_map
        result["confirmed"] = any_confirmed
    except Exception:
        pass
    return result


# ============================================================
#  HEAD-TO-HEAD  (Career + This Season + Savant)
# ============================================================

def _parse_h2h_data(data, year=None):
    # MLB API returns year-by-year splits, not a single career aggregate — sum them all.
    # Pass year= to restrict to a specific season (API ignores season param for vsPlayer).
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return {}
        if year is not None:
            splits = [s for s in splits if str(s.get("season", "")) == str(year)]
        if not splits:
            return {}
        pa  = sum(int(s["stat"].get("plateAppearances", 0) or 0) for s in splits)
        if pa == 0:
            return {}
        ab  = sum(int(s["stat"].get("atBats",      0) or 0) for s in splits)
        h   = sum(int(s["stat"].get("hits",         0) or 0) for s in splits)
        hr  = sum(int(s["stat"].get("homeRuns",     0) or 0) for s in splits)
        rbi = sum(int(s["stat"].get("rbi",          0) or 0) for s in splits)
        bb  = sum(int(s["stat"].get("baseOnBalls",  0) or 0) for s in splits)
        hbp = sum(int(s["stat"].get("hitByPitch",   0) or 0) for s in splits)
        sf  = sum(int(s["stat"].get("sacFlies",     0) or 0) for s in splits)
        d2  = sum(int(s["stat"].get("doubles",      0) or 0) for s in splits)
        d3  = sum(int(s["stat"].get("triples",      0) or 0) for s in splits)
        tb  = (h - d2 - d3 - hr) + 2*d2 + 3*d3 + 4*hr   # total bases
        avg = round(h  / ab,           3) if ab > 0            else 0.0
        obp = round((h + bb + hbp) / (pa - sf), 3) if (pa - sf) > 0 else 0.0
        slg = round(tb / ab,           3) if ab > 0            else 0.0
        return {
            "pa":  pa,
            "h":   h,
            "hr":  hr,
            "rbi": rbi,
            "avg": avg,
            "obp": obp,
            "slg": slg,
            "ops": round(obp + slg, 3),
        }
    except Exception:
        return {}


def get_h2h_stats(batter_id, pitcher_id):
    if not batter_id or not pitcher_id:
        return {}

    # Single API call — career endpoint returns all years as separate splits.
    # We derive both career totals and current-season totals by filtering on split["season"].
    this_year = date.today().year
    data = api_get(
        f"{MLB_API}/people/{batter_id}/stats",
        params={"stats": "vsPlayer", "group": "hitting", "opposingPlayerId": pitcher_id}
    )
    if not data:
        return {}

    career = _parse_h2h_data(data)                    # sum all splits = true career
    season = _parse_h2h_data(data, year=this_year)    # only this year's split

    if not career and not season:
        return {}

    return {
        "career":  career,
        "season":  season,
        "trusted": career.get("pa", 0) >= H2H_MIN_PA,
        "pa":      career.get("pa",  0),
        "h":       career.get("h",   0),
        "hr":      career.get("hr",  0),
        "rbi":     career.get("rbi", 0),
        "avg":     career.get("avg", 0),
        "obp":     career.get("obp", 0),
        "slg":     career.get("slg", 0),
        "ops":     career.get("ops", 0),
    }


def h2h_multiplier(h2h):
    if not h2h or not h2h.get("trusted"):
        return 1.0, "x1.00 (small sample or no data)"
    avg = h2h.get("avg", 0)
    if avg >= H2H_HOT_AVG:
        return 1.20, f"x1.20 owns this pitcher ({fmt_avg(avg)} career)"
    elif avg <= H2H_COLD_AVG:
        return 0.85, f"x0.85 struggles vs pitcher ({fmt_avg(avg)} career)"
    return 1.00, f"x1.00 neutral ({fmt_avg(avg)} career)"


def h2h_badge(h2h):
    if not h2h or h2h.get("pa", 0) == 0:
        return "---", "#555566"
    avg = h2h.get("avg", 0)
    pa  = h2h.get("pa", 0)
    if avg >= 0.350:
        return f"HOT {fmt_avg(avg)} / {pa}PA", "#00cc66"
    elif avg <= 0.150:
        return f"COLD {fmt_avg(avg)} / {pa}PA", "#ff4444"
    return f"NEU {fmt_avg(avg)} / {pa}PA", "#66aaff"


def h2h_card_html(h2h, pitcher_name, p_stats=None):
    if not h2h:
        return f'<div class="h2h-empty">No H2H data vs {esc(pitcher_name)}</div>'

    career = h2h.get("career", {})
    season = h2h.get("season", {})
    rows   = ""

    if career:
        pa  = career.get("pa", 0)
        avg = career.get("avg", 0)
        hr  = career.get("hr", 0)
        rbi = career.get("rbi", 0)
        h   = career.get("h", 0)
        trust  = "Trusted" if pa >= H2H_MIN_PA else "Small sample"
        tcol   = "#00cc66" if avg >= 0.350 else ("#ff4444" if avg <= 0.150 else "#66aaff")
        tlabel = "Owns" if avg >= 0.350 else ("Struggles" if avg <= 0.150 else "Neutral")
        rows += (
            '<div class="h2h-row">'
            '<span class="h2h-label">Career</span>'
            f'<span class="h2h-stat">{h}-for-{pa} {fmt_avg(avg)}</span>'
            f'<span class="h2h-stat">{hr}HR {rbi}RBI</span>'
            f'<span class="h2h-trend" style="color:{tcol}">{tlabel} ({trust})</span>'
            '</div>'
        )
    else:
        rows += (
            '<div class="h2h-row">'
            '<span class="h2h-label">Career</span>'
            '<span class="h2h-empty">No career data</span>'
            '</div>'
        )

    yr = date.today().year
    if season:
        spa  = season.get("pa", 0)
        savg = season.get("avg", 0)
        shr  = season.get("hr", 0)
        srbi = season.get("rbi", 0)
        sh   = season.get("h", 0)
        scol = "#00cc66" if savg >= 0.350 else ("#ff4444" if savg <= 0.150 else "#66aaff")
        slbl = "Hot" if savg >= 0.350 else ("Cold" if savg <= 0.150 else "Avg")
        rows += (
            '<div class="h2h-row">'
            f'<span class="h2h-label">{yr}</span>'
            f'<span class="h2h-stat" style="color:{scol}">{sh}-for-{spa} {fmt_avg(savg)}</span>'
            f'<span class="h2h-stat">{shr}HR {srbi}RBI</span>'
            f'<span class="h2h-trend" style="color:{scol}">{slbl}</span>'
            '</div>'
        )
    else:
        rows += (
            '<div class="h2h-row">'
            f'<span class="h2h-label">{yr}</span>'
            '<span class="h2h-empty">No matchups yet this season</span>'
            '</div>'
        )

    return rows


def pitcher_kbb_html(p_stats):
    """K% / BB% / HR9 row for the Pitcher box."""
    if not p_stats:
        return ""
    try:
        ip     = safe_float(p_stats.get("inningsPitched"), 1)
        so     = safe_float(p_stats.get("strikeOuts"), 0)
        bb     = safe_float(p_stats.get("baseOnBalls"), 0)
        hr_all = safe_float(p_stats.get("homeRuns"), 0)
        bf     = safe_float(p_stats.get("battersFaced"), max(ip * 3, 1))
        k_pct  = round((so / bf) * 100, 1) if bf > 0 else 0
        bb_pct = round((bb / bf) * 100, 1) if bf > 0 else 0
        hr9    = round((hr_all / ip) * 9, 2) if ip > 0 else 0
        kcol   = "#00cc66" if k_pct >= 25 else ("#ffaa33" if k_pct >= 18 else "#ff6644")
        bbcol  = "#ff6644" if bb_pct >= 10 else ("#ffaa33" if bb_pct >= 7 else "#00cc66")
        hrcol  = "#ff6644" if hr9 >= 1.5 else ("#ffaa33" if hr9 >= 1.0 else "#00cc66")
        return (
            f'<div class="fc-ib-sub" style="margin-top:4px">'
            f'<span style="color:{kcol}">K% {k_pct}%</span>'
            f'<span style="color:#445566"> · </span>'
            f'<span style="color:{bbcol}">BB% {bb_pct}%</span>'
            f'<span style="color:#445566"> · </span>'
            f'<span style="color:{hrcol}">HR/9 {hr9}</span>'
            f'</div>'
        )
    except Exception:
        return ""


# ============================================================
#  PITCHER DATA
# ============================================================

def get_pitcher_splits(player_id):
    if not player_id:
        return {}
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "statSplits", "group": "pitching",
                "season": date.today().year, "sitCodes": "vl,vr"}
    )
    results = {}
    try:
        for split in data["stats"][0]["splits"]:
            code = split.get("split", {}).get("code")
            stat = split.get("stat", {})
            if code == "vl": results["L"] = stat
            elif code == "vr": results["R"] = stat
    except Exception:
        pass
    return results

def get_pitcher_season_stats(player_id):
    if not player_id:
        return {}
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "season", "group": "pitching", "season": date.today().year}
    )
    try:
        return data["stats"][0]["splits"][0]["stat"]
    except Exception:
        return {}

def get_pitcher_last_start(player_id):
    """
    Fetch the pitcher's last start stats (runs allowed, IP, pitches).
    Returns a dict with er, ip, pitches, date — or empty dict.
    A pitcher who got lit up last start is a target; we flag that.
    """
    if not player_id:
        return {}
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching",
                "season": date.today().year, "limit": 1}
    )
    try:
        split = data["stats"][0]["splits"][0]
        st    = split.get("stat", {})
        return {
            "date":    split.get("date", "?"),
            "opp":     split.get("opponent", {}).get("abbreviation", "?"),
            "er":      int(st.get("earnedRuns", 0)),
            "ip":      safe_float(st.get("inningsPitched"), 0),
            "hits":    int(st.get("hits", 0)),
            "hr":      int(st.get("homeRuns", 0)),
            "bb":      int(st.get("baseOnBalls", 0)),
            "so":      int(st.get("strikeOuts", 0)),
            "pitches": int(st.get("numberOfPitches", 0)),
        }
    except Exception:
        return {}


def get_pitcher_recent_form(player_id, season_stats=None):
    """
    Fetch last 5 starts and compare to full-season stats.
    Returns a dict with:
      recent_era    — ERA over last 5 starts
      season_era    — full season ERA
      trend         — 'hot', 'cold', 'neutral'
      days_rest     — days since last start (fatigue flag)
      pitch_count   — pitches thrown last outing (fatigue flag)
      label         — human-readable summary
      color         — display color
      mult          — score multiplier (0.85–1.15) applied to matchup
    """
    if not player_id:
        return {"mult": 1.0, "label": "No pitcher data", "color": "#445566", "trend": "neutral"}
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching",
                "season": date.today().year, "limit": 5}
    )
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return {"mult": 1.0, "label": "No recent starts", "color": "#445566", "trend": "neutral"}

        # Recent 5 starts ERA
        recent_er = sum(int(s["stat"].get("earnedRuns", 0)) for s in splits)
        recent_ip = sum(safe_float(s["stat"].get("inningsPitched"), 0) for s in splits)
        recent_era = round((recent_er / recent_ip * 9), 2) if recent_ip > 0 else 99.0

        # Days rest from last start
        try:
            last_date  = datetime.strptime(splits[0]["date"], "%Y-%m-%d").date()
            days_rest  = (date.today() - last_date).days
        except Exception:
            days_rest  = 5

        # Pitch count last outing
        pitch_count = int(splits[0]["stat"].get("numberOfPitches", 0))

        # Compare to season ERA — use pre-fetched stats when available
        s_data = season_stats or api_get(
            f"{MLB_API}/people/{player_id}/stats",
            params={"stats": "season", "group": "pitching", "season": date.today().year}
        )
        season_era = safe_float(
            s_data["stats"][0]["splits"][0]["stat"].get("era", 4.50)
            if s_data else 4.50
        )

        # Fatigue flags — separate reasons, separate labels
        short_rest   = days_rest <= 4
        high_pitches = pitch_count >= 100
        fatigued     = short_rest or high_pitches

        # Trend vs season
        era_diff = recent_era - season_era
        if fatigued:
            mult  = 1.10
            trend = "fatigued"
            # Only mention days rest if it's actually short — don't print misleading "29d"
            if short_rest and high_pitches:
                label = f"⚠ Short rest ({days_rest}d) + {pitch_count} pitches last out — may tire early"
            elif short_rest:
                label = f"⚠ Short rest ({days_rest}d since last start) — may tire early"
            else:
                label = f"⚠ {pitch_count} pitches last out — high workload, may tire early"
            color = "#ffaa33"
        elif era_diff >= 1.5:
            mult  = 1.15
            trend = "cold"
            label = f"🎯 Struggling recently — {recent_era:.2f} ERA last 5 starts vs {season_era:.2f} season"
            color = "#00cc66"
        elif era_diff <= -1.5:
            mult  = 0.88
            trend = "hot"
            label = f"🔒 Locked in — {recent_era:.2f} ERA last 5 starts vs {season_era:.2f} season"
            color = "#ff6644"
        else:
            mult  = 1.0
            trend = "neutral"
            label = f"Consistent — {recent_era:.2f} ERA last 5 starts"
            color = "#445566"

        return {
            "mult":        mult,
            "label":       label,
            "color":       color,
            "trend":       trend,
            "recent_era":  recent_era,
            "season_era":  season_era,
            "days_rest":   days_rest,
            "pitch_count": pitch_count,
            "fatigued":    fatigued,
        }
    except Exception:
        return {"mult": 1.0, "label": "No recent data", "color": "#445566", "trend": "neutral"}


def get_player_injury_status(player_id):
    """
    Check MLB API for a player's current injury / transaction status.
    Returns a dict: { flagged: bool, label: str, color: str }
    Flags anyone on the IL (7-day, 10-day, 15-day, 60-day) or day-to-day.
    Returns clean dict on any failure — never blocks the pipeline.
    """
    if not player_id:
        return {"flagged": False, "label": "", "color": "#445566"}
    try:
        data = api_get(f"{MLB_API}/people/{player_id}", params={"hydrate": "currentTeam"})
        if not data or not data.get("people"):
            return {"flagged": False, "label": "", "color": "#445566"}
        person = data["people"][0]
        status = person.get("status", {}).get("description", "").lower()
        if any(s in status for s in ["day-to-day", "dtd"]):
            return {"flagged": True, "label": "⚠ Day-to-Day", "color": "#ffaa33"}
        if any(s in status for s in ["7-day il", "10-day il", "15-day il", "60-day il",
                                      "injured list"]):
            return {"flagged": True, "label": "🚫 IL — skip", "color": "#ff4444"}
        if "bereavement" in status or "paternity" in status:
            return {"flagged": True, "label": f"⚠ {status.title()}", "color": "#ffaa33"}
    except Exception:
        pass
    return {"flagged": False, "label": "", "color": "#445566"}


# ── Ballpark GPS coordinates for weather lookup ──────────────
PARK_COORDS = {
    "Philadelphia Phillies":  (39.9061, -75.1665),
    "Colorado Rockies":       (39.7559, -104.9942),
    "Boston Red Sox":         (42.3467, -71.0972),
    "Cincinnati Reds":        (39.0979, -84.5082),
    "Texas Rangers":          (32.7473, -97.0831),
    "Chicago Cubs":           (41.9484, -87.6553),
    "Toronto Blue Jays":      (43.6414, -79.3894),
    "Miami Marlins":          (25.7781, -80.2197),
    "Tampa Bay Rays":         (27.7683, -82.6534),
    "Detroit Tigers":         (42.3390, -83.0485),
    "Oakland Athletics":      (37.7516, -122.2005),
    "New York Mets":          (40.7571, -73.8458),
    "San Francisco Giants":   (37.7786, -122.3893),
    "Seattle Mariners":       (47.5914, -122.3325),
    "Pittsburgh Pirates":     (40.4469, -80.0057),
    "Baltimore Orioles":      (39.2838, -76.6216),
    "Atlanta Braves":         (33.8908, -84.4678),
    "Houston Astros":         (29.7572, -95.3555),
    "Los Angeles Dodgers":    (34.0739, -118.2400),
    "New York Yankees":       (40.8296, -73.9262),
    "Chicago White Sox":      (41.8299, -87.6338),
    "Cleveland Guardians":    (41.4962, -81.6852),
    "Kansas City Royals":     (39.0517, -94.4803),
    "Minnesota Twins":        (44.9817, -93.2775),
    "Los Angeles Angels":     (33.8003, -117.8827),
    "San Diego Padres":       (32.7076, -117.1570),
    "Arizona Diamondbacks":   (33.4453, -112.0667),
    "St. Louis Cardinals":    (38.6226, -90.1928),
    "Milwaukee Brewers":      (43.0280, -87.9712),
    "Washington Nationals":   (38.8730, -77.0074),
    "Las Vegas Athletics":    (36.1699, -115.1398),
}

# Domed/retractable stadiums — weather doesn't affect these
DOMED_PARKS = {
    "Tampa Bay Rays", "Toronto Blue Jays", "Miami Marlins",
    "Houston Astros", "Milwaukee Brewers", "Arizona Diamondbacks",
    "Seattle Mariners", "Minnesota Twins",
}

def get_weather(home_team):
    """
    Fetch current weather at the ballpark using Open-Meteo (free, no key).
    Returns a dict with temp_f, wind_mph, wind_dir_deg, wind_dir_txt,
    condition, is_dome, wind_boost — or minimal defaults on failure.

    Wind boost logic:
      +1.15 = strong wind blowing OUT (270°±45° for most parks) ≥ 15 mph
      +1.08 = moderate wind out ≥ 8 mph
      +0.92 = strong wind IN ≥ 15 mph
      +0.96 = moderate wind in ≥ 8 mph
       1.00 = crosswind or calm — neutral
    """
    is_dome = home_team in DOMED_PARKS
    default = {
        "temp_f": "N/A", "wind_mph": 0, "wind_dir_deg": 0,
        "wind_dir_txt": "—", "condition": "N/A",
        "is_dome": is_dome, "wind_boost": 1.0,
        "wind_label": "Dome — weather neutral" if is_dome else "No weather data",
        "wind_color": "#445566",
    }
    if is_dome:
        default["wind_label"] = "🏟 Dome — weather irrelevant"
        return default

    coords = PARK_COORDS.get(home_team)
    if not coords:
        return default

    lat, lon = coords
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,wind_speed_10m,wind_direction_10m,weather_code"
            f"&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto"
        )
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return default
        d    = r.json().get("current", {})
        temp = round(safe_float(d.get("temperature_2m"), 70))
        wind = round(safe_float(d.get("wind_speed_10m"), 0))
        wdir = int(safe_float(d.get("wind_direction_10m"), 0))
        wcode = int(d.get("weather_code", 0))

        # Convert weather code to readable condition
        if wcode == 0:                    cond = "Clear ☀️"
        elif wcode in (1, 2, 3):          cond = "Partly cloudy ⛅"
        elif wcode in (45, 48):           cond = "Foggy 🌫"
        elif wcode in (51,53,55,61,63,65):cond = "Rain 🌧"
        elif wcode in (71,73,75,77):      cond = "Snow ❄️"
        elif wcode in (80,81,82):         cond = "Showers 🌦"
        elif wcode in (95,96,99):         cond = "Thunderstorm ⛈"
        else:                             cond = "Cloudy ☁️"

        # Wind direction text
        dirs = ["N","NE","E","SE","S","SW","W","NW"]
        wdir_txt = dirs[round(wdir / 45) % 8]

        # Wind boost — "out to center" is roughly 270° at most US parks
        # (batters face east/NE, so wind from SW/W blows out to CF/RF)
        # We treat 225°–315° as "blowing out", 45°–135° as "blowing in"
        boost      = 1.0
        wind_label = f"{wind} mph {wdir_txt} · {cond} · {temp}°F"
        wind_color = "#445566"

        out_window = (225 <= wdir <= 315)
        in_window  = (45  <= wdir <= 135)

        if wind >= 15 and out_window:
            boost = 1.15
            wind_label = f"💨 Wind OUT {wind}mph {wdir_txt} — HR boost! · {temp}°F"
            wind_color = "#00ff88"
        elif wind >= 8 and out_window:
            boost = 1.08
            wind_label = f"💨 Wind out {wind}mph {wdir_txt} — slight HR boost · {temp}°F"
            wind_color = "#66aaff"
        elif wind >= 15 and in_window:
            boost = 0.92
            wind_label = f"🌬 Wind IN {wind}mph {wdir_txt} — suppresses HR · {temp}°F"
            wind_color = "#ff6644"
        elif wind >= 8 and in_window:
            boost = 0.96
            wind_label = f"🌬 Wind in {wind}mph {wdir_txt} — slight HR penalty · {temp}°F"
            wind_color = "#ffaa33"
        elif wcode in (61,63,65,80,81,82,95,96,99):
            wind_label = f"🌧 Rain risk — {cond} · {wind}mph {wdir_txt} · {temp}°F"
            wind_color = "#ffaa33"

        return {
            "temp_f":       temp,
            "wind_mph":     wind,
            "wind_dir_deg": wdir,
            "wind_dir_txt": wdir_txt,
            "condition":    cond,
            "is_dome":      False,
            "wind_boost":   boost,
            "wind_label":   wind_label,
            "wind_color":   wind_color,
        }
    except Exception:
        return default


# ============================================================
#  VEGAS TEAM TOTALS
# ============================================================

def get_game_totals():
    """Fetch today's MLB game O/U totals from The Odds API.
    Returns dict: team_name_last_word -> game_total (float).
    Falls back gracefully to empty dict if key not set or API fails."""
    if not ODDS_API_KEY:
        return {}
    try:
        resp = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "totals", "dateFormat": "iso"},
            timeout=10
        )
        games = resp.json()
        totals = {}
        for game in games:
            for team in (game.get("home_team", ""), game.get("away_team", "")):
                if not team:
                    continue
                for book in game.get("bookmakers", []):
                    for market in book.get("markets", []):
                        if market["key"] == "totals":
                            for outcome in market.get("outcomes", []):
                                if outcome["name"] == "Over":
                                    # Index by last word of team name to handle minor name differences
                                    totals[team.split()[-1]] = float(outcome["point"])
                            break
                    break
        return totals
    except Exception:
        return {}


def vegas_game_boost(game_totals, home_team, away_team):
    """Return (boost_mult, label) based on game O/U total."""
    total = game_totals.get(home_team.split()[-1]) or game_totals.get(away_team.split()[-1])
    if not total:
        return 1.0, ""
    if total >= 10.0:
        return 1.08, f"🎰 Vegas O/U {total} (run-fest)"
    elif total >= 9.0:
        return 1.04, f"🎰 Vegas O/U {total} (hitter-friendly)"
    elif total >= 8.0:
        return 1.0,  f"🎰 Vegas O/U {total} (neutral)"
    elif total >= 7.0:
        return 0.96, f"🎰 Vegas O/U {total} (pitcher-friendly)"
    else:
        return 0.92, f"🎰 Vegas O/U {total} (low-scoring)"


def get_implied_team_total(game_totals, team_name):
    """Approximate a team's implied run total from the game O/U.
    Without a run-line market we split the total evenly. Still far better
    than a flat constant for driving Runs/RBI projections."""
    t = game_totals.get(team_name.split()[-1])
    if not t:
        return None
    return t / 2.0


# ============================================================
#  BASEBALL SAVANT — EXPECTED STATS (xBA)
# ============================================================

SAVANT_XBA = {}  # {mlbam_player_id: est_ba}

def load_savant_xba(year=None):
    """Fetch Statcast expected batting average (xBA) for all batters once,
    keyed by MLBAM player id (matches the MLB Stats API ids we use).
    Degrades gracefully to an empty map if the request fails."""
    global SAVANT_XBA
    import csv, io
    year = year or date.today().year
    try:
        r = requests.get(
            "https://baseballsavant.mlb.com/leaderboard/expected_statistics",
            params={"type": "batter", "year": year, "position": "",
                    "team": "", "min": "1", "csv": "true"},
            timeout=20,
        )
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")  # strip BOM so quoted name column aligns
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            pid = row.get("player_id") or row.get("playerid")
            est = row.get("est_ba") or row.get("xba")
            if pid and est:
                try:
                    SAVANT_XBA[int(pid)] = float(est)
                    count += 1
                except ValueError:
                    pass
        print(f"   📊 Savant xBA loaded for {count} batters")
    except Exception as e:
        print(f"   📊 Savant xBA unavailable ({e}) — projecting without it")
    return SAVANT_XBA


# ============================================================
#  BATTER DATA
# ============================================================

def get_team_batters(team_id):
    data = api_get(f"{MLB_API}/teams/{team_id}/roster", params={"rosterType": "active"})
    if not data:
        return []
    return [
        {"id": p["person"]["id"], "name": p["person"]["fullName"],
         "pos": p["position"]["abbreviation"]}
        for p in data.get("roster", [])
        if p["position"]["abbreviation"] not in ["P", "SP", "RP"]
    ]

def get_batter_data(player_id):
    s_data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "season", "group": "hitting", "season": date.today().year}
    )
    stats = {}
    try:
        if s_data and s_data["stats"][0].get("splits"):
            stats = s_data["stats"][0]["splits"][0].get("stat", {})
    except (IndexError, KeyError):
        pass

    r_data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "hitting",
                "season": date.today().year, "limit": RECENT_GAME_COUNT}
    )
    recent_games, recent_dk = [], []
    try:
        if r_data and r_data["stats"][0].get("splits"):
            for g in r_data["stats"][0]["splits"][:RECENT_GAME_COUNT]:
                st  = g.get("stat", {})
                h   = int(st.get("hits", 0))
                r   = int(st.get("runs", 0))
                rbi = int(st.get("rbi", 0))
                hr  = int(st.get("homeRuns", 0))
                dk  = h + r + rbi
                recent_games.append({
                    "date": g.get("date", "?"),
                    "opp":  g.get("opponent", {}).get("abbreviation", "?"),
                    "H": h, "R": r, "RBI": rbi, "HR": hr, "DK": dk
                })
                recent_dk.append(dk)
    except (IndexError, KeyError):
        pass

    p_data = api_get(f"{MLB_API}/people/{player_id}")
    side = "R"
    try:
        if p_data and p_data.get("people"):
            side = p_data["people"][0].get("batSide", {}).get("code", "R")
    except (IndexError, KeyError):
        pass

    return stats, recent_games, recent_dk, side

def get_home_away_split(player_id, is_home):
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "statSplits", "group": "hitting",
                "season": date.today().year, "sitCodes": "h,a"}
    )
    try:
        for s in data["stats"][0]["splits"]:
            code = s.get("split", {}).get("code")
            stat = s.get("stat", {})
            if is_home and code == "h":
                return safe_float(stat.get("ops"), 0.750)
            if not is_home and code == "a":
                return safe_float(stat.get("ops"), 0.750)
    except Exception:
        pass
    return 0.750


def get_pitcher_hand(pitcher_id):
    """Return 'L' or 'R' for the pitcher's throwing hand."""
    if not pitcher_id:
        return None
    data = api_get(f"{MLB_API}/people/{pitcher_id}")
    try:
        return data["people"][0].get("pitchHand", {}).get("code", None)
    except Exception:
        return None


def get_batter_platoon_split(player_id, pitcher_hand):
    """Return batter's stats vs LHP or RHP this season (min 15 PA)."""
    if not pitcher_hand or pitcher_hand not in ("L", "R"):
        return {}
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "statSplits", "group": "hitting",
                "season": date.today().year,
                "sitCodes": "vl" if pitcher_hand == "L" else "vr"}
    )
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return {}
        stat = splits[0]["stat"]
        pa = int(stat.get("plateAppearances", 0) or 0)
        if pa < 15:
            return {}
        return {
            "pa":  pa,
            "avg": safe_float(stat.get("avg", 0)),
            "obp": safe_float(stat.get("obp", 0)),
            "slg": safe_float(stat.get("slg", 0)),
            "ops": safe_float(stat.get("ops", 0)),
        }
    except Exception:
        return {}


def platoon_multiplier(platoon_split, season_ops):
    """Compare batter's vs-hand OPS to season OPS; return score multiplier."""
    ops = platoon_split.get("ops", 0)
    if not ops or not season_ops or season_ops <= 0:
        return 1.0
    ratio = ops / season_ops
    if ratio >= 1.15:   return 1.08
    elif ratio >= 1.05: return 1.04
    elif ratio >= 0.95: return 1.0
    elif ratio >= 0.85: return 0.95
    else:               return 0.90


def compute_batter_form(recent_dk, season_stats):
    if not recent_dk or not season_stats:
        return {"mult": 1.0, "label": "No data", "trend": "neutral",
                "recent_avg": 0, "season_avg": 0, "last5_avg": 0}
    gp = max(int(season_stats.get("gamesPlayed", 1) or 1), 1)
    season_dk_pg = (
        safe_float(season_stats.get("hits", 0)) +
        safe_float(season_stats.get("runs", 0)) +
        safe_float(season_stats.get("rbi",  0))
    ) / gp
    if season_dk_pg <= 0:
        return {"mult": 1.0, "label": "No baseline", "trend": "neutral",
                "recent_avg": 0, "season_avg": 0, "last5_avg": 0}
    recent_dk_pg = sum(recent_dk) / len(recent_dk)
    last5        = recent_dk[-5:] if len(recent_dk) >= 5 else recent_dk
    last5_avg    = sum(last5) / len(last5)
    ratio = recent_dk_pg / season_dk_pg
    if ratio >= 1.50:
        label, mult, trend = "🔥 Hot",  1.12, "hot"
    elif ratio >= 1.20:
        label, mult, trend = "↑ Warm", 1.06, "warm"
    elif ratio >= 0.80:
        label, mult, trend = "→ Avg",  1.0,  "neutral"
    elif ratio >= 0.55:
        label, mult, trend = "↓ Cool", 0.94, "cool"
    else:
        label, mult, trend = "❄ Cold", 0.87, "cold"
    return {
        "mult":       mult,
        "label":      label,
        "trend":      trend,
        "recent_avg": recent_dk_pg,
        "season_avg": season_dk_pg,
        "last5_avg":  last5_avg,
    }


# ============================================================
#  SCORING ENGINE
# ============================================================

def batter_score(stats, recent_dk, park_factor, pitcher_splits,
                 batter_side, batting_order, split_ops, h2h,
                 weather_boost=1.0, pitcher_form_mult=1.0, batter_form_mult=1.0,
                 vegas_boost=1.0, platoon_mult=1.0):
    # ── Season base: use PER-GAME averages, not raw totals ────
    gp = max(int(stats.get("gamesPlayed", 1) or 1), 1)
    h_pg   = safe_float(stats.get("hits"))  / gp
    r_pg   = safe_float(stats.get("runs"))  / gp
    rbi_pg = safe_float(stats.get("rbi"))   / gp
    season_base = (h_pg + r_pg + rbi_pg) * 50

    momentum     = sum(recent_dk) * 3.0
    matchup_mult = 1.0
    if batter_side in pitcher_splits:
        opp_ops = safe_float(pitcher_splits[batter_side].get("ops"), 0.750)
        if opp_ops > 0.850:   matchup_mult = 1.25
        elif opp_ops < 0.650: matchup_mult = 0.80
    h2h_mult, _  = h2h_multiplier(h2h)
    order_mult   = LINEUP_MULTIPLIERS.get(batting_order, 1.0) if batting_order else 1.0
    split_mult   = HOME_AWAY_BOOST if split_ops >= 0.850 else (HOME_AWAY_PENALTY if split_ops <= 0.650 else 1.0)
    park_mult    = park_factor / 100.0
    return round(
        (season_base + momentum)
        * matchup_mult * h2h_mult * order_mult
        * split_mult * park_mult
        * weather_boost * pitcher_form_mult * batter_form_mult * vegas_boost * platoon_mult,
        1
    )

# ============================================================
#  MATHEMATICAL HRR PROJECTION ENGINE  (v4)
#  Based on PA-weighted probability model:
#  Projected HRR = Projected H + Projected R + Projected RBI
#
#  H   = (PA - BB) × adjusted_avg
#  R   = (H + BB) × team_scoring_rate
#  RBI = runners_on_base_per_pa × pa × drive_in_rate
# ============================================================

# Average plate appearances per game by lineup position
# Based on MLB historical averages
PA_BY_ORDER = {
    1: 4.6, 2: 4.4, 3: 4.3, 4: 4.2,
    5: 4.1, 6: 4.0, 7: 3.9, 8: 3.85, 9: 3.8
}
DEFAULT_PA = 4.1  # Used when lineup not confirmed

# Average runners on base per PA by lineup position
# Middle order sees more runners, bottom order sees fewer
RUNNERS_ON_BY_ORDER = {
    1: 0.30, 2: 0.35, 3: 0.42, 4: 0.50,
    5: 0.48, 6: 0.40, 7: 0.35, 8: 0.30, 9: 0.28
}
DEFAULT_RUNNERS_ON = 0.38

# Standard team scoring rate — % of times on base that score
# Adjusted up/down based on park factor
BASE_SCORING_RATE = 0.32

# Drive-in rate by batting order (clutch factor + HR probability)
DRIVE_IN_RATE_BY_ORDER = {
    1: 0.18, 2: 0.20, 3: 0.24, 4: 0.26,
    5: 0.24, 6: 0.20, 7: 0.18, 8: 0.16, 9: 0.15
}
DEFAULT_DRIVE_IN_RATE = 0.20


def project_hrr(stats, pitcher_splits, batter_side, batting_order,
                park_factor, h2h, recent_dk,
                implied_team_total=None, xba=None,
                pitcher_stats=None, batter_k_rate=None):
    """
    Mathematical HRR projection using PA-based probability model.

    v5 additions:
      implied_team_total — Vegas implied runs for this team → scales R & RBI
      xba                — Statcast expected batting average → sharpens H
      pitcher_stats      — opponent AVG-against & K/9 → hit suppression
      batter_k_rate      — batter strikeout rate → hit suppression

    Returns:
        proj_h      — projected hits tonight
        proj_r      — projected runs tonight
        proj_rbi    — projected RBIs tonight
        proj_total  — projected HRR total
        proj_str    — formatted display string
        floor       — conservative floor
        ceiling     — optimistic ceiling
        breakdown   — dict of all calculation components for display
    """
    # ── Base stats ───────────────────────────────────────────
    season_avg = safe_float(stats.get("avg"), 0.260)
    season_obp = safe_float(stats.get("obp"), 0.330)
    season_slg = safe_float(stats.get("slg"), 0.420)
    games_played = max(int(stats.get("gamesPlayed", 1) or 1), 1)
    season_bb   = safe_float(stats.get("baseOnBalls"), 0)
    season_pa   = safe_float(stats.get("plateAppearances"), games_played * 4.0)
    bb_rate     = (season_bb / season_pa) if season_pa > 0 else 0.08

    # ── Plate appearances tonight ─────────────────────────────
    pa = PA_BY_ORDER.get(batting_order, DEFAULT_PA) if batting_order else DEFAULT_PA

    # ── Adjust AVG for pitcher matchup ────────────────────────
    matchup_adj = 1.0
    if batter_side in pitcher_splits:
        p_ops = safe_float(pitcher_splits[batter_side].get("ops"), 0.750)
        # Scale: league avg OPS ~.720, adjust proportionally
        matchup_adj = p_ops / 0.720
        matchup_adj = max(0.70, min(matchup_adj, 1.40))  # cap adjustment

    # Adjust for H2H history if trusted
    h2h_adj = 1.0
    if h2h and h2h.get("trusted"):
        h2h_avg = h2h.get("avg", season_avg)
        if h2h_avg > 0:
            h2h_adj = (h2h_avg / max(season_avg, 0.200))
            h2h_adj = max(0.75, min(h2h_adj, 1.35))  # cap adjustment

    # ── Statcast xBA blend ────────────────────────────────────
    # Expected batting average predicts FUTURE hits better than raw AVG
    # (strips out luck/BABIP noise). Weight it slightly higher than AVG.
    base_avg = season_avg
    if xba and xba > 0:
        base_avg = season_avg * 0.45 + xba * 0.55

    # ── Hit-suppression: strikeouts + pitcher contact quality ─
    hit_adj = 1.0
    if batter_k_rate is not None and batter_k_rate > 0:
        # League avg K% ~.22; high-K hitters get fewer balls in play
        hit_adj *= max(0.80, min(1.10, 1.0 - (batter_k_rate - 0.22) * 0.8))
    if pitcher_stats:
        k9 = safe_float(pitcher_stats.get("strikeoutsPer9Inn"), 8.5)
        # League avg ~8.5 K/9; high-K pitcher suppresses hits
        hit_adj *= max(0.82, min(1.10, 1.0 - (k9 - 8.5) * 0.012))
        opp_avg = safe_float(pitcher_stats.get("avg"), 0.250)
        if opp_avg > 0:
            # Pitcher's actual opponent batting-average-against vs league .250
            hit_adj *= max(0.85, min(1.15, opp_avg / 0.250))

    adjusted_avg = base_avg * matchup_adj * h2h_adj * hit_adj

    # ── Park factor adjustment ────────────────────────────────
    park_adj     = park_factor / 100.0
    scoring_rate = BASE_SCORING_RATE * park_adj

    # ── Vegas implied team total → run-scoring environment ────
    # The single best predictor of how many runs a lineup scores.
    # League-average implied team total ≈ 4.3 runs.
    env_mult = 1.0
    if implied_team_total and implied_team_total > 0:
        env_mult = max(0.75, min(1.35, implied_team_total / 4.3))
        scoring_rate *= env_mult

    # ── Step 1: Project Hits ──────────────────────────────────
    proj_bb  = pa * bb_rate
    proj_h   = (pa - proj_bb) * adjusted_avg

    # ── Step 2: Project Runs ──────────────────────────────────
    times_on_base = proj_h + proj_bb
    proj_r        = times_on_base * scoring_rate

    # ── Step 3: Project RBIs ─────────────────────────────────
    runners_on    = RUNNERS_ON_BY_ORDER.get(batting_order, DEFAULT_RUNNERS_ON)
    # More runs implied → more runners on base to drive in
    runners_on   *= env_mult
    drive_in_rate = DRIVE_IN_RATE_BY_ORDER.get(batting_order, DEFAULT_DRIVE_IN_RATE)
    # Add solo HR contribution (HR rate × PA)
    hr_rate = safe_float(stats.get("homeRuns"), 0) / (games_played * 4.0)
    solo_hr_rbi   = pa * hr_rate * 0.25  # ~25% of HRs are solo
    proj_rbi      = (runners_on * pa * drive_in_rate) + solo_hr_rbi

    # ── Total projection ─────────────────────────────────────
    proj_total = proj_h + proj_r + proj_rbi

    # ── Recent form adjustment ───────────────────────────────
    # Blend mathematical projection with recent actual performance
    if recent_dk and len(recent_dk) >= 3:
        recent_avg = sum(recent_dk) / len(recent_dk)
        # 60% math model, 40% recent form — balances theory with reality
        proj_total = (proj_total * 0.60) + (recent_avg * 0.40)
        proj_h     = proj_h * 0.60 + (recent_avg * 0.40 * 0.45)  # ~45% of HRR is hits
        proj_r     = proj_r * 0.60 + (recent_avg * 0.40 * 0.28)  # ~28% runs
        proj_rbi   = proj_rbi * 0.60 + (recent_avg * 0.40 * 0.27) # ~27% RBI

    # ── Floor and ceiling ─────────────────────────────────────
    variance = max(0.8, proj_total * 0.35)  # ~35% variance
    floor    = max(0, round(proj_total - variance * 0.6))
    ceiling  = round(proj_total + variance * 1.2)
    if recent_dk:
        ceiling = max(ceiling, max(recent_dk))  # ceiling never below best recent game

    proj_str = f"{floor}–{ceiling} DK pts"

    breakdown = {
        "pa":           round(pa, 1),
        "proj_bb":      round(proj_bb, 2),
        "adj_avg":      round(adjusted_avg, 3),
        "matchup_adj":  round(matchup_adj, 2),
        "h2h_adj":      round(h2h_adj, 2),
        "proj_h":       round(proj_h, 2),
        "proj_r":       round(proj_r, 2),
        "proj_rbi":     round(proj_rbi, 2),
        "proj_total":   round(proj_total, 2),
        "scoring_rate": round(scoring_rate, 3),
        "runners_on":   round(runners_on, 3),
        "drive_in":     drive_in_rate,
        "xba":          round(xba, 3) if xba else None,
        "hit_adj":      round(hit_adj, 2),
        "env_mult":     round(env_mult, 2),
        "implied_tt":   round(implied_team_total, 1) if implied_team_total else None,
    }

    return proj_h, proj_r, proj_rbi, proj_total, proj_str, floor, ceiling, breakdown


def projected_dk_range(recent_dk, stats=None, pitcher_splits=None,
                       batter_side="R", batting_order=0,
                       park_factor=100, h2h=None):
    """
    Wrapper that uses mathematical model when stats available,
    falls back to recent average method if not.
    """
    if stats and pitcher_splits is not None:
        _, _, _, _, proj_str, floor, ceiling, _ = project_hrr(
            stats, pitcher_splits, batter_side,
            batting_order, park_factor, h2h or {}, recent_dk or []
        )
        return proj_str, floor, ceiling

    # Fallback — recent average method
    if not recent_dk:
        return "?–?", 0, 0
    avg     = sum(recent_dk) / len(recent_dk)
    floor   = max(0, round(avg - 1))
    ceiling = max(round(avg + 1), max(recent_dk))
    return f"{floor}–{ceiling} DK pts", floor, ceiling


def dk_line_recommendation(recent_dk, proj_total=None):
    """
    Recommend DK line. Thresholds require a genuine edge over the line —
    not just barely clearing it. DK juice means you need real margin.
    """
    target = proj_total if proj_total is not None else None
    if target is None and recent_dk:
        target = sum(recent_dk) / len(recent_dk)
    if target is None:
        return "Over 0.5", 1

    # Tightened: need projection well above the line, not just past it
    if target >= 3.8:   return "Over 2.5 ★★★", 3   # was 3.2 — needs real conviction
    elif target >= 2.8: return "Over 1.5 ★★", 2     # was 2.3
    elif target >= 1.8: return "Over 1.5 ★", 2      # was 1.5
    else:               return "Over 0.5", 1

def matchup_grade(pitcher_splits, batter_side):
    if batter_side not in pitcher_splits:
        return "B", "No split data — neutral assumed"
    ops = safe_float(pitcher_splits[batter_side].get("ops"), 0.750)
    if ops >= 0.900:   return "A+", f"Elite edge — {fmt_avg(ops)} OPS allowed vs {batter_side}HH"
    elif ops >= 0.850: return "A",  f"Strong edge — {fmt_avg(ops)} OPS allowed vs {batter_side}HH"
    elif ops >= 0.780: return "B",  f"Slight edge — {fmt_avg(ops)} OPS vs {batter_side}HH"
    elif ops >= 0.650: return "C",  f"Neutral — {fmt_avg(ops)} OPS vs {batter_side}HH"
    else:              return "D",  f"Tough — {batter_side}HH held to {fmt_avg(ops)} OPS"

def confidence_grade(player):
    score = 0
    recent_avg = sum(player["recent_dk"]) / len(player["recent_dk"]) if player["recent_dk"] else 0
    if recent_avg >= 4:   score += 4
    elif recent_avg >= 3: score += 3
    elif recent_avg >= 2: score += 2
    elif recent_avg >= 1: score += 1
    spot = player.get("batting_order", 0)
    if spot == 0:
        spot = 5  # Default to middle of order when lineup not confirmed
    if spot in [3, 4]:   score += 4
    elif spot in [1, 2]: score += 3
    elif spot == 5:      score += 2
    mg = player.get("matchup_grade", "C")
    if mg == "A+":   score += 4
    elif mg == "A":  score += 3
    elif mg == "B":  score += 2
    elif mg == "C":  score += 1
    h2h = player.get("h2h", {})
    if h2h and h2h.get("trusted"):
        avg = h2h.get("avg", 0)
        if avg >= 0.400:   score += 3
        elif avg >= 0.300: score += 2
        elif avg <= 0.150: score -= 2
    hr_rate = player["season_hr"] / max(player["games_played"], 1)
    # Require at least 15 games before trusting HR rate — small samples distort
    if player["games_played"] >= 15:
        if hr_rate >= 0.30:   score += 4
        elif hr_rate >= 0.20: score += 3
        elif hr_rate >= 0.10: score += 2
    else:
        score += 1  # partial credit for new/returning players
    avg_float = parse_avg(player.get("season_avg", ".000"))
    if avg_float >= 0.320:   score += 3
    elif avg_float >= 0.280: score += 2
    elif avg_float >= 0.250: score += 1
    if player["park_factor"] >= 108:   score += 2
    elif player["park_factor"] >= 103: score += 1
    if score >= 22:   return "A+", "Elite betting profile — everything lines up"
    elif score >= 19: return "A",  "Strong overall play"
    elif score >= 16: return "B+", "Good value with upside"
    elif score >= 13: return "B",  "Solid playable prop"
    elif score >= 10: return "C+", "Risky but viable — size down"
    else:             return "C",  "Low confidence — skip or avoid"


def card_remark(p):
    """
    Generate a plain-English one-liner that explains WHY this player
    is ranked where they are. Reads the actual data — not canned text.
    Shows the single most compelling reason to bet or avoid.
    """
    conf      = p.get("conf_grade", "C")
    matchup   = p.get("matchup_grade", "C")
    recent_dk = p.get("recent_dk", [])
    h2h       = p.get("h2h", {})
    h2h_avg   = h2h.get("avg", 0) if h2h else 0
    h2h_pa    = h2h.get("pa", 0)  if h2h else 0
    h2h_trust = h2h.get("trusted", False) if h2h else False
    order     = p.get("batting_order", 0)
    recent_avg = sum(recent_dk) / len(recent_dk) if recent_dk else 0
    pitcher   = p.get("opp_pitcher", "the pitcher")
    # Sanitize pitcher name — remove any chars that could break HTML
    pitcher   = pitcher.encode('ascii', 'ignore').decode('ascii').strip() or "the pitcher"
    p_last    = p.get("p_last_start", {})
    p_form    = p.get("p_form", {})
    wx        = p.get("weather", {})
    inj       = p.get("injury", {})
    season_avg = p.get("season_avg", ".000")
    hr        = p.get("season_hr", 0)
    gp        = max(p.get("games_played", 1), 1)
    hr_rate   = hr / gp

    # ── Injury — always lead with this if flagged ─────────────
    if inj.get("flagged"):
        return f"⛔ {inj['label']} — verify before placing any bet on this player."

    parts = []

    # ── Recent form — most predictive factor ─────────────────
    if recent_avg >= 4.0:
        last3 = recent_dk[-3:] if len(recent_dk) >= 3 else recent_dk
        l3avg = sum(last3) / len(last3)
        if l3avg >= 4.0:
            parts.append(f"🔥 On fire — averaging {recent_avg:.1f} DK pts over last {len(recent_dk)} games including {l3avg:.1f} in the last 3")
        else:
            parts.append(f"🔥 Hot streak — {recent_avg:.1f} DK pts/game over last {len(recent_dk)} games")
    elif recent_avg >= 2.5:
        parts.append(f"✅ Consistently producing — {recent_avg:.1f} DK pts/game recently")
    elif recent_avg < 1.0 and len(recent_dk) >= 5:
        parts.append(f"❄️ Cold bat — only {recent_avg:.1f} DK pts/game over last {len(recent_dk)} games")

    # ── Momentum divergence — warm/hot overall but cooling off ───
    # Hot/Warm badge reflects the full 10-game avg; trend arrow compares first vs second half.
    # Flag when those two signals disagree: looks good on paper but actually fading.
    if len(recent_dk) >= 6 and recent_avg >= 2.0:
        mid       = len(recent_dk) // 2
        early_avg = sum(recent_dk[:mid]) / mid
        late_avg  = sum(recent_dk[mid:]) / (len(recent_dk) - mid)
        if late_avg - early_avg <= -1.0:
            parts.append(f"⚠ Momentum fading — {late_avg:.1f} pts/game last {len(recent_dk)//2} games vs {early_avg:.1f} earlier (Warm/Hot badge reflects full 10-game window, not current direction)")

    # ── Pitcher recent form ───────────────────────────────────
    pf_trend = p_form.get("trend", "neutral")
    if pf_trend == "cold":
        parts.append(p_form.get("label", ""))
    elif pf_trend == "fatigued":
        parts.append(p_form.get("label", ""))
    elif pf_trend == "hot":
        parts.append(p_form.get("label", ""))

    # ── H2H — very specific and compelling if trusted ────────
    if h2h_trust and h2h_pa >= 10:
        if h2h_avg >= 0.400:
            parts.append(f"absolutely owns {pitcher} ({h2h.get('h',0)}-for-{h2h_pa}, {fmt_avg(h2h_avg)} career)")
        elif h2h_avg >= 0.300:
            parts.append(f"hits {pitcher} well ({fmt_avg(h2h_avg)} in {h2h_pa} career PA)")
        elif h2h_avg <= 0.150:
            parts.append(f"⚠ struggles vs {pitcher} ({fmt_avg(h2h_avg)} in {h2h_pa} PA — caution)")

    # ── Matchup grade ─────────────────────────────────────────
    if matchup == "A+":
        parts.append(f"{pitcher} is getting hit hard by {p.get('side','R')}HH batters this season")
    elif matchup == "A":
        parts.append(f"strong platoon edge vs {pitcher}")
    elif matchup == "D":
        parts.append(f"⚠ {pitcher} dominates {p.get('side','R')}HH — tough matchup")

    # ── Pitcher last start ────────────────────────────────────
    if p_last and p_last.get("er", 0) >= 4:
        parts.append(f"{pitcher} gave up {p_last['er']} ER last outing — may be vulnerable tonight")
    elif p_last and safe_float(p_last.get("ip", 9)) < 3:
        parts.append(f"{pitcher} lasted under 3 innings last start — could be short tonight")

    # ── Batting order ─────────────────────────────────────────
    if order == 1:
        parts.append("leads off = most plate appearances of anyone")
    elif order in [3, 4]:
        parts.append(f"batting {'3rd' if order==3 else 'cleanup'} = prime RBI spot")
    elif order >= 8 and p.get("lineup_confirmed"):
        parts.append("low in the order — fewer PA than normal")

    # ── Power upside ─────────────────────────────────────────
    if hr_rate >= 0.25:
        parts.append(f"elite HR pace ({hr} HR in {gp} games) — big ceiling")

    # ── Weather ──────────────────────────────────────────────
    if wx.get("wind_boost", 1.0) >= 1.15:
        parts.append(f"wind blowing out hard tonight — HR conditions")
    elif wx.get("wind_boost", 1.0) <= 0.92:
        parts.append(f"wind blowing in — suppresses power tonight")

    # ── Fallback if nothing notable ───────────────────────────
    if not parts:
        if conf in ("A+", "A"):
            parts.append(f"strong across-the-board profile — {p.get('conf_label','')}")
        elif conf == "C":
            parts.append(f"limited upside tonight — {p.get('conf_label','')}")
        else:
            parts.append(f"solid but no standout edge — {p.get('conf_label','')}")

    # Join: lead sentence + supporting details
    lead = parts[0]
    rest = "; ".join(parts[1:3]) if len(parts) > 1 else ""  # max 2 supporting points
    return f"{lead}{('. ' + rest.capitalize()) if rest else ''}."


# ============================================================
#  SCHEDULE FETCH
# ============================================================

def get_todays_games():
    print("📅 Fetching today's schedule...")
    data = api_get(
        f"{MLB_API}/schedule",
        params={"sportId": 1, "date": date.today().strftime("%Y-%m-%d"),
                "hydrate": "probablePitcher,venue,team"}
    )
    if not data or not data.get("dates"):
        return []
    games = []
    for d in data["dates"]:
        for game in d.get("games", []):
            try:
                dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
                if (dt - timedelta(hours=4)).hour >= MIN_GAME_START_HOUR:
                    games.append(game)
            except Exception:
                continue
    print(f"   ✓ Found {len(games)} games tonight.")
    return games


# ============================================================
#  HTML GENERATOR
# ============================================================

def score_bar_html(score, max_score=400):
    """Animated score progress bar."""
    pct = min(int((score / max_score) * 100), 100)
    color = "#00ff88" if pct > 60 else ("#ffaa33" if pct > 35 else "#ff6644")
    return f"""
        <div class="score-bar-wrap">
            <div class="score-bar" style="width:{pct}%;background:{color};"></div>
        </div>"""

def sparkline_html(recent_dk):
    """Mini SVG bar chart for recent game DK pts."""
    if not recent_dk:
        return "<span style='color:#555'>No data</span>"
    max_val = max(recent_dk) if recent_dk else 1
    max_val = max(max_val, 1)
    bars = ""
    w, gap = 18, 4
    total_w = len(recent_dk) * (w + gap)
    for i, val in enumerate(recent_dk):
        h    = max(4, int((val / max_val) * 44))
        x    = i * (w + gap)
        y    = 48 - h
        color = "#00ff88" if val >= 4 else ("#66aaff" if val >= 2 else "#555566")
        bars += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{color}"/>'
        bars += f'<text x="{x+9}" y="62" text-anchor="middle" fill="#aaa" font-size="9">{val}</text>'
    return f'<svg width="{total_w}" height="68" style="display:block;margin:4px 0">{bars}</svg>'

def factor_bar_html(label, value, max_val, color="#66aaff", note=""):
    """Small labeled progress bar for score breakdown."""
    pct = min(int((value / max(max_val, 1)) * 100), 100)
    return f"""
        <div class="factor-row">
            <span class="factor-label">{esc(label)}</span>
            <div class="factor-bar-wrap">
                <div class="factor-bar" style="width:{pct}%;background:{color}"></div>
            </div>
            <span class="factor-val">{value}</span>
            <span class="factor-note">{esc(note)}</span>
        </div>"""

def conf_badge(grade, label=""):
    colors = CONF_COLORS.get(grade, ("#888899", "#111122"))
    return f'<span class="badge" style="background:{colors[0]};color:{colors[1]};font-weight:700">{esc(grade)}</span>'

def match_badge(grade):
    color = MATCH_COLORS.get(grade, "#888899")
    return f'<span class="badge" style="background:{color};color:#000;font-weight:700">{esc(grade)}</span>'

def dk_stars(stars):
    return "★" * stars + "☆" * (3 - stars)


def hot_cold_badge(recent_dk):
    """
    Returns (emoji, label, bg_color, text_color) based on last 5 games.
    🔥 On Fire  = avg >= 3.5 pts  (consistently hot)
    ✅ Warm      = avg >= 2.0 pts  (producing)
    ❄️ Cold      = avg <  1.0 pts  (struggling)
    → Neutral   = everything else
    """
    if not recent_dk:
        return "—", "No data", "#1a2233", "#445566"
    avg = sum(recent_dk) / len(recent_dk)
    # Also check last 3 games specifically for recent trajectory
    last3 = recent_dk[-3:] if len(recent_dk) >= 3 else recent_dk
    last3_avg = sum(last3) / len(last3)
    if avg >= 3.5 and last3_avg >= 3.0:
        return "🔥", "On Fire",  "#1a2e00", "#7fff00"
    elif avg >= 3.5:
        return "🔥", "Hot",      "#1a2e00", "#00cc66"
    elif avg >= 2.0:
        return "✅", "Warm",     "#001a22", "#66aaff"
    elif avg < 1.0:
        return "❄️", "Cold",     "#1a0a00", "#ff6644"
    else:
        return "→",  "Neutral",  "#111828", "#778899"


def trend_arrow(recent_dk):
    """
    Returns (arrow, label, color) showing if player is trending UP or DOWN.
    Compares avg of first half vs second half of recent games.
    ↑ Trending Up   = second half avg > first half avg
    ↓ Trending Down = second half avg < first half avg
    → Steady        = flat
    """
    if not recent_dk or len(recent_dk) < 3:
        return "→", "Steady", "#778899"
    mid   = len(recent_dk) // 2
    early = recent_dk[:mid]
    late  = recent_dk[mid:]
    early_avg = sum(early) / len(early) if early else 0
    late_avg  = sum(late)  / len(late)  if late  else 0
    diff = late_avg - early_avg
    if diff >= 1.0:
        return "↑", f"+{diff:.1f} pts/game", "#00cc66"
    elif diff <= -1.0:
        return "↓", f"{diff:.1f} pts/game",  "#ff6644"
    else:
        return "→", "Steady",                "#778899"


# ============================================================
#  GITHUB PAGES DEPLOY
# ============================================================

def deploy_to_github(html_content, filename="index.html"):
    """
    Push the HTML dashboard to GitHub Pages via the API.
    File becomes live at GITHUB_PAGES_URL instantly.
    """
    if not GITHUB_TOKEN:
        print("⚠ GITHUB_TOKEN not set — skipping deploy.")
        return False

    import base64, json as _json

    api_url  = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{filename}"
    headers  = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }

    # Check if file already exists (need its SHA to update)
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"H-Bomb report {date.today()}",
        "content": base64.b64encode(html_content.encode("utf-8")).decode("utf-8"),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    # The dashboard is ~800 KB (~1 MB base64). On a slow/flaky uplink a short
    # timeout would silently drop the deploy while the tiny PWA files still
    # succeed — which stranded the live app on an old build. Longer timeout +
    # retries fix that.
    body = _json.dumps(payload)
    last_err = ""
    for attempt in range(1, 4):
        try:
            r = requests.put(api_url, headers=headers, data=body, timeout=90)
            if r.status_code in (200, 201):
                print(f"OK  Deployed to GitHub Pages: {GITHUB_PAGES_URL}")
                return True
            last_err = f"HTTP {r.status_code} {r.text[:160]}"
            print(f"   deploy attempt {attempt}/3 failed: {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"   deploy attempt {attempt}/3 error: {last_err}")
        time.sleep(3)
    print(f"FAILED to deploy index.html after 3 attempts: {last_err}")
    notify("❌ H-Bomb deploy FAILED",
           f"Could not push index.html after 3 attempts.\n{last_err[:200]}",
           tags="rotating_light", priority="high")
    return False


def verify_live_deploy():
    """Confirm the LIVE GitHub Pages site actually serves today's dashboard.

    Pushing to the repo is not the same as the site updating — a failed Pages
    build once left the live site stale for days while the log happily said
    'Deployed'. This turns that silent failure into a loud one.
    """
    today_str = date.today().strftime("%A, %B %d, %Y")
    # Pages needs a moment to build/propagate after the push (can be 1-2 min,
    # especially for an Actions-triggered build).
    for attempt in range(1, 9):
        time.sleep(20)
        try:
            r = requests.get(f"{GITHUB_PAGES_URL}?cb={int(time.time())}", timeout=20)
            if today_str in r.text:
                print(f"   ✓ Verified live site is serving today's report ({today_str}).")
                return True
        except Exception as e:
            print(f"   verify attempt {attempt}/4 error: {e}")
    # Still stale — surface the Pages build status so the cause is obvious.
    print("!! LIVE SITE NOT UPDATED — repo push succeeded but Pages is stale.")
    build_info = ""
    if GITHUB_TOKEN:
        try:
            h = {"Authorization": f"token {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github.v3+json"}
            b = requests.get(
                f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/pages/builds?per_page=1",
                headers=h, timeout=15).json()[0]
            err = (b.get("error") or {}).get("message") or "none"
            build_info = f"Pages build: {b.get('status')} | {err}"
            print(f"!! {build_info}")
        except Exception as e:
            print(f"!! Could not read Pages build status: {e}")
    notify("⚠️ H-Bomb site is STALE",
           f"Push succeeded but the live site isn't showing today's report.\n{build_info}",
           tags="warning", priority="high")
    return False


def build_pwa_files():
    """
    Generate manifest.json and service worker so the dashboard
    installs on phone home screens like a real app.
    Deploys both files to GitHub alongside the HTML.
    """
    if not GITHUB_TOKEN:
        return

    import base64, json as _json

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }

    # ── manifest.json ──────────────────────────────────────
    manifest = {
        "name":             "H-Bomb Daily Picks",
        "short_name":       "H-Bomb",
        "description":      "Daily DraftKings HRR baseball picks",
        "start_url":        "./index.html",
        "display":          "standalone",
        "background_color": "#0a0a12",
        "theme_color":      "#00ff88",
        "orientation":      "portrait",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ]
    }

    # ── service worker ─────────────────────────────────────
    sw = """
const CACHE = 'hbomb-v1';
self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(['./index.html'])));
});
self.addEventListener('fetch', e => {
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
"""

    files = {
        "manifest.json": _json.dumps(manifest, indent=2),
        "sw.js":         sw,
    }

    for fname, content in files.items():
        api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{fname}"
        sha = None
        try:
            r = requests.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception:
            pass
        payload = {
            "message": f"PWA {fname}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch":  GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        try:
            requests.put(api_url, headers=headers,
                         data=_json.dumps(payload), timeout=10)
        except Exception:
            pass


def deploy_static_asset(local_name, repo_name=None):
    """Upload a local binary file (e.g. splash GIF) to the repo ONCE.
    Skips the upload if the file is already present, so we don't re-push
    large assets on every run."""
    if not GITHUB_TOKEN:
        return
    import base64, json as _json
    repo_name = repo_name or local_name
    local_path = os.path.join(os.path.dirname(__file__), local_name)
    if not os.path.exists(local_path):
        print(f"   ⚠ {local_name} not found locally — skipping upload.")
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{repo_name}"
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            return  # already uploaded — nothing to do
    except Exception:
        pass
    try:
        with open(local_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        payload = {"message": f"Add {repo_name}", "content": content, "branch": GITHUB_BRANCH}
        r = requests.put(api_url, headers=headers, data=_json.dumps(payload), timeout=30)
        if r.status_code in (200, 201):
            print(f"   ✅ Uploaded {repo_name} to repo.")
        else:
            print(f"   ❌ Upload {repo_name} failed: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"   ❌ Upload {repo_name} error: {e}")


def build_html(all_players, games_meta, time_slates, generated_at):
    """Build the complete HTML dashboard string and JS string separately."""

    top10 = all_players[:10]

    # ── CSS ──────────────────────────────────────────────────
    css = """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: #0a0a12;
        color: #d0d0e0;
        min-height: 100vh;
    }
    a { color: #66aaff; }

    /* ── Header ── */
    .header {
        background: linear-gradient(135deg, #0d1f3c 0%, #0a1628 50%, #0d0d1a 100%);
        border-bottom: 1px solid #1a2a4a;
        padding: 28px 32px 22px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
    }
    .header-left h1 {
        font-size: 28px;
        font-weight: 800;
        color: #fff;
        letter-spacing: -0.5px;
    }
    .header-left h1 span { color: #00ff88; }
    .header-left .subtitle {
        font-size: 13px;
        color: #6677aa;
        margin-top: 4px;
    }
    .header-right {
        text-align: right;
        font-size: 12px;
        color: #445566;
    }
    .header-right .gen-time { color: #667788; }

    /* ── Layout ── */
    .container { max-width: 1300px; margin: 0 auto; padding: 28px 24px; }
    .section-title {
        font-size: 13px;
        font-weight: 700;
        color: #445577;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin: 32px 0 14px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .section-title::after {
        content: '';
        flex: 1;
        height: 1px;
        background: #1a2233;
    }

    /* ── Info cards (How Scores Work) ── */
    .info-cards-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
        gap: 8px;
        margin-bottom: 10px;
    }
    .info-card {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 10px;
        padding: 14px 10px;
        cursor: pointer;
        min-height: 58px;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        transition: border-color 0.15s, transform 0.12s;
        user-select: none;
    }
    .info-card:hover {
        border-color: #00ff8844;
        transform: translateY(-2px);
    }
    .info-card-front {
        font-size: 12px;
        font-weight: 700;
        color: #aabbcc;
        line-height: 1.4;
        pointer-events: none;
    }

    /* ── Info popup modal ── */
    .info-popup-backdrop {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,0.75);
        z-index: 2000;
        align-items: center;
        justify-content: center;
        padding: 24px 16px;
        backdrop-filter: blur(3px);
    }
    .info-popup-backdrop.open { display: flex; }
    .info-popup {
        background: #0a0f1c;
        border: 1px solid #00ff8833;
        border-radius: 14px;
        padding: 24px;
        max-width: 400px;
        width: 100%;
        position: relative;
        animation: modalIn 0.16s ease;
    }
    .info-popup-title {
        font-size: 16px;
        font-weight: 700;
        color: #00ff88;
        margin-bottom: 12px;
    }
    .info-popup-body {
        font-size: 13px;
        color: #8899bb;
        line-height: 1.8;
    }
    .info-popup-close {
        position: absolute;
        top: 12px;
        right: 14px;
        background: #1a2a40;
        border: none;
        color: #aabbcc;
        font-size: 18px;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.12s;
    }
    .info-popup-close:hover { background: #ff4444; color: #fff; }

    /* ── Tonight's games strip ── */
    .games-strip {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
        margin-bottom: 8px;
    }
    .game-chip {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 10px;
        padding: 14px 16px;
    }
    .game-chip .matchup {
        font-size: 15px;
        font-weight: 700;
        color: #fff;
        margin-bottom: 5px;
    }
    .game-chip .game-meta {
        font-size: 11px;
        color: #556688;
        line-height: 1.8;
    }
    .game-chip .park-pill {
        display: inline-block;
        font-size: 10px;
        padding: 2px 8px;
        border-radius: 20px;
        margin-top: 6px;
        font-weight: 600;
    }
    .lineup-pill {
        display: inline-block;
        font-size: 10px;
        padding: 2px 9px;
        border-radius: 20px;
        margin-top: 4px;
        font-weight: 600;
    }
    .confirmed { background: #003322; color: #00ff88; }
    .pending   { background: #221100; color: #ffaa33; }

    /* ── score bar (shared) ── */
    .score-bar-wrap {
        height: 5px;
        background: #111828;
        border-radius: 3px;
        margin: 5px 0 2px;
        overflow: hidden;
        width: 64px;
    }
    .score-bar { height: 100%; border-radius: 3px; transition: width 0.5s ease; }

    /* ── badge (shared) ── */
    .badge {
        display: inline-block;
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 20px;
        font-weight: 600;
    }
    /* Labeled badge — stacks the grade + tiny label underneath */
    .badge-labeled {
        display: inline-flex;
        flex-direction: column;
        align-items: center;
        gap: 1px;
    }
    .badge-lbl {
        font-size: 8px;
        color: #445566;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        font-weight: 600;
        line-height: 1;
    }

    /* ── Factor bars (shared) ── */
    .factor-row {
        display: grid;
        grid-template-columns: 130px 1fr 45px 1fr;
        gap: 6px;
        align-items: center;
        margin-bottom: 5px;
    }
    .factor-label    { font-size: 11px; color: #667788; }
    .factor-bar-wrap { height: 5px; background: #111828; border-radius: 3px; overflow: hidden; }
    .factor-bar      { height: 100%; border-radius: 3px; }
    .factor-val      { font-size: 11px; color: #aabbcc; text-align: right; }
    .factor-note     { font-size: 10px; color: #445566; }

    /* ── Unified Top 10 full cards ── */
    .top10-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 14px;
    }
    .fc-card {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 12px;
        overflow: hidden;
        transition: transform 0.15s, box-shadow 0.15s;
    }
    .fc-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 28px rgba(0,0,0,0.4);
    }
    .fc-header {
        padding: 14px 16px 12px;
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        border-bottom: 1px solid #111828;
        gap: 8px;
    }
    .fc-rank     { font-size: 22px; font-weight: 900; line-height: 1; flex-shrink: 0; }
    .fc-name     { font-size: 16px; font-weight: 700; color: #fff; }
    .fc-sub      { font-size: 11px; color: #445566; margin-top: 2px; }
    .fc-badges   { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; }
    .fc-score-block { text-align: right; flex-shrink: 0; }
    .fc-score-num   { font-size: 26px; font-weight: 800; line-height: 1; }
    .fc-score-lbl   { font-size: 9px; color: #445566; text-transform: uppercase;
                      letter-spacing: 0.06em; margin-top: 1px; }
    .fc-body { padding: 13px 16px; }
    .fc-stat-row {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 7px;
        margin-bottom: 12px;
    }
    .fc-stat { background: #0a1020; border-radius: 7px; padding: 7px 5px; text-align: center; }
    .fc-sv   { font-size: 14px; font-weight: 700; color: #ddeeff; }
    .fc-sl   { font-size: 9px; color: #445566; text-transform: uppercase;
               letter-spacing: 0.04em; margin-top: 2px; }
    .fc-section-lbl {
        font-size: 9px; color: #334455; text-transform: uppercase;
        letter-spacing: 0.07em; margin-bottom: 5px; margin-top: 10px;
    }
    .fc-info-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-top: 10px;
    }
    .fc-info-box { background: #0a1020; border-radius: 7px; padding: 9px 11px; }
    .fc-ib-label { font-size: 9px; color: #445566; text-transform: uppercase;
                   letter-spacing: 0.06em; margin-bottom: 3px; }
    .fc-ib-val   { font-size: 12px; color: #ccd; font-weight: 600; }
    .fc-ib-sub   { font-size: 10px; color: #445566; margin-top: 2px; }
    .fc-dk-rec {
        background: #060e1a;
        border: 1px solid #1a3355;
        border-radius: 7px;
        padding: 9px 13px;
        margin-top: 10px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .fc-dk-label { font-size: 9px; color: #445566; text-transform: uppercase; letter-spacing: 0.06em; }
    .fc-dk-val   { font-size: 14px; font-weight: 700; color: #00ff88; }
    .fc-dk-range { font-size: 12px; color: #66aaff; }

    /* ── Time slates ── */
    .slate-section {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 12px;
        overflow: hidden;
        margin-bottom: 14px;
    }
    .slate-header {
        background: #080f1c;
        padding: 10px 16px;
        font-size: 13px;
        font-weight: 700;
        color: #66aaff;
        border-bottom: 1px solid #1a2a40;
    }
    .slate-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
    }
    .slate-table th {
        padding: 7px 14px;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #334455;
        text-align: left;
        background: #060d18;
    }
    .slate-table td {
        padding: 9px 14px;
        border-top: 1px solid #0e1828;
        color: #aabbcc;
    }
    .slate-table tr:hover td { background: #0d1828; }

    /* ── Filter bar ── */
    .filter-bar {
        background: #080f1c;
        border: 1px solid #1a2a40;
        border-radius: 12px;
        padding: 14px 18px;
        margin-bottom: 18px;
        display: flex;
        flex-wrap: wrap;
        gap: 14px;
        align-items: flex-end;
    }
    .filter-group { display: flex; flex-direction: column; gap: 5px; }
    .filter-label {
        font-size: 10px;
        color: #445566;
        text-transform: uppercase;
        letter-spacing: 0.07em;
    }
    .filter-select, .filter-range {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 7px;
        color: #ddeeff;
        padding: 6px 10px;
        font-size: 13px;
        font-family: inherit;
        outline: none;
        cursor: pointer;
        min-width: 130px;
    }
    .filter-select:focus { border-color: #00ff88; }
    .filter-range { padding: 4px 8px; accent-color: #00ff88; }
    .filter-btn {
        background: #00ff88;
        color: #001a0d;
        border: none;
        border-radius: 7px;
        padding: 7px 18px;
        font-size: 13px;
        font-weight: 700;
        cursor: pointer;
        font-family: inherit;
        align-self: flex-end;
    }
    .filter-btn:hover { background: #00cc66; }
    .count-label {
        font-size: 12px;
        color: #445566;
        align-self: flex-end;
        padding-bottom: 7px;
    }
    .hidden { display: none !important; }

    /* ── Player headshot ── */
    .fc-headshot {
        width: 72px;
        height: 72px;
        border-radius: 50%;
        object-fit: cover;
        object-position: center 20%;
        border: 2px solid #1a2a40;
        flex-shrink: 0;
        background: #0a1020;
        display: block;
    }
    .fc-headshot-wrap {
        position: relative;
        flex-shrink: 0;
        width: 72px;
        height: 72px;
    }
    .fc-rank-badge {
        position: absolute;
        bottom: -4px;
        right: -6px;
        font-size: 13px;
        font-weight: 900;
        background: #0a0a12;
        border: 1px solid #1a2a40;
        border-radius: 20px;
        padding: 1px 5px;
        line-height: 18px;
        white-space: nowrap;
    }

    /* ── Math projection breakdown ── */
    .fc-math-breakdown {
        background: #060d18;
        border: 1px solid #1a3355;
        border-radius: 7px;
        padding: 9px 12px;
        margin-top: 8px;
    }
    .fc-math-title {
        font-size: 10px;
        color: #445566;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        margin-bottom: 7px;
    }
    .fc-math-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 6px;
    }
    .fc-math-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        background: #0a1020;
        border-radius: 5px;
        padding: 5px 4px;
    }
    .fc-math-lbl { font-size: 9px; color: #445566; text-transform: uppercase;
                   letter-spacing: 0.04em; margin-bottom: 2px; }
    .fc-math-val { font-size: 13px; font-weight: 600; color: #aabbcc; }

    /* ── Card remark / scout note ── */
    .fc-remark {
        font-size: 11.5px;
        color: #8899bb;
        margin-top: 6px;
        line-height: 1.5;
        font-style: italic;
        padding: 5px 8px;
        background: #080e1a;
        border-left: 2px solid #1a2a40;
        border-radius: 0 5px 5px 0;
    }

    /* ── H2H card rows ── */
    .h2h-row {
        display: grid;
        grid-template-columns: 58px 1fr 1fr 1fr;
        gap: 4px;
        align-items: center;
        padding: 4px 0;
        border-bottom: 1px solid #111828;
        font-size: 11px;
    }
    .h2h-row:last-child { border-bottom: none; }
    .h2h-label { color: #445566; font-size: 10px; text-transform: uppercase;
                 letter-spacing: 0.05em; font-weight: 600; }
    .h2h-stat  { color: #aabbcc; }
    .h2h-trend { font-weight: 600; }
    .h2h-empty { color: #334455; font-style: italic; font-size: 11px;
                 grid-column: 2 / -1; }

    /* ── Pick box on each card ── */
    .pick-box {
        margin-top: 10px;
        background: #050c18;
        border: 1px solid #1a3355;
        border-radius: 8px;
        padding: 10px 12px;
    }
    .pick-box-title {
        font-size: 10px;
        color: #445566;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        margin-bottom: 8px;
        font-weight: 600;
    }
    .pick-box-row {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
    }
    .pick-name-input {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 6px;
        color: #ddeeff;
        font-size: 12px;
        padding: 5px 9px;
        font-family: inherit;
        width: 110px;
        outline: none;
    }
    .pick-name-input:focus { border-color: #00ff88; }
    .pick-name-input::placeholder { color: #334455; }
    .pick-line-select {
        background: #0d1525;
        border: 1px solid #1a2a40;
        border-radius: 6px;
        color: #ddeeff;
        font-size: 12px;
        padding: 5px 9px;
        font-family: inherit;
        outline: none;
        cursor: pointer;
    }
    .pick-line-select:focus { border-color: #00ff88; }
    .pick-submit-btn {
        background: #003322;
        border: 1px solid #00ff8844;
        border-radius: 6px;
        color: #00ff88;
        font-size: 12px;
        font-weight: 700;
        padding: 5px 14px;
        cursor: pointer;
        font-family: inherit;
        transition: background .12s;
        white-space: nowrap;
    }
    .pick-submit-btn:hover { background: #004433; }
    .pick-submitted {
        display: none;
        font-size: 12px;
        color: #00cc66;
        font-weight: 600;
        padding: 4px 0;
    }
    .pick-submitted.show { display: block; }

    /* ── Tracker modal ── */
    .tracker-summary {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
        gap: 10px;
        margin-bottom: 16px;
    }
    .tracker-stat {
        background: #0d1525;
        border-radius: 8px;
        padding: 10px 12px;
        text-align: center;
    }
    .tracker-stat .ts-val {
        font-size: 24px;
        font-weight: 800;
        line-height: 1;
    }
    .tracker-stat .ts-lbl {
        font-size: 10px;
        color: #445566;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-top: 4px;
    }
    .tracker-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
    }
    .tracker-table th {
        text-align: left;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #334455;
        padding: 6px 10px;
        border-bottom: 1px solid #0e1828;
    }
    .tracker-table td {
        padding: 8px 10px;
        border-bottom: 1px solid #0a1020;
        color: #aabbcc;
        vertical-align: middle;
    }
    .tracker-table tr:last-child td { border-bottom: none; }
    .result-btn {
        font-size: 11px;
        padding: 3px 10px;
        border-radius: 20px;
        border: 1px solid;
        cursor: pointer;
        font-family: inherit;
        font-weight: 600;
        background: transparent;
        margin-right: 4px;
        transition: all .12s;
    }
    .result-btn.hit  { border-color: #00cc66; color: #00cc66; }
    .result-btn.hit:hover, .result-btn.hit.active  { background: #003322; }
    .result-btn.miss { border-color: #ff4444; color: #ff4444; }
    .result-btn.miss:hover, .result-btn.miss.active { background: #220000; }
    .result-chip { font-size: 11px; font-weight: 700; }
    .result-chip.hit  { color: #00ff88; }
    .result-chip.miss { color: #ff4444; }
    .result-chip.pending { color: #445566; }

    /* Floating tracker button */
    .tracker-fab {
        position: fixed;
        bottom: 24px;
        right: 24px;
        background: #003322;
        border: 1px solid #00ff88;
        border-radius: 50px;
        color: #00ff88;
        font-size: 13px;
        font-weight: 700;
        padding: 10px 20px;
        cursor: pointer;
        font-family: inherit;
        box-shadow: 0 4px 20px rgba(0,255,136,0.2);
        z-index: 999;
        transition: transform .12s, box-shadow .12s;
        display: flex;
        align-items: center;
        gap: 7px;
    }
    .tracker-fab:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 28px rgba(0,255,136,0.3);
    }
    .tracker-fab .fab-count {
        background: #00ff88;
        color: #001a0d;
        border-radius: 20px;
        font-size: 11px;
        font-weight: 800;
        padding: 1px 7px;
        min-width: 20px;
        text-align: center;
    }
    .leaderboard-row {
        display: flex;
        align-items: center;
        padding: 10px 0;
        border-bottom: 1px solid #0e1828;
        gap: 12px;
    }
    .leaderboard-row:last-child { border-bottom: none; }
    .lb-medal { font-size: 20px; min-width: 28px; }
    .lb-name  { flex: 1; font-size: 14px; font-weight: 700; color: #ddeeff; }
    .lb-stats { text-align: right; font-size: 12px; color: #667788; }
    .lb-rate  { font-size: 18px; font-weight: 800; }

    /* ── Picks tracker callout ── */
    .picks-callout {
        background: #060e1a;
        border: 1px solid #00ff8822;
        border-left: 3px solid #00ff88;
        border-radius: 10px;
        padding: 13px 16px;
        margin-bottom: 10px;
    }
    .picks-callout-text {
        font-size: 13px;
        color: #8899bb;
        line-height: 1.7;
    }
    .picks-callout-text b { color: #ddeeff; }
    .picks-callout-btn {
        display: inline-block;
        background: #003322;
        border: 1px solid #00ff8844;
        border-radius: 6px;
        color: #00ff88;
        font-size: 12px;
        font-weight: 700;
        padding: 2px 10px;
        vertical-align: middle;
        white-space: nowrap;
    }

    /* ── Footer ── */
    .footer {
        text-align: center;
        padding: 28px;
        font-size: 11px;
        color: #334455;
        border-top: 1px solid #111820;
        margin-top: 40px;
    }

    /* ── Modal system ── */
    .modal-backdrop {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,0.82);
        z-index: 1000;
        align-items: flex-start;
        justify-content: center;
        padding: 24px 16px;
        overflow-y: auto;
        backdrop-filter: blur(3px);
    }
    .modal-backdrop.modal-open {
        display: flex;
    }
    .modal-box {
        background: #0a0f1c;
        border: 1px solid #1a2a40;
        border-radius: 16px;
        width: 100%;
        max-width: 1200px;
        max-height: 90vh;
        overflow-y: auto;
        animation: modalIn 0.18s ease;
        position: relative;
    }
    @keyframes modalIn {
        from { opacity: 0; transform: translateY(18px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    .modal-header {
        background: #080f1c;
        padding: 16px 20px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid #1a2a40;
        position: sticky;
        top: 0;
        z-index: 10;
    }
    .modal-title {
        font-size: 16px;
        font-weight: 700;
        color: #fff;
    }
    .modal-title span { color: #00ff88; }
    .modal-meta {
        font-size: 11px;
        color: #445566;
        margin-top: 3px;
    }
    .modal-close {
        background: #1a2a40;
        border: none;
        color: #aabbcc;
        font-size: 20px;
        line-height: 1;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition: background 0.15s;
    }
    .modal-close:hover { background: #ff4444; color: #fff; }
    .modal-body {
        padding: 16px;
    }
    .modal-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 14px;
    }

    /* ── Clickable game chip ── */
    .game-chip {
        cursor: pointer;
        transition: transform 0.12s, box-shadow 0.12s, border-color 0.12s;
    }
    .game-chip:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 24px rgba(0,200,100,0.12);
        border-color: #00ff8844;
    }
    .chip-click-hint {
        font-size: 10px;
        color: #00ff8866;
        margin-top: 6px;
        font-weight: 600;
        letter-spacing: 0.05em;
    }

    /* ── Top 10 button chip ── */
    .top10-chip {
        background: linear-gradient(135deg, #0d1f3c, #0a1628);
        border: 1px solid #00ff8844;
        border-radius: 10px;
        padding: 14px 20px;
        cursor: pointer;
        transition: transform 0.12s, box-shadow 0.12s, border-color 0.12s;
        display: flex;
        align-items: center;
        gap: 14px;
    }
    .top10-chip:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 28px rgba(0,255,136,0.15);
        border-color: #00ff88aa;
    }
    .top10-chip-icon { font-size: 28px; line-height: 1; }
    .top10-chip-label { font-size: 16px; font-weight: 800; color: #00ff88; }
    .top10-chip-sub   { font-size: 11px; color: #445566; margin-top: 2px; }

    /* ── Theme toggle button ── */
    .theme-toggle {
        background: #1a2a40; border: 1px solid #334455; border-radius: 20px;
        color: #aabbcc; font-size: 16px; padding: 6px 14px; cursor: pointer;
        font-family: inherit; transition: all .15s; white-space: nowrap;
    }
    .theme-toggle:hover { border-color: #00ff88; color: #00ff88; }

    /* ════════════════════════════════════════════════════════
       LIGHT MODE OVERRIDES  (body.light prefix on everything)
       ════════════════════════════════════════════════════════ */
    body.light { background: #f0f4f8; color: #2a3a50; }
    body.light a { color: #2255cc; }

    /* Header */
    body.light .header { background: linear-gradient(135deg,#1a3a6c 0%,#1e3d70 50%,#162d55 100%); border-bottom-color: #2a4a80; }
    body.light .header-left h1 { color: #fff; }
    body.light .header-left .subtitle { color: #aabbdd; }
    body.light .header-right { color: #aabbcc; }
    body.light .header-right .gen-time { color: #ccd8ee; }
    body.light .theme-toggle { background: rgba(255,255,255,0.15); border-color: rgba(255,255,255,0.3); color: #ddeeff; }
    body.light .theme-toggle:hover { border-color: #00ff88; color: #00ff88; }

    /* Layout */
    body.light .section-title { color: #3a5a88; }
    body.light .section-title::after { background: #ccd8ea; }
    body.light .footer { color: #889aac; border-top-color: #ccd8ea; }

    /* Cards */
    body.light .game-chip { background: #fff; border-color: #ccd8ea; }
    body.light .game-chip .matchup { color: #1a2a40; }
    body.light .game-chip .game-meta { color: #667788; }
    body.light .fc-card { background: #fff; border-color: #ccd8ea; box-shadow: 0 2px 8px rgba(0,0,0,0.07); }
    body.light .fc-card:hover { box-shadow: 0 8px 24px rgba(0,0,0,0.12); }
    body.light .fc-header { border-bottom-color: #e0e8f2; }
    body.light .fc-name { color: #1a2a40; }
    body.light .fc-sub { color: #667788; }
    body.light .fc-score-lbl { color: #889aac; }
    body.light .fc-stat { background: #edf2f8; }
    body.light .fc-sv { color: #1a2a40; }
    body.light .fc-sl { color: #889aac; }
    body.light .fc-section-lbl { color: #889aac; }
    body.light .fc-info-box { background: #edf2f8; }
    body.light .fc-ib-label { color: #889aac; }
    body.light .fc-ib-val { color: #2a3a50; }
    body.light .fc-ib-sub { color: #778899; }
    body.light .fc-dk-rec { background: #edf8f2; border-color: #b0ddc8; }
    body.light .fc-dk-label { color: #778899; }
    body.light .fc-dk-val { color: #009944; }
    body.light .fc-dk-range { color: #2255cc; }
    body.light .fc-math-breakdown { background: #edf2f8; border-color: #ccd8ea; }
    body.light .fc-math-title { color: #889aac; }
    body.light .fc-math-item { background: #dde8f4; }
    body.light .fc-math-lbl { color: #889aac; }
    body.light .fc-math-val { color: #2a3a50; }
    body.light .fc-remark { background: #f5f8fc; border-left-color: #ccd8ea; color: #556677; }
    body.light .fc-rank-badge { background: #f0f4f8; border-color: #ccd8ea; }
    body.light .score-bar-wrap { background: #dde6f0; }
    body.light .factor-bar-wrap { background: #dde6f0; }
    body.light .factor-label { color: #778899; }
    body.light .factor-val { color: #445566; }
    body.light .factor-note { color: #889aac; }

    /* Slate tables */
    body.light .slate-section { background: #fff; border-color: #ccd8ea; }
    body.light .slate-header { background: #f0f4f8; border-bottom-color: #ccd8ea; color: #2255cc; }
    body.light .slate-table th { background: #e8eef6; color: #778899; }
    body.light .slate-table td { color: #445566; border-top-color: #e8eef6; }
    body.light .slate-table tr:hover td { background: #f5f8fc; }

    /* Filter bar */
    body.light .filter-bar { background: #f5f8fc; border-color: #ccd8ea; }
    body.light .filter-label { color: #778899; }
    body.light .filter-select, body.light .filter-range { background: #fff; border-color: #ccd8ea; color: #2a3a50; }
    body.light .count-label { color: #778899; }

    /* Info cards */
    body.light .info-card { background: #fff; border-color: #ccd8ea; }
    body.light .info-card:hover { border-color: #009944; }
    body.light .info-card-front { color: #445566; }
    body.light .info-popup { background: #fff; border-color: #b0ddc8; }
    body.light .info-popup-title { color: #009944; }
    body.light .info-popup-body { color: #445566; }
    body.light .info-popup-close { background: #edf2f8; color: #445566; }

    /* Confirmed / pending pills */
    body.light .confirmed { background: #d4f5e5; color: #006633; }
    body.light .pending   { background: #fff3d4; color: #885500; }

    /* Pick boxes */
    body.light .pick-box { background: #edf8f2; border-color: #b0ddc8; }
    body.light .pick-box-title { color: #778899; }
    body.light .pick-name-input { background: #fff; border-color: #ccd8ea; color: #1a2a40; }
    body.light .pick-name-input::placeholder { color: #aabbcc; }
    body.light .pick-line-select { background: #fff; border-color: #ccd8ea; color: #1a2a40; }
    body.light .pick-submit-btn { background: #d4f5e5; border-color: #009944; color: #006633; }
    body.light .pick-submit-btn:hover { background: #c0f0d8; }

    /* Modals */
    body.light .modal-backdrop { background: rgba(10,20,40,0.7); }
    body.light .modal-box { background: #fff; border-color: #ccd8ea; }
    body.light .modal-header { background: #f5f8fc; border-bottom-color: #ccd8ea; }
    body.light .modal-title { color: #1a2a40; }
    body.light .modal-title span { color: #009944; }
    body.light .modal-meta { color: #778899; }
    body.light .modal-close { background: #e8eef6; color: #445566; }

    /* Tracker */
    body.light .tracker-stat { background: #edf2f8; }
    body.light .tracker-stat .ts-lbl { color: #778899; }
    body.light .tracker-table th { color: #778899; border-bottom-color: #dde6f0; }
    body.light .tracker-table td { color: #445566; border-bottom-color: #edf2f8; }
    body.light .result-btn.hit  { border-color: #009944; color: #009944; }
    body.light .result-btn.hit:hover, body.light .result-btn.hit.active  { background: #d4f5e5; }
    body.light .result-btn.miss { border-color: #cc2222; color: #cc2222; }
    body.light .result-btn.miss:hover, body.light .result-btn.miss.active { background: #ffebeb; }
    body.light .leaderboard-row { border-bottom-color: #e8eef6; }
    body.light .lb-name  { color: #1a2a40; }
    body.light .lb-stats { color: #778899; }
    body.light .picks-callout { background: #edf8f2; border-color: #b0ddc8; border-left-color: #009944; }
    body.light .picks-callout-text { color: #445566; }
    body.light .picks-callout-text b { color: #1a2a40; }
    body.light .picks-callout-btn { background: #d4f5e5; color: #006633; border-color: #009944; }

    /* H2H rows */
    body.light .h2h-row { border-bottom-color: #e8eef6; }
    body.light .h2h-label { color: #778899; }
    body.light .h2h-stat  { color: #445566; }
    body.light .h2h-empty { color: #aabbcc; }

    /* FAB */
    body.light .tracker-fab { background: #d4f5e5; border-color: #009944; color: #006633; box-shadow: 0 4px 20px rgba(0,153,68,0.2); }
    body.light .tracker-fab .fab-count { background: #009944; color: #fff; }

    /* Badge labels */
    body.light .badge-lbl { color: #889aac; }
    body.light .top10-chip { background: linear-gradient(135deg,#1a3a6c,#1e3d70); }
    body.light .top10-chip-label { color: #00ff88; }
    body.light .top10-chip-sub { color: #aabbdd; }
    """

    js = """
    // ── Theme toggle ──────────────────────────────────────────
    function toggleTheme() {
        const light = document.body.classList.toggle('light');
        const btn = document.getElementById('theme-btn');
        if (btn) btn.textContent = light ? '🌙 Dark' : '☀️ Light';
        localStorage.setItem('hbomb-theme', light ? 'light' : 'dark');
    }
    (function() {
        if (localStorage.getItem('hbomb-theme') === 'light') {
            document.body.classList.add('light');
            const btn = document.getElementById('theme-btn');
            if (btn) btn.textContent = '🌙 Dark';
        }
    })();

    // ── All modal + popup functions defined first ─────────────

    function openModal(id) {
        const modal = document.getElementById(id);
        if (!modal) { console.warn('Modal not found:', id); return; }
        modal.classList.add('modal-open');
        document.body.style.overflow = 'hidden';
        modal.scrollTop = 0;
        if (id === 'modal-tracker') { try { refreshTrackerModal(); } catch(e) { console.warn(e); } }
    }
    function closeModal(id) {
        const modal = document.getElementById(id);
        if (!modal) return;
        modal.classList.remove('modal-open');
        document.body.style.overflow = '';
    }
    function showInfo(card) {
        const title = card.querySelector('.info-card-front').textContent.trim();
        const body  = card.querySelector('.info-card-data').textContent.trim();
        document.getElementById('info-popup-title').textContent = title;
        document.getElementById('info-popup-body').innerHTML = body.replace(/\\n/g, '<br>');
        document.getElementById('info-popup-backdrop').classList.add('open');
        document.body.style.overflow = 'hidden';
    }
    function closeInfo(e) {
        if (e.target === document.getElementById('info-popup-backdrop')) {
            document.getElementById('info-popup-backdrop').classList.remove('open');
            document.body.style.overflow = '';
        }
    }
    function closeInfoBtn() {
        document.getElementById('info-popup-backdrop').classList.remove('open');
        document.body.style.overflow = '';
    }

    // ── Global event handlers ─────────────────────────────────
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        closeInfoBtn();
        document.querySelectorAll('.modal-backdrop.modal-open').forEach(m => {
            m.classList.remove('modal-open');
        });
        document.body.style.overflow = '';
    });
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal-backdrop')) {
            e.target.classList.remove('modal-open');
            document.body.style.overflow = '';
        }
    });

    if ('serviceWorker' in navigator) { navigator.serviceWorker.register('./sw.js'); }

    // ── Supabase config ───────────────────────────────────────
    const SB_URL = 'https://hpoxotxejiilxzhxiuan.supabase.co';
    const SB_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhwb3hvdHhlamlpbHh6aHhpdWFuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0Nzg2MzgsImV4cCI6MjA5NTA1NDYzOH0.57oQLnh3Wv8n1F34OVsNvFdsklVktbKUeTlGDkq1X7s';
    const TODAY  = new Date().toISOString().slice(0, 10);

    async function sbFetch(path, opts = {}) {
        try {
            const res = await fetch(SB_URL + '/rest/v1/' + path, {
                headers: {
                    'apikey':        SB_KEY,
                    'Authorization': 'Bearer ' + SB_KEY,
                    'Content-Type':  'application/json',
                    'Prefer':        opts.prefer || 'return=representation',
                },
                method: opts.method || 'GET',
                body:   opts.body   || undefined,
            });
            if (!res.ok) { console.error('Supabase', res.status, await res.text()); return null; }
            const text = await res.text();
            return text ? JSON.parse(text) : [];
        } catch(e) { console.error('Supabase fetch error', e); return null; }
    }

    async function logPickBtn(btn) {
        const pid    = btn.dataset.pid;
        const pname  = btn.dataset.pname;
        const conf   = btn.dataset.conf;
        const match  = btn.dataset.match;
        const score  = parseFloat(btn.dataset.score);
        const suffix = btn.dataset.suffix;
        await logPick(pid, pname, conf, match, score, suffix);
    }

    async function logPick(playerId, playerName, conf, matchup, score, suffix) {
        const nameEl = document.getElementById('pn-' + playerId + '-' + suffix);
        const lineEl = document.getElementById('pl-' + playerId + '-' + suffix);
        const msgEl  = document.getElementById('ps-' + playerId + '-' + suffix);
        const who    = nameEl ? nameEl.value.trim() : '';
        const line   = lineEl ? lineEl.value : 'Over 1.5';
        if (!who) {
            if (nameEl) { nameEl.style.borderColor = '#ff4444'; nameEl.focus(); }
            return;
        }
        if (nameEl) nameEl.style.borderColor = '';
        const existing = await sbFetch(
            'picks?who=eq.' + encodeURIComponent(who) +
            '&player_id=eq.' + encodeURIComponent(playerId) +
            '&date=eq.' + TODAY
        );
        if (existing && existing.length > 0) {
            if (msgEl) { msgEl.textContent = 'Already logged for ' + who + ' today!'; msgEl.classList.add('show'); }
            return;
        }
        const row = {
            date: TODAY, who, player_id: playerId, player_name: playerName,
            line, conf, matchup, score, result: 'pending', actual: null
        };
        const res = await sbFetch('picks', { method: 'POST', body: JSON.stringify(row) });
        if (!res) {
            if (msgEl) { msgEl.style.color = '#ff4444'; msgEl.textContent = 'Save failed — check console.'; msgEl.classList.add('show'); }
            return;
        }
        if (msgEl) { msgEl.style.color = '#00cc66'; msgEl.textContent = '✓ Logged: ' + who + ' on ' + playerName + ' ' + line; msgEl.classList.add('show'); }
        updateFabCount();
        refreshTrackerModal();
    }

    async function setResult(pickId, result) {
        await sbFetch('picks?id=eq.' + pickId, { method: 'PATCH', body: JSON.stringify({ result }), prefer: 'return=minimal' });
        refreshTrackerModal();
    }

    async function setActual(pickId, val) {
        await sbFetch('picks?id=eq.' + pickId, {
            method: 'PATCH',
            body:   JSON.stringify({ actual: val === '' ? null : parseInt(val) }),
            prefer: 'return=minimal'
        });
    }

    async function voidPick(id) {
        await sbFetch('picks?id=eq.' + id, {
            method: 'PATCH',
            body:   JSON.stringify({ result: 'void' }),
            prefer: 'return=minimal'
        });
        refreshTrackerModal();
    }

    async function deletePick(id) {
        if (!confirm('Delete this pick permanently?')) return;
        await sbFetch('picks?id=eq.' + id, { method: 'DELETE', prefer: 'return=minimal' });
        refreshTrackerModal();
        updateFabCount();
    }

    async function updateFabCount() {
        const picks = await sbFetch('picks?date=eq.' + TODAY + '&select=id');
        const el = document.getElementById('fab-count');
        if (el) el.textContent = picks ? picks.length : 0;
    }

    async function refreshTrackerModal() {
        const todayPicks = await sbFetch('picks?date=eq.' + TODAY + '&order=id.asc') || [];
        const allPicks   = await sbFetch('picks?order=date.desc,id.desc') || [];
        const realPicks  = allPicks.filter(p => p.result !== 'void');
        const settled    = realPicks.filter(p => p.result !== 'pending');
        const hits       = settled.filter(p => p.result === 'hit');
        const hitRate    = settled.length ? Math.round((hits.length / settled.length) * 100) : null;

        document.getElementById('tr-total').textContent = realPicks.length;
        document.getElementById('tr-today').textContent = todayPicks.filter(p => p.result !== 'void').length;
        const rEl = document.getElementById('tr-rate');
        rEl.textContent = hitRate !== null ? hitRate + '%' : '—';
        rEl.style.color = hitRate >= 55 ? '#00ff88' : hitRate !== null && hitRate < 45 ? '#ff4444' : '#aabbcc';
        document.getElementById('tr-hits').textContent = hits.length;
        document.getElementById('tr-miss').textContent = settled.length - hits.length;

        // ── Today's picks table ──────────────────────────────
        const tbody = document.getElementById('tr-picks-body');
        if (!todayPicks.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#334455;padding:20px;font-style:italic">No picks logged today yet.</td></tr>';
        } else {
            tbody.innerHTML = todayPicks.map(p => {
                const isVoid = p.result === 'void';
                const rHit   = p.result === 'hit'  ? 'active' : '';
                const rMiss  = p.result === 'miss' ? 'active' : '';
                let actions;
                if (isVoid) {
                    actions = '<span style="color:#556677;font-size:11px;font-style:italic">Voided/DNP</span>';
                } else {
                    actions =
                        '<button class="result-btn hit '  + rHit  + '" data-id="' + p.id + '" data-r="hit"  onclick="setResult(this.dataset.id,this.dataset.r)">Hit</button>' +
                        '<button class="result-btn miss ' + rMiss + '" data-id="' + p.id + '" data-r="miss" onclick="setResult(this.dataset.id,this.dataset.r)">Miss</button>' +
                        '<button style="background:none;border:none;color:#ff4444;cursor:pointer;padding:0 5px;font-size:15px;line-height:1;opacity:0.5;margin-left:4px" data-id="' + p.id + '" onclick="deletePick(this.dataset.id)" title="Delete">✕</button>';
                }
                return '<tr' + (isVoid ? ' style="opacity:0.35"' : '') + '>' +
                    '<td style="font-weight:700;color:#ddeeff">' + p.who + '</td>' +
                    '<td>' + (p.player_name || '—') + '</td>' +
                    '<td style="white-space:nowrap">' + p.line + '</td>' +
                    '<td><input type="number" min="0" max="10" placeholder="H+R+RBI" ' +
                    'value="' + (p.actual !== null && p.actual !== undefined ? p.actual : '') + '" ' +
                    'onchange="setActual(' + p.id + ', this.value)" ' +
                    (isVoid ? 'disabled ' : '') +
                    'style="width:80px;background:#0d1525;border:1px solid #1a2a40;border-radius:5px;color:#ddeeff;font-size:12px;padding:3px 6px;font-family:inherit"></td>' +
                    '<td style="white-space:nowrap">' + actions + '</td></tr>';
            }).join('');
        }

        // ── Past picks — grouped by date, then by who ─────────
        const pastPicks = allPicks.filter(p => p.date < TODAY);
        const pastDiv   = document.getElementById('tr-past-picks');
        if (!pastPicks.length) {
            pastDiv.innerHTML = '<div style="color:#334455;font-style:italic;font-size:12px;padding:8px 0">No past picks yet.</div>';
        } else {
            const pastDates = [...new Set(pastPicks.map(p => p.date))].sort().reverse();
            pastDiv.innerHTML = pastDates.map(d => {
                const dPicks  = pastPicks.filter(p => p.date === d);
                const nHits   = dPicks.filter(p => p.result === 'hit').length;
                const nMiss   = dPicks.filter(p => p.result === 'miss').length;
                const nPend   = dPicks.filter(p => p.result === 'pending').length;
                const parts   = [];
                if (nHits) parts.push(nHits + ' hit');
                if (nMiss) parts.push(nMiss + ' miss');
                if (nPend) parts.push(nPend + ' pending');
                const summary = d.slice(5) + ' — ' + (parts.join(', ') || 'voided');

                // Group by who within this date
                const whoMap = {};
                dPicks.forEach(p => { if (!whoMap[p.who]) whoMap[p.who] = []; whoMap[p.who].push(p); });

                const rows = Object.entries(whoMap).map(([who, picks]) => {
                    const pickSpans = picks.map(p => {
                        const icon = p.result === 'hit' ? '✅' : p.result === 'miss' ? '❌' : p.result === 'void' ? '🚫' : '⏳';
                        const name = p.player_name || '—';
                        const score = p.actual !== null && p.actual !== undefined ? ' (' + p.actual + ')' : '';
                        let btns = '';
                        if (p.result === 'pending') {
                            btns = ' <button class="result-btn hit" style="padding:1px 6px;font-size:10px" data-id="' + p.id + '" data-r="hit" onclick="setResult(this.dataset.id,this.dataset.r)">Hit</button>' +
                                   ' <button class="result-btn miss" style="padding:1px 6px;font-size:10px" data-id="' + p.id + '" data-r="miss" onclick="setResult(this.dataset.id,this.dataset.r)">Miss</button>' +
                                   ' <button class="result-btn" style="padding:1px 6px;font-size:10px;color:#ffaa33;border-color:#ffaa33" data-id="' + p.id + '" onclick="voidPick(this.dataset.id)">DNP</button>';
                        }
                        return icon + ' ' + name + score + btns;
                    }).join('<span style="color:#334455"> &nbsp;·&nbsp; </span>');
                    return '<div style="padding:4px 0;border-bottom:1px solid #0d1a2a;font-size:12px">' +
                        '<span style="font-weight:700;color:#ddeeff;min-width:80px;display:inline-block">' + who + '</span>' +
                        '<span style="color:#99aabb">' + pickSpans + '</span></div>';
                }).join('');

                return '<details style="border:1px solid #1a2a3a;border-radius:6px;margin-bottom:6px;overflow:hidden">' +
                    '<summary style="padding:8px 12px;cursor:pointer;background:#0a1525;color:#aabbcc;font-size:12px;font-weight:600;list-style:none;display:flex;justify-content:space-between">' +
                    '<span>' + summary + '</span><span style="color:#334455">▼</span></summary>' +
                    '<div style="padding:4px 12px 8px;background:#060f1c">' + rows + '</div>' +
                    '</details>';
            }).join('');
        }

        const byWho = {};
        allPicks.filter(p => p.result !== 'void').forEach(p => {
            if (!byWho[p.who]) byWho[p.who] = { hits: 0, miss: 0, pend: 0 };
            if (p.result === 'hit') byWho[p.who].hits++;
            else if (p.result === 'miss') byWho[p.who].miss++;
            else byWho[p.who].pend++;
        });
        const board = Object.entries(byWho)
            .map(([name, s]) => {
                const tot = s.hits + s.miss;
                return { name, ...s, rate: tot ? Math.round((s.hits / tot) * 100) : null };
            })
            .sort((a, b) => (b.rate ?? -1) - (a.rate ?? -1));

        const medals = ['1st','2nd','3rd'];
        const lb = document.getElementById('tr-leaderboard');
        lb.innerHTML = !board.length
            ? '<div style="color:#334455;text-align:center;padding:16px;font-style:italic">No data yet.</div>'
            : board.map((r, i) => {
                const rc = r.rate >= 55 ? '#00ff88' : r.rate !== null && r.rate < 45 ? '#ff4444' : '#aabbcc';
                return '<div class="leaderboard-row">' +
                    '<div class="lb-medal">' + (i < 3 ? medals[i] : i + 1) + '</div>' +
                    '<div class="lb-name">' + r.name + '</div>' +
                    '<div class="lb-stats"><div class="lb-rate" style="color:' + rc + '">' +
                    (r.rate !== null ? r.rate + '%' : '—') + '</div>' +
                    r.hits + 'W · ' + r.miss + 'L · ' + r.pend + ' pending</div></div>';
            }).join('');

        updateFabCount();
    }

    // Safe async init — Supabase errors never block modal clicks
    (async () => { try { await updateFabCount(); } catch(e) { console.warn('FAB init:', e); } })();
    """

    # ── Tonight's games strip — clickable chips + modals ────
    games_html   = ""
    game_modals  = ""

    for gm_idx, gm in enumerate(games_meta):
        pf      = gm["park_factor"]
        pf_col  = "#00cc66" if pf >= 105 else ("#ffaa33" if pf >= 101 else "#ff6644")
        lu_cls  = "confirmed" if gm["lineup_confirmed"] else "pending"
        lu_txt  = "★ Lineup Confirmed" if gm["lineup_confirmed"] else "⚠ Lineup Pending"
        wx      = gm.get("weather", {})
        wx_lbl  = esc(wx.get("wind_label", ""))
        wx_col  = wx.get("wind_color", "#445566")
        modal_id = f"modal-game-{gm_idx}"

        # Find top 5 players for this game
        away_name = gm["away"]
        home_name = gm["home"]
        game_players = [
            p for p in all_players
            if p["team"] in (away_name, home_name)
        ][:5]

        # Build the 5 cards for this game's modal
        game_modal_cards = ""
        slate_accents = ["#1D9E75","#185FA5","#6644AA","#854F0B","#5F5E5A"]  # fallback only
        slate_rnum    = ["#00ff88","#66aaff","#aa88ff","#ffaa33","#888780"]

        for j, p in enumerate(game_players):
            proj_str_m   = p.get("proj_range", "?-?")
            dk_line_m, _ = dk_line_recommendation(p["recent_dk"], p.get("proj_total"))
            hc_emoji_m, hc_label_m, hc_bg_m, hc_col_m = hot_cold_badge(p["recent_dk"])
            tr_arrow_m, tr_label_m, tr_color_m = trend_arrow(p["recent_dk"])
            cc_m  = CONF_COLORS.get(p["conf_grade"], ("#888899","#111122"))
            mc_m  = MATCH_COLORS.get(p["matchup_grade"], "#888899")
            acc_m = TEAM_COLORS.get(p["team"], DEFAULT_TEAM_COLOR)
            rnc_m = slate_rnum[j]    if j < len(slate_rnum)    else "#888780"
            order_m  = p["order_label"] if p["lineup_confirmed"] else "Pending"
            ha_m     = "Home" if p["is_home"] else "Away"
            wx_m     = p.get("weather", {})
            wx_lbl_m = esc(wx_m.get("wind_label", ""))
            wx_col_m = wx_m.get("wind_color", "#445566")
            inj_m    = p.get("injury", {})
            inj_html_m = f'<span class="badge" style="background:#220000;color:{inj_m["color"]};font-weight:700">{esc(inj_m["label"])}</span>' if inj_m.get("flagged") else ""
            pl_m     = p.get("p_last_start", {})
            pf_data_m = p.get("p_form", {})
            pl_col_m = "#445566"
            pl_txt_m = "Last start: no data"
            pf_lbl_m = esc(pf_data_m.get("label", ""))
            pf_col_m = pf_data_m.get("color", "#445566")
            if pl_m and pl_m.get("er") is not None:
                danger_m = pl_m["er"] >= 4 or (pl_m.get("ip", 9) < 4)
                pl_col_m = "#ff6644" if danger_m else "#445566"
                pl_txt_m = f"⚠ Last start rough: {pl_m['er']}ER / {pl_m.get('ip','?')}IP — target tonight" if danger_m else f"Last start: {pl_m['er']}ER / {pl_m.get('ip','?')}IP vs {pl_m.get('opp','?')} ({pl_m.get('date','?')})"
            spark_m  = sparkline_html(p["recent_dk"])
            bd_m     = p.get("proj_breakdown") or {}
            bf_m        = p.get("batter_form", {})
            bf_lbl_m    = esc(bf_m.get("label", ""))
            bf_col_m    = {"hot": "#ff8844", "warm": "#ffaa33", "neutral": "#445566", "cool": "#66aaff", "cold": "#99aacc"}.get(bf_m.get("trend", "neutral"), "#445566")
            bf_r_avg_m  = bf_m.get("recent_avg", 0)
            bf_s_avg_m  = bf_m.get("season_avg", 0)
            bf_l5_avg_m = bf_m.get("last5_avg",  0)
            bf_detail_m = esc(f"Season avg: {bf_s_avg_m:.1f} DK/g  ·  Last 10: {bf_r_avg_m:.1f} DK/g  ·  Last 5: {bf_l5_avg_m:.1f} DK/g") if bf_r_avg_m else ""
            pt_m     = p.get("platoon", {})
            ph_m     = p.get("p_hand", "")
            if pt_m and ph_m:
                pt_ops_m  = pt_m.get("ops", 0)
                pt_pa_m   = pt_m.get("pa", 0)
                hl_m      = "LHP" if ph_m == "L" else "RHP"
                pt_lbl_m  = esc(f"{p['name'].split()[-1]} vs {hl_m}: {fmt_avg(pt_ops_m)} OPS ({pt_pa_m} PA this season)")
                pt_col_m  = "#00cc66" if pt_ops_m >= 0.800 else ("#ffaa33" if pt_ops_m >= 0.700 else "#ff6644")
            else:
                pt_lbl_m, pt_col_m = "", "#445566"

            game_modal_cards += f"""
            <div class="fc-card">
                <div style="height:3px;background:{acc_m}"></div>
                <div class="fc-header">
                    <div style="display:flex;align-items:flex-start;gap:10px">
                        <div class="fc-headshot-wrap">
                            <img class="fc-headshot"
                                 src="https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{p['id']}/headshot/67/current"
                                 onerror="this.onerror=null;this.style.opacity='0.15'"
                                 alt="{esc(p['name'])}">
                            <div class="fc-rank-badge" style="color:{rnc_m}">#{j+1}</div>
                        </div>
                        <div>
                            <div class="fc-name">{esc(p['name'])}</div>
                            <div class="fc-sub">{esc(p['team'])} · {esc(p['pos'])} · {esc(p['side'])}HH · {ha_m}</div>
                            <div class="fc-badges">
                                <span class="badge-labeled">
                                    <span class="badge" style="background:{cc_m[0]};color:{cc_m[1]}">{esc(p['conf_grade'])}</span>
                                    <span class="badge-lbl">CONF</span>
                                </span>
                                <span class="badge-labeled">
                                    <span class="badge" style="background:{mc_m}22;color:{mc_m}">{esc(p['matchup_grade'])}</span>
                                    <span class="badge-lbl">MATCHUP</span>
                                </span>
                                <span class="badge" style="background:#111828;color:#66aaff">{esc(order_m)}</span>
                                <span class="badge" style="background:{hc_bg_m};color:{hc_col_m}">{hc_emoji_m} {hc_label_m}</span>
                                <span class="badge" style="background:#0a1020;color:{tr_color_m}">{tr_arrow_m} {esc(tr_label_m)}</span>
                                {inj_html_m}
                            </div>
                            <div class="fc-remark">{esc(card_remark(p))}</div>
                        </div>
                    </div>
                    <div class="fc-score-block">
                        <div class="fc-score-num" style="color:{rnc_m}">{p['score']}</div>
                        <div class="fc-score-lbl">Guru Score</div>
                        {score_bar_html(p['score'])}
                        {(lambda d: f'<div style="font-size:10px;font-weight:700;color:{"#00cc66" if d>0 else "#ff6644"};margin-top:2px">{"▲" if d>0 else "▼"} {d:+.1f} since last run</div>' if d != 0 else "")(p.get("score_delta", 0))}
                    </div>
                </div>
                <div class="fc-body">
                    <div class="fc-stat-row">
                        <div class="fc-stat"><div class="fc-sv">{p['season_avg']}</div><div class="fc-sl">AVG</div></div>
                        <div class="fc-stat"><div class="fc-sv">{p['season_hr']}</div><div class="fc-sl">HR</div></div>
                        <div class="fc-stat"><div class="fc-sv">{p['season_rbi']}</div><div class="fc-sl">RBI</div></div>
                        <div class="fc-stat"><div class="fc-sv">{p['hr_pct']}%</div><div class="fc-sl">HR/G</div></div>
                    </div>
                    <div class="fc-section-lbl">Recent {len(p['recent_games'])} Games — DK pts per game</div>
                    {spark_m}
                    <div style="font-size:12px;color:{bf_col_m};font-weight:700;padding:2px 0 1px">{bf_lbl_m}</div>
                    <div style="font-size:10px;color:#556677;padding:0 0 4px">{bf_detail_m}</div>
                    <div class="fc-info-grid">
                        <div class="fc-info-box">
                            <div class="fc-ib-label">Pitcher</div>
                            <div class="fc-ib-val">{esc(p['opp_pitcher'])}</div>
                            <div class="fc-ib-sub">ERA {p['p_era']} · WHIP {p['p_whip']}</div>
                            {pitcher_kbb_html(p.get('p_stats_raw'))}
                            <div class="fc-ib-sub" style="color:{pl_col_m};margin-top:4px">{pl_txt_m}</div>
                            <div class="fc-ib-sub" style="color:{pf_col_m};margin-top:3px">{pf_lbl_m}</div>
                        </div>
                        <div class="fc-info-box">
                            <div class="fc-ib-label">Matchup</div>
                            {f'<div class="fc-ib-sub" style="color:{pt_col_m};margin-bottom:4px">{pt_lbl_m}</div>' if pt_lbl_m else ""}
                            {h2h_card_html(p['h2h'], p['opp_pitcher'])}
                        </div>
                    </div>
                    <div style="font-size:11px;padding:6px 0 2px;color:{wx_col_m};font-weight:600">{wx_lbl_m}</div>
                    {f'<div style="font-size:11px;padding:0 0 4px;color:#aaccff;font-weight:600">{esc(p.get("vegas_label",""))}</div>' if p.get("vegas_label") else ""}
                    <div class="fc-dk-rec">
                        <div>
                            <div class="fc-dk-label">Recommended Bet</div>
                            <div class="fc-dk-val">{esc(dk_line_m)}</div>
                        </div>
                        <div style="text-align:right">
                            <div class="fc-dk-label">Projected Range</div>
                            <div class="fc-dk-range">{esc(proj_str_m)}</div>
                        </div>
                    </div>
                    <div style="font-size:10px;color:#334455;padding:5px 0 2px;line-height:1.7">
                        📱 <b style="color:#445566">How to bet:</b>
                        DraftKings app → More → Props → Player Props →
                        search <b style="color:#667788">{esc(p['name'].split()[-1])}</b> →
                        Hits+Runs+RBIs → tap <b style="color:#667788">{esc(dk_line_m.split()[0])} {esc(dk_line_m.split()[1]) if len(dk_line_m.split()) > 1 else ''}</b>
                    </div>
                    <div class="pick-box">
                        <div class="pick-box-title">✅ Log your pick</div>
                        <div class="pick-box-row">
                            <input class="pick-name-input" type="text" placeholder="Your name"
                                   id="pn-{p['id']}-m" list="names-datalist" autocomplete="off">
                            <select class="pick-line-select" id="pl-{p['id']}-m">
                                <option>Over 1</option>
                                <option selected>Over 2</option>
                                <option>Over 3</option>
                                <option>Over 4</option>
                            </select>
                            <button class="pick-submit-btn"
                                    data-pid="{p['id']}" data-pname="{esc(p['name'])}"
                                    data-conf="{esc(p['conf_grade'])}" data-match="{esc(p['matchup_grade'])}"
                                    data-score="{p['score']}" data-suffix="m"
                                    onclick="logPickBtn(this)">
                                ⚾ HRR Pick
                            </button>
                        </div>
                        <div style="font-size:10px;margin-top:3px;color:{'#00cc66' if p['lineup_confirmed'] else '#ffaa33'}">
                            {'★ Lineup confirmed' if p['lineup_confirmed'] else '⚠ Lineup pending — pick still counts'}
                        </div>
                        <div class="pick-submitted" id="ps-{p['id']}-m"></div>
                    </div>
                </div>
            </div>"""

        # Clickable game chip
        games_html += f"""
        <div class="game-chip" onclick="openModal('{modal_id}')">
            <div class="matchup">{esc(gm['away'])} @ {esc(gm['home'])}</div>
            <div class="game-meta">
                🕒 {esc(gm['time'])} &nbsp;|&nbsp; {esc(gm['venue'])}<br>
                ⚾ {esc(gm['away_p'])} vs {esc(gm['home_p'])}<br>
                <span style="color:{wx_col};font-weight:600">{wx_lbl}</span>
            </div>
            <span class="park-pill" style="background:{pf_col}22;color:{pf_col}">
                Park Factor {pf}
            </span>
            <span class="lineup-pill {lu_cls}">{lu_txt}</span>
            <div class="chip-click-hint">▶ TAP FOR TOP 5 PICKS</div>
        </div>"""

        # Modal for this game
        game_modals += f"""
        <div class="modal-backdrop" id="{modal_id}">
            <div class="modal-box">
                <div class="modal-header">
                    <div>
                        <div class="modal-title">⚾ {esc(gm['away'])} @ <span>{esc(gm['home'])}</span></div>
                        <div class="modal-meta">🕒 {esc(gm['time'])} · {esc(gm['venue'])} · Park {pf} · {lu_txt}</div>
                    </div>
                    <button class="modal-close" onclick="closeModal('{modal_id}')" aria-label="Close">✕</button>
                </div>
                <div class="modal-body">
                    <div class="modal-grid">{game_modal_cards}</div>
                </div>
            </div>
        </div>"""

    # ── Unified Top 10 full cards ────────────────────────────
    # Accent colors per rank position (1=gold green, 2=blue, 3=purple, 4-10 fade)
    rank_accents = [
        "#1D9E75","#185FA5","#6644AA",
        "#1D9E75","#185FA5","#854F0B",
        "#5F5E5A","#185FA5","#791F1F","#5F5E5A"
    ]
    rank_num_colors = [
        "#00ff88","#66aaff","#aa88ff",
        "#00cc66","#4488dd","#ffaa33",
        "#888780","#4488dd","#ff6644","#888780"
    ]

    top10_cards_html = ""
    for i, p in enumerate(top10):
        proj_str, _, _       = projected_dk_range(
            p["recent_dk"], p.get("season_avg") and {},
        )
        # Use stored mathematical projection
        proj_str   = p.get("proj_range", proj_str)
        dk_line, _ = dk_line_recommendation(p["recent_dk"], p.get("proj_total"))
        h2h_txt, h2h_color   = h2h_badge(p["h2h"])
        hc_emoji, hc_label, hc_bg, hc_col = hot_cold_badge(p["recent_dk"])
        tr_arrow, tr_label, tr_color       = trend_arrow(p["recent_dk"])
        bd = p.get("proj_breakdown") or {}
        order_txt  = p["order_label"] if p["lineup_confirmed"] else "Pending"
        ha         = "Home" if p["is_home"] else "Away"
        accent     = TEAM_COLORS.get(p["team"], DEFAULT_TEAM_COLOR)
        rnum_col   = rank_num_colors[i]
        cc         = CONF_COLORS.get(p["conf_grade"],  ("#888899","#111122"))
        mc         = MATCH_COLORS.get(p["matchup_grade"], "#888899")

        # Weather badge
        wx       = p.get("weather", {})
        wx_lbl   = esc(wx.get("wind_label", ""))
        wx_col   = wx.get("wind_color", "#445566")

        # Injury flag
        inj      = p.get("injury", {})
        inj_html = ""
        if inj.get("flagged"):
            inj_html = f'<span class="badge" style="background:#220000;color:{inj["color"]};font-weight:700">{esc(inj["label"])}</span>'

        # Pitcher last start summary
        pl = p.get("p_last_start", {})
        if pl and pl.get("er") is not None:
            danger = pl["er"] >= 4 or (pl.get("ip", 9) < 4)
            pl_col = "#ff6644" if danger else "#445566"
            pl_txt = f"Last start: {pl['er']}ER / {pl.get('ip','?')}IP vs {pl.get('opp','?')} ({pl.get('date','?')})"
            if danger:
                pl_txt = f"⚠ Last start rough: {pl['er']}ER / {pl.get('ip','?')}IP — target tonight"
        else:
            pl_col = "#445566"
            pl_txt = "Last start: no data"

        # Pitcher recent form (last 5 starts vs season)
        pf        = p.get("p_form", {})
        pf_lbl    = esc(pf.get("label", ""))
        pf_col    = pf.get("color", "#445566")

        # Batter recent form
        bf          = p.get("batter_form", {})
        bf_lbl      = esc(bf.get("label", ""))
        _bf_cols    = {"hot": "#ff8844", "warm": "#ffaa33", "neutral": "#445566", "cool": "#66aaff", "cold": "#99aacc"}
        bf_col      = _bf_cols.get(bf.get("trend", "neutral"), "#445566")
        bf_r_avg    = bf.get("recent_avg", 0)
        bf_s_avg    = bf.get("season_avg", 0)
        bf_l5_avg   = bf.get("last5_avg",  0)
        bf_detail   = esc(f"Season avg: {bf_s_avg:.1f} DK/g  ·  Last 10: {bf_r_avg:.1f} DK/g  ·  Last 5: {bf_l5_avg:.1f} DK/g") if bf_r_avg else ""

        # Platoon split vs pitcher hand
        pt        = p.get("platoon", {})
        pt_hand   = p.get("p_hand", "")
        if pt and pt_hand:
            pt_ops  = pt.get("ops", 0)
            pt_pa   = pt.get("pa", 0)
            hand_lbl = "LHP" if pt_hand == "L" else "RHP"
            pt_lbl  = esc(f"{p['name'].split()[-1]} vs {hand_lbl}: {fmt_avg(pt_ops)} OPS ({pt_pa} PA this season)")
            pt_col  = "#00cc66" if pt_ops >= 0.800 else ("#ffaa33" if pt_ops >= 0.700 else "#ff6644")
        else:
            pt_lbl, pt_col = "", "#445566"

        season_vol = p["season_hits"] + p["season_runs"] + p["season_rbi"]
        momentum   = sum(p["recent_dk"]) * 3

        # Full SVG sparkline with trend overlay
        sparkline  = sparkline_html(p["recent_dk"])

        # Factor bars (3 key ones — clean, not cluttered)
        factors = ""
        factors += factor_bar_html("Season Volume",   season_vol,       250, "#66aaff", f"{season_vol} H+R+RBI")
        factors += factor_bar_html("Recent Momentum", momentum,         300, "#00ff88", f"last 10 × 3")
        factors += factor_bar_html("Park Factor",     p["park_factor"], 116, "#ffaa33", f"×{p['park_factor']/100:.2f}")

        top10_cards_html += f"""
        <div class="fc-card" data-rank="{i+1}" data-time="{esc(p['time'])}" data-streak="{hc_label}" data-grade="{esc(p['conf_grade'])}">
            <div style="height:4px;background:{accent}"></div>
            <div class="fc-header">
                <div style="display:flex;align-items:flex-start;gap:10px">
                    <div class="fc-headshot-wrap">
                        <img class="fc-headshot"
                             src="https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{p['id']}/headshot/67/current"
                             onerror="this.onerror=null;this.style.opacity='0.15'"
                             alt="{esc(p['name'])}">
                        <div class="fc-rank-badge" style="color:{rnum_col}">#{i+1}</div>
                    </div>
                    <div>
                        <div class="fc-name">{esc(p['name'])}</div>
                        <div class="fc-sub">{esc(p['team'])} · {esc(p['pos'])} · {esc(p['side'])}HH · {ha} · {esc(p['time'])}</div>
                        <div class="fc-badges">
                            <span class="badge-labeled">
                                <span class="badge" style="background:{cc[0]};color:{cc[1]}">{esc(p['conf_grade'])}</span>
                                <span class="badge-lbl">CONF</span>
                            </span>
                            <span class="badge-labeled">
                                <span class="badge" style="background:{mc}22;color:{mc}">{esc(p['matchup_grade'])}</span>
                                <span class="badge-lbl">MATCHUP</span>
                            </span>
                            <span class="badge" style="background:#111828;color:#66aaff">{esc(order_txt)}</span>
                            <span class="badge" style="background:{hc_bg};color:{hc_col}">{hc_emoji} {hc_label}</span>
                            <span class="badge" style="background:#0a1020;color:{tr_color}">{tr_arrow} {esc(tr_label)}</span>
                            {inj_html}
                        </div>
                        <div class="fc-remark">{esc(card_remark(p))}</div>
                    </div>
                </div>
                <div class="fc-score-block">
                    <div class="fc-score-num" style="color:{rnum_col}">{p['score']}</div>
                    <div class="fc-score-lbl">Guru Score</div>
                    {score_bar_html(p['score'])}
                    {(lambda d: f'<div style="font-size:10px;font-weight:700;color:{"#00cc66" if d>0 else "#ff6644"};margin-top:2px">{"▲" if d>0 else "▼"} {d:+.1f} since last run</div>' if d != 0 else "")(p.get("score_delta", 0))}
                </div>
            </div>
            <div class="fc-body">
                <div class="fc-stat-row">
                    <div class="fc-stat"><div class="fc-sv">{p['season_avg']}</div><div class="fc-sl">AVG</div></div>
                    <div class="fc-stat"><div class="fc-sv">{p['season_hr']}</div><div class="fc-sl">HR</div></div>
                    <div class="fc-stat"><div class="fc-sv">{p['season_rbi']}</div><div class="fc-sl">RBI</div></div>
                    <div class="fc-stat"><div class="fc-sv">{p['hr_pct']}%</div><div class="fc-sl">HR/G</div></div>
                </div>

                <div class="fc-section-lbl">Recent {len(p['recent_games'])} Games — DK pts per game</div>
                {sparkline}
                <div style="font-size:12px;color:{bf_col};font-weight:700;padding:2px 0 1px">{bf_lbl}</div>
                <div style="font-size:10px;color:#556677;padding:0 0 6px">{bf_detail}</div>

                <div class="fc-section-lbl">Score Factors</div>
                {factors}

                <div class="fc-info-grid">
                    <div class="fc-info-box">
                        <div class="fc-ib-label">Pitcher</div>
                        <div class="fc-ib-val">{esc(p['opp_pitcher'])}</div>
                        <div class="fc-ib-sub">ERA {p['p_era']} · WHIP {p['p_whip']}</div>
                        {pitcher_kbb_html(p.get('p_stats_raw'))}
                        <div class="fc-ib-sub" style="color:{pl_col};margin-top:4px">{pl_txt}</div>
                        <div class="fc-ib-sub" style="color:{pf_col};margin-top:3px">{pf_lbl}</div>
                    </div>
                    <div class="fc-info-box">
                        <div class="fc-ib-label">Matchup</div>
                        {f'<div class="fc-ib-sub" style="color:{pt_col};margin-bottom:4px">{pt_lbl}</div>' if pt_lbl else ""}
                        {h2h_card_html(p['h2h'], p['opp_pitcher'])}
                    </div>
                </div>
                <div style="font-size:11px;padding:6px 0 2px;color:{wx_col};font-weight:600">
                    {wx_lbl}
                </div>
                {f'<div style="font-size:11px;padding:0 0 4px;color:#aaccff;font-weight:600">{esc(p.get("vegas_label",""))}</div>' if p.get("vegas_label") else ""}

                <div class="fc-dk-rec">
                    <div>
                        <div class="fc-dk-label">Recommended Bet</div>
                        <div class="fc-dk-val">{esc(dk_line)}</div>
                    </div>
                    <div style="text-align:right">
                        <div class="fc-dk-label">Projected DK pts</div>
                        <div class="fc-dk-range">{esc(proj_str)}</div>
                    </div>
                </div>
                <div style="font-size:10px;color:#334455;padding:5px 0 2px;line-height:1.7">
                    📱 <b style="color:#445566">How to bet:</b>
                    DraftKings app → More → Props → Player Props →
                    search <b style="color:#667788">{esc(p['name'].split()[-1])}</b> →
                    Hits+Runs+RBIs → tap <b style="color:#667788">{esc(dk_line.split()[0])} {esc(dk_line.split()[1]) if len(dk_line.split()) > 1 else ''}</b>
                </div>
                <div class="pick-box">
                    <div class="pick-box-title">✅ Log your pick</div>
                    <div class="pick-box-row">
                        <input class="pick-name-input" type="text" placeholder="Your name"
                               id="pn-{p['id']}-t" list="names-datalist" autocomplete="off">
                        <select class="pick-line-select" id="pl-{p['id']}-t">
                            <option>Over 1</option>
                            <option selected>Over 2</option>
                            <option>Over 3</option>
                            <option>Over 4</option>
                        </select>
                        <button class="pick-submit-btn"
                                data-pid="{p['id']}" data-pname="{esc(p['name'])}"
                                data-conf="{esc(p['conf_grade'])}" data-match="{esc(p['matchup_grade'])}"
                                data-score="{p['score']}" data-suffix="t"
                                onclick="logPickBtn(this)">
                            ⚾ HRR Pick
                        </button>
                    </div>
                    <div style="font-size:10px;margin-top:3px;color:{'#00cc66' if p['lineup_confirmed'] else '#ffaa33'}">
                        {'★ Lineup confirmed' if p['lineup_confirmed'] else '⚠ Lineup pending — pick still counts'}
                    </div>
                    <div class="pick-submitted" id="ps-{p['id']}-t"></div>
                </div>
                <div class="fc-math-breakdown">
                    <div class="fc-math-title">📐 Math Projection (v4)</div>
                    <div class="fc-math-grid">
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Proj PA</span>
                            <span class="fc-math-val">{bd.get("pa", "—")}</span>
                        </div>
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Adj AVG</span>
                            <span class="fc-math-val">{fmt_avg(bd.get("adj_avg", 0))}</span>
                        </div>
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Proj H</span>
                            <span class="fc-math-val">{bd.get("proj_h", "—")}</span>
                        </div>
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Proj R</span>
                            <span class="fc-math-val">{bd.get("proj_r", "—")}</span>
                        </div>
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Proj RBI</span>
                            <span class="fc-math-val">{bd.get("proj_rbi", "—")}</span>
                        </div>
                        <div class="fc-math-item">
                            <span class="fc-math-lbl">Total</span>
                            <span class="fc-math-val" style="color:#00ff88;font-weight:700">{p.get("proj_total", "—")}</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>"""

    # ── Time filter options (kept for potential future use) ──
    unique_times = sorted(
        set(p["time"] for p in all_players),
        key=lambda x: datetime.strptime(x, "%I:%M %p ET")
    )

    # ── Top Projected Plays table (clean, sortable) ───────────
    tp_rows = ""
    for i, p in enumerate(all_players[:20], 1):
        vs = "@" if not p.get("is_home") else "vs"
        tp_rows += f"""<tr>
        <td class="tp-rank">{i}</td>
        <td class="tp-name">{esc(p['name'])}<div class="tp-sub">{esc(p['team'])} {vs} {esc(p.get('opp_pitcher','TBD'))}</div></td>
        <td>{p.get('proj_h',0):.1f}</td>
        <td>{p.get('proj_r',0):.1f}</td>
        <td>{p.get('proj_rbi',0):.1f}</td>
        <td class="tp-total">{p.get('proj_total',0):.1f}</td>
        <td>{p.get('proj_floor','')}–{p.get('proj_ceiling','')}</td>
        <td>{esc(str(p.get('conf_grade','')))}</td>
    </tr>"""

    top_plays_html = f"""
    <div class="section-title">📈 Top Projected Plays — tap a column to sort</div>
    <style>
      .tp-wrap {{ overflow-x:auto; border:1px solid #1a2a4a; border-radius:12px; margin-bottom:8px; }}
      .tp-table {{ width:100%; border-collapse:collapse; font-size:13px; min-width:560px; }}
      .tp-table th {{ background:#0d1f3c; color:#88aadd; padding:10px 8px; text-align:center;
                      cursor:pointer; user-select:none; white-space:nowrap; font-weight:700; }}
      .tp-table th:hover {{ color:#00ff88; }}
      .tp-table td {{ padding:9px 8px; text-align:center; border-top:1px solid #14213a; }}
      .tp-table tr:nth-child(even) td {{ background:#0c1220; }}
      .tp-name {{ text-align:left !important; font-weight:700; color:#e6e9f5; }}
      .tp-sub {{ font-size:11px; color:#6677aa; font-weight:400; margin-top:2px; }}
      .tp-rank {{ color:#7788aa; font-weight:700; }}
      .tp-total {{ color:#00cc66; font-weight:800; }}
      /* light-mode overrides */
      body.light .tp-wrap {{ border-color:#ccd8ea; }}
      body.light .tp-table td {{ border-top-color:#e0e8f2; color:#2a3a50; }}
      body.light .tp-table tr:nth-child(even) td {{ background:#eef3f9; }}
      body.light .tp-name {{ color:#1a2a40; }}
      body.light .tp-sub {{ color:#667788; }}
      body.light .tp-rank {{ color:#889aac; }}
      body.light .tp-total {{ color:#0a9d54; }}
    </style>
    <div class="tp-wrap">
      <table class="tp-table" id="tp-table">
        <thead><tr>
          <th onclick="sortTP(0,true)">#</th>
          <th onclick="sortTP(1,false)" style="text-align:left">Player</th>
          <th onclick="sortTP(2,true)">H</th>
          <th onclick="sortTP(3,true)">R</th>
          <th onclick="sortTP(4,true)">RBI</th>
          <th onclick="sortTP(5,true)">Total</th>
          <th onclick="sortTP(6,false)">Range</th>
          <th onclick="sortTP(7,false)">Conf</th>
        </tr></thead>
        <tbody>{tp_rows}</tbody>
      </table>
    </div>
    <script>
      function sortTP(col, numeric) {{
        var tb = document.getElementById('tp-table').tBodies[0];
        var rows = Array.prototype.slice.call(tb.rows);
        var asc = tb.getAttribute('data-col') == col ? tb.getAttribute('data-asc') != 'true' : false;
        rows.sort(function(a, b) {{
          var x = a.cells[col].innerText.trim(), y = b.cells[col].innerText.trim();
          if (numeric) {{ x = parseFloat(x) || 0; y = parseFloat(y) || 0; return asc ? x - y : y - x; }}
          return asc ? x.localeCompare(y) : y.localeCompare(x);
        }});
        rows.forEach(function(r) {{ tb.appendChild(r); }});
        tb.setAttribute('data-col', col); tb.setAttribute('data-asc', asc);
      }}
    </script>
    """

    # ── Splash screen (Phillie Phanatic GIF) ──────────────────
    # Plain string (not an f-string) so CSS/JS braces stay literal.
    splash_html = """
    <div id="hb-splash" onclick="hbSkip()">
      <img src="phanatic.gif" alt="Phillie Phanatic" class="hb-gif">
      <div class="hb-title">&#9918; H-Bomb</div>
      <div class="hb-tag">tap to enter</div>
    </div>
    <style>
      #hb-splash{position:fixed;inset:0;z-index:99999;display:flex;flex-direction:column;
        align-items:center;justify-content:center;gap:18px;cursor:pointer;
        background:radial-gradient(circle at 50% 40%,#123a66 0%,#0a1830 45%,#04060d 100%);
        animation:hbFade .7s ease 4s forwards;}
      #hb-splash.hb-gone{animation:hbFade .4s ease forwards;}
      @keyframes hbFade{to{opacity:0;visibility:hidden;}}
      .hb-gif{width:min(340px,70vw);height:auto;border-radius:18px;
        box-shadow:0 12px 40px rgba(0,0,0,.55);animation:hbPop .5s cubic-bezier(.2,1.4,.4,1) both;}
      @keyframes hbPop{0%{opacity:0;transform:scale(.6);}100%{opacity:1;transform:scale(1);}}
      .hb-title{font-family:-apple-system,'Segoe UI',sans-serif;font-weight:900;
        font-size:clamp(28px,7vw,48px);letter-spacing:-.5px;
        background:linear-gradient(90deg,#00ff88,#7affc4);-webkit-background-clip:text;
        background-clip:text;-webkit-text-fill-color:transparent;opacity:0;
        animation:hbRise .5s ease .35s both;}
      .hb-tag{font-family:-apple-system,'Segoe UI',sans-serif;font-size:12px;
        letter-spacing:.25em;text-transform:uppercase;color:#5d7aa6;opacity:0;
        animation:hbRise .5s ease 1s both;}
      @keyframes hbRise{0%{opacity:0;transform:translateY(12px);}100%{opacity:1;transform:translateY(0);}}
      @media (prefers-reduced-motion:reduce){
        #hb-splash{animation:hbFade .4s ease 2s forwards;}
        .hb-gif,.hb-title,.hb-tag{animation-duration:.01s;animation-delay:0s;}
      }
    </style>
    <script>
      function hbSkip(){var s=document.getElementById('hb-splash');if(s)s.classList.add('hb-gone');}
      setTimeout(function(){var s=document.getElementById('hb-splash');if(s)s.style.pointerEvents='none';},4800);
    </script>
    """

    # ── Service notice banner (dismissible) ───────────────────
    notice_html = """
    <div id="hb-notice">
      <span class="hb-notice-icon">✅</span>
      <div class="hb-notice-text">
        <b>We're back and fully operational!</b>
        Sorry for any recent hiccups over the last few days — H-Bomb is running clean again
        and updating on schedule (11 AM · 3 PM · 6 PM ET). Thanks for your patience. ⚾
      </div>
      <button class="hb-notice-x" onclick="document.getElementById('hb-notice').remove()" aria-label="Dismiss">✕</button>
    </div>
    <style>
      #hb-notice{display:flex;align-items:flex-start;gap:12px;max-width:1300px;
        margin:18px auto 0;padding:14px 16px;
        background:linear-gradient(135deg,#04231a,#062b1e);
        border:1px solid #00ff8855;border-left:4px solid #00ff88;border-radius:12px;
        color:#c8f5df;font-size:14px;line-height:1.45;}
      #hb-notice .hb-notice-icon{font-size:20px;line-height:1.2;}
      #hb-notice .hb-notice-text b{color:#00ff88;}
      #hb-notice .hb-notice-x{margin-left:auto;background:transparent;border:none;
        color:#5d8a76;font-size:16px;cursor:pointer;padding:0 4px;line-height:1;}
      #hb-notice .hb-notice-x:hover{color:#00ff88;}
      body.light #hb-notice{background:#e8f9f0;border-color:#00994488;border-left-color:#00994a;color:#1a4a35;}
      body.light #hb-notice .hb-notice-text b{color:#0a9d54;}
    </style>
    """

    # ── Assemble full page ────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#00ff88">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="H-Bomb">
<link rel="manifest" href="manifest.json">
<title>⚾ H-Bomb — Daily Picks {date.today().strftime('%b %d, %Y')}</title>
<style>{css}</style>
</head>
<body>
{splash_html}
<div class="header">
    <div class="header-left">
        <h1>⚾ Baseball <span>Guru</span></h1>
        <div class="subtitle">
            DraftKings H+R+RBI Daily Report &nbsp;·&nbsp;
            {date.today().strftime('%A, %B %d, %Y')}
        </div>
    </div>
    <div class="header-right">
        <div class="gen-time">Generated {esc(generated_at)}</div>
        <div style="margin-top:4px;color:#223344">
            Data: MLB Stats API · Free · No key required
        </div>
        <div style="margin-top:10px">
            <button class="theme-toggle" id="theme-btn" onclick="toggleTheme()" title="Toggle light/dark mode">☀️ Light</button>
        </div>
    </div>
</div>

<div class="container">

    {notice_html}

    <!-- How scores work -->
    <div class="section-title">📖 How Scores Work — tap any card</div>
    <div class="info-cards-grid">
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">⚾ DK Scoring</div>
            <div class="info-card-data" style="display:none">Each Hit, Run, and RBI = +1 DraftKings point. A Home Run scores all three at once = +3 pts minimum in a single swing.</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">📊 Guru Score</div>
            <div class="info-card-data" style="display:none">Predictive ranking — NOT raw DK pts. Higher = stronger pick tonight. Typical range 50–400. Over 280 = elite matchup. Use to compare players against each other, not as a points prediction.</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">7️⃣ Score Factors</div>
            <div class="info-card-data" style="display:none">1. Season H+R+RBI per game (normalized so April = September)
2. Last 10 games DK pts ×3 — most important factor
3. Pitcher platoon split vs batter handedness
4. H2H career history (10+ PA required)
5. Batting order position
6. Home/Away OPS split
7. Park factor + weather (wind out = HR boost)</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">🏅 Confidence</div>
            <div class="info-card-data" style="display:none">Overall betting confidence — combines ALL factors:

A+ = Everything lines up — strong bet
A  = Very good play
B+ = Good value with upside
B  = Solid but not perfect
C+ = Risky — size down
C  = Low confidence — skip</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">⚔️ Matchup</div>
            <div class="info-card-data" style="display:none">How vulnerable tonight's pitcher is to this batter's handedness (L/R). Separate from confidence.

A+ = Pitcher gets crushed by this batter type
A  = Strong edge for batter
B  = Slight edge
C  = Neutral — no real advantage
D  = Pitcher dominates — avoid</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">💵 DK Lines</div>
            <div class="info-card-data" style="display:none">Over 0.5 = safe floor, nearly any productive game
Over 1.5 = needs real contribution, good for hot bats
Over 2.5 = multiple stats needed, elite matchups only
Over 3.5 = boom or bust, strong HR upside required

In DraftKings: More → Props → Player Props → search name → Hits+Runs+RBIs</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">⭐ Lineup</div>
            <div class="info-card-data" style="display:none">★ Confirmed = official lineup posted, batting order factored into score.

⚠ Pending = not posted yet, no order bonus applied.

Re-run at 3:00 PM ET to get confirmed lineups before afternoon and evening games.</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">🚫 Injuries</div>
            <div class="info-card-data" style="display:none">🚫 IL = player is on the injured list — skip this pick, they will not play.

⚠ Day-to-Day = questionable — wait for lineup confirmation before betting.</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">📈 Sparkline</div>
            <div class="info-card-data" style="display:none">Bar chart shows last 10 games DK pts per game.
Left = oldest game · Right = most recent.

Green = 4+ pts (hot)
Blue = 2-3 pts (producing)
Gray = 0-1 pts (cold)

Rising bars = player is heating up.</div>
        </div>
        <div class="info-card" onclick="showInfo(this)">
            <div class="info-card-front">📐 Projection</div>
            <div class="info-card-data" style="display:none">Proj PA = plate appearances tonight by lineup spot (Leadoff ~4.6, Cleanup ~4.2).

Adj AVG = season avg + Statcast xBA, adjusted for pitcher matchup, H2H, batter & pitcher strikeout rates.

Runs & RBI scale to the Vegas implied team total (run environment).

Proj H/R/RBI calculated separately.

Total = 60% math model + 40% recent form.

If Total > DK line → lean Over.</div>
        </div>
    </div>

    <!-- Info popup modal -->
    <div class="info-popup-backdrop" id="info-popup-backdrop" onclick="closeInfo(event)">
        <div class="info-popup" onclick="event.stopPropagation()">
            <button class="info-popup-close" onclick="closeInfoBtn()">✕</button>
            <div class="info-popup-title" id="info-popup-title"></div>
            <div class="info-popup-body" id="info-popup-body"></div>
        </div>
    </div>

    <!-- Picks tracker callout -->
    <div class="picks-callout">
        <div class="picks-callout-text">
            🏆 <b>H-Bomb now tracks your picks!</b> Open any game or the Top 10, find your player, and tap
            <span class="picks-callout-btn">⚾ HRR Pick</span>
            to log it. Check the
            <span class="picks-callout-btn" style="background:#002211;border-color:#00ff8866">📊 My Picks</span>
            button anytime to see today's picks, hit rate, and the leaderboard.
        </div>
    </div>

    {top_plays_html}

    <!-- Tonight's games + Top 10 button -->
    <div class="section-title">📅 Tonight's Games — tap any game for top picks</div>
    <div class="games-strip">
        <!-- Top 10 button chip -->
        <div class="top10-chip" onclick="openModal('modal-top10')">
            <div class="top10-chip-icon">🏆</div>
            <div>
                <div class="top10-chip-label">Top 10 Picks</div>
                <div class="top10-chip-sub">Best plays across all games tonight</div>
                <div class="chip-click-hint" style="margin-top:6px">▶ TAP TO VIEW ALL 10</div>
            </div>
        </div>
        {games_html}
    </div>

    <div class="footer">
        ⚠️ For entertainment and informational purposes only. Always verify lines
        in the DraftKings app before placing bets. Scores are predictive rankings,
        not guaranteed outcomes. Bet responsibly.
        &nbsp;·&nbsp; Data: MLB Stats API (free)
    </div>

</div>

<!-- Floating tracker button -->
<button class="tracker-fab" onclick="openModal('modal-tracker')">
    📊 My Picks
    <span class="fab-count" id="fab-count">0</span>
</button>

<!-- Shared datalist for name autocomplete across all pick boxes -->
<datalist id="names-datalist"></datalist>

<!-- Tracker Modal -->
<div class="modal-backdrop" id="modal-tracker">
    <div class="modal-box">
        <div class="modal-header">
            <div>
                <div class="modal-title">📊 Picks <span>Tracker</span></div>
                <div class="modal-meta">Log results · track hit rate · leaderboard</div>
            </div>
            <button class="modal-close" onclick="closeModal('modal-tracker')" aria-label="Close">✕</button>
        </div>
        <div class="modal-body">
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:10px;margin-bottom:16px">
                <div class="tracker-stat"><div class="ts-val" id="tr-total">0</div><div class="ts-lbl">All-time picks</div></div>
                <div class="tracker-stat"><div class="ts-val" id="tr-today">0</div><div class="ts-lbl">Today</div></div>
                <div class="tracker-stat"><div class="ts-val" id="tr-rate" style="color:#aabbcc">—</div><div class="ts-lbl">Hit rate</div></div>
                <div class="tracker-stat"><div class="ts-val" style="color:#00ff88" id="tr-hits">0</div><div class="ts-lbl">Hits</div></div>
                <div class="tracker-stat"><div class="ts-val" style="color:#ff4444" id="tr-miss">0</div><div class="ts-lbl">Misses</div></div>
            </div>

            <div style="font-size:11px;color:#445566;font-weight:600;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Today's picks — enter result after games finish</div>
            <div style="overflow-x:auto;margin-bottom:20px">
                <table class="tracker-table">
                    <thead><tr>
                        <th>Who</th><th>Player</th><th>Line</th>
                        <th>Actual H+R+RBI</th><th>Actions</th>
                    </tr></thead>
                    <tbody id="tr-picks-body">
                        <tr><td colspan="5" style="text-align:center;color:#334455;padding:20px;font-style:italic">No picks logged today yet.</td></tr>
                    </tbody>
                </table>
            </div>

            <div style="font-size:11px;color:#445566;font-weight:600;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Past results by date</div>
            <div id="tr-past-picks" style="margin-bottom:20px">
                <div style="color:#334455;font-style:italic;font-size:12px;padding:8px 0">No past picks yet.</div>
            </div>

            <div style="font-size:11px;color:#445566;font-weight:600;text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px">🏆 All-time leaderboard</div>
            <div id="tr-leaderboard">
                <div style="color:#334455;text-align:center;padding:16px;font-style:italic">No data yet.</div>
            </div>
        </div>
    </div>
</div>

<!-- Top 10 Modal -->
<div class="modal-backdrop" id="modal-top10">
    <div class="modal-box">
        <div class="modal-header">
            <div>
                <div class="modal-title">🏆 Top <span>10</span> Picks Tonight</div>
                <div class="modal-meta">Best plays across all games · {date.today().strftime('%A, %B %d, %Y')}</div>
            </div>
            <button class="modal-close" onclick="closeModal('modal-top10')" aria-label="Close">✕</button>
        </div>
        <div class="modal-body">
            <div class="modal-grid">{top10_cards_html}</div>
        </div>
    </div>
</div>

<!-- Per-game modals -->
{game_modals}

<script>
{js}
</script>
</body>
</html>"""

    return html


# ============================================================
#  EMAIL SUMMARY
#  Clean plain text nudge — the real experience is the
#  browser dashboard that auto-opens at the same time.
# ============================================================

def build_email_summary(all_players, games_meta):
    """
    Short, readable plain text email.
    Just the top 3 picks + quick top 10 list.
    Full dashboard opens automatically in browser.
    """
    today    = date.today().strftime("%A, %B %d, %Y")
    lines    = []

    lines.append(f"⚾ BASEBALL GURU — {today}")
    lines.append("Your full dashboard has opened in your browser.")
    lines.append("=" * 48)
    lines.append("")

    # Tonight's games one-liner
    lines.append("TONIGHT'S GAMES:")
    for gm in games_meta:
        lines.append(f"  {gm['away']} @ {gm['home']}  {gm['time']}  |  Park: {gm['park_factor']}")
    lines.append("")

    # Top 3 with the key info only
    lines.append("TOP 3 PICKS")
    lines.append("=" * 48)
    for i, p in enumerate(all_players[:3], 1):
        proj_str, _, _ = projected_dk_range(p["recent_dk"])
        dk_line, _     = dk_line_recommendation(p["recent_dk"])
        hc_emoji, hc_label, _, _ = hot_cold_badge(p["recent_dk"])
        tr_arrow, tr_label, _    = trend_arrow(p["recent_dk"])
        order_txt = p["order_label"] if p["lineup_confirmed"] else "Lineup Pending"
        lines.append(f"""
#{i}  {p['name']} — {p['team']}
    {p['side']}HH · {order_txt} · {"Home" if p["is_home"] else "Away"}
    Streak:   {hc_emoji} {hc_label}  {tr_arrow} {tr_label}
    Facing:   {p['opp_pitcher']}  (ERA {p['p_era']} · WHIP {p['p_whip']})
    Matchup:  {p['matchup_grade']} — {p['matchup_label']}
    H2H:      {p['h2h_label']}
    Season:   AVG {p['season_avg']}  HR {p['season_hr']}  RBI {p['season_rbi']}  HR/G {p['hr_pct']}%
    Proj:     {proj_str}
    Bet:      {dk_line}
    Conf:     {p['conf_grade']} — {p['conf_label']}
    Score:    {p['score']}""")

    # Quick top 10 ranking
    lines.append("")
    lines.append("=" * 48)
    lines.append("TOP 10 RANKING")
    lines.append("=" * 48)
    for i, p in enumerate(all_players[:10], 1):
        proj_str, _, _ = projected_dk_range(p["recent_dk"])
        dk_line, _     = dk_line_recommendation(p["recent_dk"])
        hc_emoji, _, _, _ = hot_cold_badge(p["recent_dk"])
        tr_arrow, _, _ = trend_arrow(p["recent_dk"])
        order_txt = p["order_label"] if p["lineup_confirmed"] else "Pending"
        lines.append(
            f"  #{i:<2} {p['name']:<22} {p['team']:<20} "
            f"Score:{p['score']:<6} {proj_str:<12} "
            f"{hc_emoji} {tr_arrow}  {dk_line}"
        )

    lines.append("")
    lines.append("=" * 48)
    lines.append("⚠ For entertainment only. Verify lines before betting.")
    lines.append("=" * 48)

    return "\n".join(lines)


# ============================================================
#  DATA PIPELINE  (same as final.py — all logic in one place)
# ============================================================

def run_pipeline():
    """Pull all data, score all players, return structured results."""
    games = get_todays_games()
    if not games:
        return [], [], {}

    all_players  = []
    time_slates  = {}
    games_meta   = []

    game_totals    = get_game_totals()
    if game_totals:
        print(f"   🎰 Vegas totals loaded for {len(game_totals)} teams")
    else:
        print("   🎰 Vegas totals: no key set — scoring without (set ODDS_API_KEY to enable)")

    load_savant_xba()  # Statcast xBA map (one request, cached for the run)

    prev_scores = load_previous_scores()

    for game in games:
        away         = game["teams"]["away"]
        home         = game["teams"]["home"]
        venue        = game.get("venue", {}).get("name", "Unknown")
        park_factor  = PARK_FACTORS.get(home["team"]["name"], DEFAULT_PARK_FACTOR)
        display_time = format_time(game.get("gameDate", ""))
        game_pk      = game.get("gamePk")

        lineup_data      = get_game_lineups(game_pk) if game_pk else {"confirmed": False}
        lineup_confirmed = lineup_data.get("confirmed", False)

        # Fetch weather for this ballpark (free, no API key)
        weather = get_weather(home["team"]["name"])
        print(f"   🌤 Weather: {weather['wind_label']}")

        games_meta.append({
            "away":             away["team"]["name"],
            "home":             home["team"]["name"],
            "time":             display_time,
            "venue":            venue,
            "park_factor":      park_factor,
            "away_p":           away.get("probablePitcher", {}).get("fullName", "TBD"),
            "home_p":           home.get("probablePitcher", {}).get("fullName", "TBD"),
            "lineup_confirmed": lineup_confirmed,
            "weather":          weather,
        })

        matchup_configs = [
            (away, home.get("probablePitcher", {}), False),
            (home, away.get("probablePitcher", {}), True),
        ]

        # Build a unique key per game for grouping
        game_key = f"{display_time} — {away['team']['name']} @ {home['team']['name']}"

        vboost, vlabel = vegas_game_boost(game_totals, home["team"]["name"], away["team"]["name"])

        for team, opp_pitcher, is_home in matchup_configs:
            p_id        = opp_pitcher.get("id")
            p_name      = opp_pitcher.get("fullName", "TBD")
            p_splits    = get_pitcher_splits(p_id) if p_id else {}
            p_stats     = get_pitcher_season_stats(p_id) if p_id else {}
            p_last      = get_pitcher_last_start(p_id) if p_id else {}
            p_form      = get_pitcher_recent_form(p_id) if p_id else {"mult": 1.0, "label": "No data", "color": "#445566", "trend": "neutral"}
            p_hand      = get_pitcher_hand(p_id) if p_id else None
            team_id     = team["team"]["id"]
            team_name   = team["team"]["name"]
            team_order  = lineup_data.get(team_id, {})
            team_implied = get_implied_team_total(game_totals, team_name)

            print(f"   Loading {team_name} vs {p_name}...", end=" ", flush=True)

            all_batters = get_team_batters(team_id)
            if lineup_confirmed and team_order:
                batters = [b for b in all_batters if b["id"] in team_order] or all_batters
            else:
                batters = all_batters

            # ── Pass 1: Quick score every batter (cheap calls only) ──
            # Fetch batter data in parallel — this is the dominant cost of the
            # whole run (3 API calls per batter, for every batter on the roster
            # when lineups aren't posted yet). Sequentially this alone could
            # exceed the run's time budget on a full slate.
            quick_scored = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                fetched = list(pool.map(lambda bb: (bb, get_batter_data(bb["id"])), batters))
            for b, data in fetched:
                b_stats, b_recent_games, b_recent_dk, b_side = data
                if not b_stats:
                    continue
                batting_order = team_order.get(b["id"], 0) if lineup_confirmed else 0
                # Quick score without H2H, home/away split, or injury
                quick = batter_score(b_stats, b_recent_dk, park_factor,
                                     p_splits, b_side, batting_order, 0.750, {},
                                     weather_boost=weather["wind_boost"],
                                     pitcher_form_mult=p_form["mult"],
                                     vegas_boost=vboost)
                quick_scored.append((quick, b, b_stats, b_recent_games, b_recent_dk, b_side, batting_order))

            # Sort and take only top batters for expensive calls
            quick_scored.sort(key=lambda x: x[0], reverse=True)
            top_batters = quick_scored[:TOP_BATTERS_PER_TEAM]

            count = 0
            for _, b, b_stats, b_recent_games, b_recent_dk, b_side, batting_order in top_batters:
                order_label, order_desc = ORDER_PROFILES.get(batting_order, ("?", "Unknown"))
                # Expensive calls only for top players — run them concurrently
                # since they're independent network fetches.
                with ThreadPoolExecutor(max_workers=4) as pool:
                    f_split   = pool.submit(get_home_away_split, b["id"], is_home)
                    f_h2h     = pool.submit(get_h2h_stats, b["id"], p_id) if p_id else None
                    f_injury  = pool.submit(get_player_injury_status, b["id"])
                    f_platoon = pool.submit(get_batter_platoon_split, b["id"], p_hand)
                    split_ops = f_split.result()
                    h2h       = f_h2h.result() if f_h2h else {}
                    injury    = f_injury.result()
                    platoon   = f_platoon.result()
                h2h_mv, h2h_ml = h2h_multiplier(h2h)
                b_form     = compute_batter_form(b_recent_dk, b_stats)
                season_ops = safe_float(b_stats.get("ops"), 0.750)
                p_mult     = platoon_multiplier(platoon, season_ops)

                score    = batter_score(b_stats, b_recent_dk, park_factor,
                                        p_splits, b_side, batting_order, split_ops, h2h,
                                        weather_boost=weather["wind_boost"],
                                        pitcher_form_mult=p_form["mult"],
                                        batter_form_mult=b_form["mult"],
                                        vegas_boost=vboost,
                                        platoon_mult=p_mult)
                m_grade, m_label = matchup_grade(p_splits, b_side)
                hr_count = int(b_stats.get("homeRuns", 0))
                gp       = int(b_stats.get("gamesPlayed", 1) or 1)
                _, h2h_label_full = h2h_mv, ""

                h2h_label_str = ""
                if h2h and h2h.get("pa", 0) > 0:
                    pa  = h2h["pa"]
                    avg = h2h["avg"]
                    hr  = h2h["hr"]
                    rbi = h2h["rbi"]
                    h_c = h2h["h"]
                    trust = "✓ Trusted" if h2h.get("trusted") else f"⚠ Small sample ({pa} PA)"
                    trend = "🔥 OWNS" if avg >= 0.400 else ("✅ Strong" if avg >= 0.300 else ("❌ Struggles" if avg <= 0.150 else "→ Neutral"))
                    h2h_label_str = f"{h_c}-for-{pa} ({fmt_avg(avg)}) | {hr} HR | {rbi} RBI | {trend} | {trust}"
                else:
                    h2h_label_str = f"No career history vs {p_name}"

                # Mathematical HRR projection (v5 — Vegas total, xBA, K-rate)
                b_pa     = safe_float(b_stats.get("plateAppearances"), 0)
                b_so     = safe_float(b_stats.get("strikeOuts"), 0)
                b_k_rate = (b_so / b_pa) if b_pa > 0 else None
                (proj_h_val, proj_r_val, proj_rbi_val, proj_total_val,
                 _, proj_floor_val, proj_ceiling_val, proj_breakdown) = project_hrr(
                    b_stats, p_splits, b_side, batting_order,
                    park_factor, h2h, b_recent_dk,
                    implied_team_total=team_implied,
                    xba=SAVANT_XBA.get(b["id"]),
                    pitcher_stats=p_stats,
                    batter_k_rate=b_k_rate,
                )
                proj_range_result = projected_dk_range(
                    b_recent_dk, b_stats, p_splits,
                    b_side, batting_order, park_factor, h2h
                )
                player_obj = {
                    "id":               b["id"],
                    "name":             b["name"],
                    "pos":              b["pos"],
                    "side":             b_side,
                    "team":             team_name,
                    "is_home":          is_home,
                    "opp_pitcher":      p_name,
                    "opp_pitcher_id":   p_id,
                    "p_era":            p_stats.get("era",  "N/A"),
                    "p_whip":           p_stats.get("whip", "N/A"),
                    "p_last_start":     p_last,
                    "p_form":           p_form,
                    "p_stats_raw":      p_stats,
                    "score":            score,
                    "matchup_grade":    m_grade,
                    "matchup_label":    m_label,
                    "h2h":              h2h,
                    "h2h_label":        h2h_label_str,
                    "h2h_mv":           h2h_mv,
                    "h2h_ml":           h2h_ml,
                    "batting_order":    batting_order,
                    "order_label":      order_label,
                    "order_desc":       order_desc,
                    "lineup_confirmed": lineup_confirmed,
                    "lineup_status":    "★ CONFIRMED" if lineup_confirmed else "⚠ PENDING",
                    "split_ops":        split_ops,
                    "season_avg":       fmt_avg(b_stats.get("avg")),
                    "season_obp":       fmt_avg(b_stats.get("obp")),
                    "season_slg":       fmt_avg(b_stats.get("slg")),
                    "season_hr":        hr_count,
                    "hr_pct":           round((hr_count / gp) * 100, 1),
                    "season_rbi":       int(b_stats.get("rbi", 0)),
                    "season_runs":      int(b_stats.get("runs", 0)),
                    "season_hits":      int(b_stats.get("hits", 0)),
                    "games_played":     gp,
                    "recent_games":     b_recent_games,
                    "recent_dk":        b_recent_dk,
                    "time":             display_time,
                    "park_factor":      park_factor,
                    "venue":            venue,
                    "weather":          weather,
                    "vegas_label":      vlabel,
                    "injury":           injury,
                    "batter_form":      b_form,
                    "platoon":          platoon,
                    "p_hand":           p_hand,
                    "proj_total":       round(proj_total_val, 2),
                    "proj_h":           round(proj_h_val, 2),
                    "proj_r":           round(proj_r_val, 2),
                    "proj_rbi":         round(proj_rbi_val, 2),
                    "proj_floor":       proj_floor_val,
                    "proj_ceiling":     proj_ceiling_val,
                    "proj_breakdown":   proj_breakdown,
                    "proj_range":       proj_range_result[0],
                    "score_delta":      round(score - prev_scores.get(str(b["id"]), score), 1),
                }

                conf_grade_val, conf_label_val = confidence_grade(player_obj)
                player_obj["conf_grade"] = conf_grade_val
                player_obj["conf_label"] = conf_label_val

                all_players.append(player_obj)
                if game_key not in time_slates:
                    time_slates[game_key] = []
                time_slates[game_key].append(player_obj)
                count += 1

            print(f"✓ ({count} batters)")

    all_players.sort(key=lambda x: x["score"], reverse=True)
    save_current_scores(all_players)
    return all_players, games_meta, time_slates


# ============================================================
#  SAVE & EMAIL
# ============================================================

def load_previous_scores():
    try:
        with open(SCORES_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_current_scores(all_players):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    scores = {str(p["id"]): p["score"] for p in all_players}
    with open(SCORES_FILE, "w") as f:
        json.dump(scores, f)

def save_html(html_content):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    filename = os.path.join(OUTPUT_FOLDER, f"hrr_report_{date.today()}.html")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    return filename

def save_txt(txt_content):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    filename = os.path.join(OUTPUT_FOLDER, f"hrr_report_{date.today()}.txt")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(txt_content)
    return filename

def send_email(plain_body, deployed=False):
    """
    Sends a clean plain text summary with a live link to the
    GitHub Pages dashboard. One tap — full dashboard on any device.
    Falls back to plain summary if GitHub deploy was skipped.
    """
    if not ENABLE_EMAIL:
        print("📧 Email disabled.")
        return
    if not EMAIL_PASSWORD:
        print("❌ Set GMAIL_APP_PASSWORD environment variable first.")
        return
    if not EMAIL_RECIPIENTS:
        print("❌ No recipients defined in EMAIL_RECIPIENTS.")
        return
    try:
        # Add the live link at the top if deployed
        if deployed and GITHUB_TOKEN:
            link_block = (
                f"🌐 VIEW FULL DASHBOARD:\n"
                f"   {GITHUB_PAGES_URL}\n\n"
                f"Open the link on any device — phone, tablet, laptop.\n"
                f"Save it to your home screen for instant daily access.\n"
                f"{'─' * 48}\n\n"
            )
            body = link_block + plain_body
        else:
            body = plain_body

        msg = MIMEMultipart()
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = ", ".join(EMAIL_RECIPIENTS)
        msg["Subject"] = f"⚾ H-Bomb Picks — {date.today().strftime('%b %d, %Y')}"
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENTS, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {len(EMAIL_RECIPIENTS)} recipient(s).")
    except Exception as e:
        print(f"❌ Email failed: {e}")


# ============================================================
#  MAIN RUNNER
# ============================================================

# ============================================================
#  SETTLE PICKS
#  Runs at noon each day — fetches yesterday's final box scores
#  from MLB API, calculates actual H+R+RBI per player, and
#  updates each pending pick in Supabase as Hit or Miss.
# ============================================================

SUPABASE_URL = "https://hpoxotxejiilxzhxiuan.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhwb3hvdHhlamlpbHh6aHhpdWFuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0Nzg2MzgsImV4cCI6MjA5NTA1NDYzOH0.57oQLnh3Wv8n1F34OVsNvFdsklVktbKUeTlGDkq1X7s"

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

def sb_get(path):
    """GET from Supabase REST API."""
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=SB_HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"   ⚠ Supabase GET error: {e}")
        return None

def sb_patch(path, data):
    """PATCH (update) in Supabase REST API."""
    try:
        h = {**SB_HEADERS, "Prefer": "return=minimal"}
        r = requests.patch(f"{SUPABASE_URL}/rest/v1/{path}", headers=h,
                           json=data, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"   ⚠ Supabase PATCH error: {e}")
        return False

def sb_post(path, data):
    """POST (insert) into Supabase REST API. data may be a dict or list."""
    try:
        h = {**SB_HEADERS, "Prefer": "return=minimal"}
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{path}", headers=h,
                          json=data, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"   ⚠ Supabase POST error: {e}")
        return False


# ============================================================
#  MODEL ACCURACY TRACKING
#  Logs each night's projected H/R/RBI, then fills in the
#  actual results so projection error (MAE) can be measured
#  and the model tuned with evidence instead of guesses.
# ============================================================

def log_projections(all_players):
    """Save tonight's projected H/R/RBI for every player to Supabase."""
    today = date.today().strftime("%Y-%m-%d")
    rows = []
    for p in all_players:
        rows.append({
            "date":        today,
            "player_id":   p["id"],
            "player_name": p["name"],
            "team":        p.get("team", ""),
            "proj_h":      p.get("proj_h", 0),
            "proj_r":      p.get("proj_r", 0),
            "proj_rbi":    p.get("proj_rbi", 0),
            "proj_total":  p.get("proj_total", 0),
        })
    if not rows:
        return
    # Avoid duplicate rows if the report runs multiple times in a day
    existing = sb_get(f"projections?date=eq.{today}&select=player_id")
    have = {e["player_id"] for e in existing} if existing else set()
    rows = [r for r in rows if r["player_id"] not in have]
    if not rows:
        print("   📊 Projections already logged for today.")
        return
    if sb_post("projections", rows):
        print(f"   📊 Logged {len(rows)} projection(s) for accuracy tracking.")


def settle_projections():
    """Fill in actual H/R/RBI for past projections and report model error."""
    print(f"\n{'='*62}")
    print(f"📊  Settling projection accuracy...")
    print(f"{'='*62}")
    # Only attempt to settle the last few days. Older unsettled rows are almost
    # all bench players who never played and will NEVER settle — re-hitting the
    # API for all of them every run was costing minutes and starving the report.
    cutoff = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
    pending = sb_get(f"projections?actual_total=is.null&date=gte.{cutoff}&order=date.asc&limit=400")
    if not pending:
        print("   ✓ No recent projections to settle.")
        return
    today = date.today().strftime("%Y-%m-%d")
    # Each attempt costs an MLB API call + a Supabase write, so cap the work per
    # run. Three runs a day comfortably clears a day's ~270 projections; anything
    # left simply settles on the next run.
    MAX_ATTEMPTS_PER_RUN = 120
    settled, attempts, errs = 0, 0, []
    for proj in pending:
        pdate = proj.get("date", "")
        if pdate >= today:
            continue  # games not finished
        if attempts >= MAX_ATTEMPTS_PER_RUN:
            print(f"   ⏸ Hit {MAX_ATTEMPTS_PER_RUN}-per-run cap — rest settle next run.")
            break
        attempts += 1
        actual = get_player_hrr_on_date(proj["player_id"], pdate)
        if actual is None:
            continue  # DNP / not final yet
        a_h, a_r, a_rbi, a_total = actual
        ok = sb_patch(f"projections?id=eq.{proj['id']}", {
            "actual_h": a_h, "actual_r": a_r,
            "actual_rbi": a_rbi, "actual_total": a_total,
        })
        if ok:
            settled += 1
            errs.append(abs(safe_float(proj.get("proj_total")) - a_total))
    if errs:
        mae = sum(errs) / len(errs)
        print(f"   ✓ Settled {settled}/{attempts} attempted | Avg error (MAE): {mae:.2f} DK pts")
    else:
        print(f"   ✓ Settled {settled}/{attempts} attempted.")
    print(f"{'='*62}")


def line_to_threshold(line):
    """
    Convert a DK line string to the minimum HRR needed to win.
    'Over 1.5' → need 2 or more.
    """
    try:
        val = float(line.lower().replace("over", "").replace("★", "").strip().split()[0])
        return int(round(val))  # Over 2 → need 2+  (round handles legacy .5 lines)
    except Exception:
        return 2  # safe default

def get_player_hrr_on_date(player_id, target_date):
    """
    Fetch a player's actual H+R+RBI from the MLB Stats API game log
    for a specific date. Returns (hits, runs, rbi, total) or None if
    the player did not play that day (postponed, DNP, etc).
    Uses startDate/endDate to pin the query to the exact date, then sums
    across all splits (handles doubleheaders).
    """
    data = api_get(
        f"{MLB_API}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "hitting",
                "season": target_date[:4],
                "startDate": target_date, "endDate": target_date}
    )
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return None
        h = r = rbi = 0
        for split in splits:
            st   = split.get("stat", {})
            h   += int(st.get("hits", 0))
            r   += int(st.get("runs", 0))
            rbi += int(st.get("rbi",  0))
        return h, r, rbi, h + r + rbi
    except Exception:
        pass
    return None  # Player did not play this date


def settle_picks():
    """
    Called at noon each day. Settles ALL pending picks regardless of date —
    covers yesterday's games plus any postponements from earlier days.
    For each pending pick it looks up the player's game log on the pick date.
    If the player played → mark Hit or Miss.
    If they still haven't played → leave pending (will retry next noon run).
    """
    print(f"\n{'='*62}")
    print(f"⚾  Settling all pending picks...")
    print(f"{'='*62}")

    # Fetch every pending pick across all dates
    pending = sb_get("picks?result=eq.pending&order=date.asc")
    if not pending:
        print("   ✓ No pending picks to settle.")
        return

    print(f"   Found {len(pending)} pending pick(s) across all dates.")
    settled_count  = 0
    still_pending  = 0

    for pick in pending:
        player_id   = pick.get("player_id")
        player_name = pick.get("player_name", "Unknown")
        pick_date   = pick.get("date", "")
        line        = pick.get("line", "Over 1.5")
        who         = pick.get("who", "?")
        pick_id     = pick.get("id")
        threshold   = line_to_threshold(line)

        # Don't try to settle today's picks — games haven't finished yet
        if pick_date >= date.today().strftime("%Y-%m-%d"):
            still_pending += 1
            continue

        result_data = get_player_hrr_on_date(player_id, pick_date)

        if result_data is None:
            pick_date_obj = datetime.strptime(pick_date, "%Y-%m-%d").date()
            days_old = (date.today() - pick_date_obj).days
            if days_old >= 3:
                sb_patch(f"picks?id=eq.{pick_id}", {"result": "void", "actual": 0})
                print(f"   🚫 VOID: {player_name} ({pick_date}) — no game after {days_old} days (DNP/postponed)")
                settled_count += 1
            else:
                print(f"   ⏳ {player_name} ({pick_date}) — no game found yet, retrying tomorrow.")
                still_pending += 1
            continue

        h, r, rbi, total = result_data
        result = "hit" if total >= threshold else "miss"
        symbol = "✅" if result == "hit" else "❌"

        ok = sb_patch(
            f"picks?id=eq.{pick_id}",
            {"result": result, "actual": total}
        )

        if ok:
            print(f"   {symbol} {who} — {player_name} ({pick_date}) {line}: "
                  f"{h}H+{r}R+{rbi}RBI = {total} → {result.upper()}")
            settled_count += 1
        else:
            still_pending += 1

    print(f"\n   ✓ Settled: {settled_count}  |  ⏳ Still pending: {still_pending}")
    print(f"{'='*62}")


def now_et():
    """Current time in US Eastern. On GitHub Actions the runner clock is UTC,
    so datetime.now() there is 4-5 hrs off — always convert for anything we
    label 'ET'. Falls back to a fixed EDT offset if the tz database is absent."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.utcnow() - timedelta(hours=4)  # EDT (baseball season)


def run_report():
    print("\n" + "=" * 62)
    print("⚾  H-BOMB — Generating Daily HRR Report")
    print(f"    {now_et().strftime('%A, %B %d, %Y  %I:%M %p')} ET")
    print("=" * 62)

    generated_at = now_et().strftime("%I:%M %p ET")

    # Verify the schedule independently so we can tell "genuinely no games"
    # apart from "the API/network failed" — and never exit silently on failure.
    sched = api_get(f"{MLB_API}/schedule", params={"sportId": 1,
                    "date": date.today().strftime("%m/%d/%Y")})
    game_count = 0
    try:
        game_count = sum(d.get("totalGames", 0) for d in (sched or {}).get("dates", []))
    except Exception:
        game_count = 0

    all_players, games_meta, time_slates = run_pipeline()

    if not all_players:
        if game_count > 0:
            # Games exist but we got nothing — this is an API/network failure.
            print(f"!! {game_count} games scheduled but NO player data — API/network failure. NOT deploying.")
            notify("⚠️ H-Bomb: no data despite games",
                   f"{game_count} games scheduled today but the pipeline returned no players "
                   f"(API/network issue). Dashboard NOT updated — will retry next run.",
                   tags="warning", priority="high")
        else:
            print("No games scheduled today — nothing to deploy.")
            notify("⚾ H-Bomb: no games today",
                   "No MLB games scheduled — dashboard left as-is. (Normal on off-days.)",
                   tags="baseball", priority="high")
        return

    # Log projections for accuracy tracking
    log_projections(all_players)

    # Build outputs
    html_report  = build_html(all_players, games_meta, time_slates, generated_at)
    email_report = build_email_summary(all_players, games_meta)

    # Save files locally
    html_path = save_html(html_report)
    txt_path  = save_txt(email_report)

    # Deploy PWA support files (manifest + service worker)
    build_pwa_files()

    # Upload splash GIF once (skipped if already in repo)
    deploy_static_asset("phanatic.gif")

    # Deploy dashboard to GitHub Pages
    deployed = deploy_to_github(html_report, "index.html")

    # Confirm the live site actually reflects the push (not just the commit)
    if deployed:
        live_ok = verify_live_deploy()
        if live_ok and NTFY_NOTIFY_SUCCESS:
            top = all_players[0] if all_players else None
            if top:
                msg = (f"Top play: {top['name']} ({top['team']}) "
                       f"vs {top.get('opp_pitcher','TBD')}\n"
                       f"Proj {top.get('proj_h',0):.1f}H / {top.get('proj_r',0):.1f}R / "
                       f"{top.get('proj_rbi',0):.1f}RBI · {top.get('conf_grade','')}\n"
                       f"{GITHUB_PAGES_URL}")
            else:
                msg = f"Dashboard updated.\n{GITHUB_PAGES_URL}"
            notify("⚾ H-Bomb updated", msg, tags="baseball", priority="high")

    # Send email — with live URL if deployed, plain summary if not
    send_email(email_report, deployed=deployed)

    print(f"\n✅ HTML report saved: {html_path}")
    print(f"✅ TXT summary saved:  {txt_path}")
    if deployed:
        print(f"🌐 Live URL: {GITHUB_PAGES_URL}")

    # Auto-open local browser copy
    if AUTO_OPEN_BROWSER:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
        print("🌐 Opened in browser.")

    print("=" * 62)


import traceback

def safe(fn, label=""):
    """Run a job and swallow any exception so one bad run (e.g. a network
    outage) can never kill the scheduler. Logs the full traceback and moves on."""
    try:
        fn()
    except Exception as e:
        print(f"\n{'!'*62}")
        print(f"⚠ {label or fn.__name__} failed — caught, script stays alive.")
        traceback.print_exc()
        print(f"{'!'*62}\n")
        notify(f"⚠️ H-Bomb: {label or fn.__name__} failed",
               f"{type(e).__name__}: {str(e)[:200]}",
               tags="warning", priority="high")


if __name__ == "__main__":
    import sys

    # --once: run a single settle + report and exit (for Windows Task Scheduler).
    # No infinite loop, so each scheduled run is its own independent process —
    # a network blip in one run can never strand the others.
    if "--once" in sys.argv:
        AUTO_OPEN_BROWSER = False  # background task — don't pop a browser window

        # Backup scheduled runs pass --skip-if-recent. If the primary run for
        # this slot already deployed within the last ~40 min, the backup exits
        # immediately (no double work / double ping). It only does real work
        # when the primary was actually skipped by GitHub's cron throttling.
        if "--skip-if-recent" in sys.argv and GITHUB_TOKEN:
            try:
                from datetime import timezone
                _h = {"Authorization": f"token {GITHUB_TOKEN}",
                      "Accept": "application/vnd.github.v3+json"}
                _c = requests.get(
                    f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/commits",
                    params={"path": "index.html", "per_page": 1}, headers=_h, timeout=15).json()
                _last = datetime.strptime(_c[0]["commit"]["committer"]["date"],
                                          "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                _age = (datetime.now(timezone.utc) - _last).total_seconds() / 60
                if _age < 40:
                    print(f"Backup run: primary already deployed {_age:.0f} min ago — skipping.")
                    sys.exit(0)
                print(f"Backup run: last deploy was {_age:.0f} min ago — primary was skipped, running.")
            except Exception as e:
                print(f"skip-if-recent check failed ({e}) — running anyway to be safe.")

        # Watchdog: if a run ever stalls (e.g. laptop slept mid-run and a
        # socket got stuck), force-exit so it can't linger as a zombie and
        # block the next scheduled run. The next trigger recovers cleanly.
        import threading
        def _watchdog(limit_sec=1500):
            import time as _t
            _t.sleep(limit_sec)
            print(f"⏱ Watchdog: run exceeded {limit_sec}s — forcing exit.")
            notify("⏱️ H-Bomb run timed out",
                   f"Run exceeded {limit_sec//60} min and was killed. "
                   f"Today's dashboard may not have updated.",
                   tags="hourglass", priority="high")
            os._exit(1)
        threading.Thread(target=_watchdog, daemon=True).start()

        print(f"\n=== HBomb --once run: {datetime.now():%Y-%m-%d %H:%M:%S} ===")

        # DST reminder: US clocks fall back Sun Nov 1, 2026. After that the
        # GitHub Actions cron (set for EDT) fires an hour early until updated.
        # Ping the phone for a few days so the fix doesn't get forgotten.
        if "2026-11-01" <= date.today().strftime("%Y-%m-%d") <= "2026-11-04":
            notify("🕐 Fix H-Bomb cron (DST ended)",
                   "Daylight saving ended — the Actions runs now fire an hour early "
                   "(10am/2pm/5pm ET). Tell Claude to bump the cron by +1 hour.",
                   tags="alarm_clock", priority="high")

        # Report + deploy FIRST. Settling yesterday's results is bookkeeping and
        # can be slow (hundreds of API calls); it must never starve the deploy.
        # If the watchdog fires during settling, the dashboard is already live
        # and the remaining rows just settle on the next run.
        safe(run_report,         "run_report")
        safe(settle_picks,       "settle_picks")
        safe(settle_projections, "settle_projections")
        print(f"=== HBomb --once run complete ===\n")
        sys.exit(0)

    safe(settle_picks,       "settle_picks")
    safe(settle_projections, "settle_projections")
    safe(run_report,         "run_report")

    # 11:00 AM — settle yesterday + report (covers day game lineups, 1 PM starts)
    # 3:00 PM  — report (afternoon game lineups confirmed, 4 PM starts)
    # 6:00 PM  — report (evening game lineups confirmed, 7-8 PM starts)
    def noon_run():
        safe(settle_picks,       "settle_picks")
        safe(settle_projections, "settle_projections")
        safe(run_report,         "run_report")

    # Times are machine local time — ensure host clock is set to ET
    schedule.every().day.at("11:00").do(noon_run)
    schedule.every().day.at("15:00").do(lambda: safe(run_report, "run_report"))
    schedule.every().day.at("18:00").do(lambda: safe(run_report, "run_report"))
    print(f"\n⏰ Scheduled: 11:00 AM (settle + report), 3:00 PM, and 6:00 PM (report) ET.")
    print(f"⏰ Picks from yesterday will be auto-settled each day at 11 AM.")

    while True:
        try:
            schedule.run_pending()
        except Exception:
            print("⚠ Scheduler tick error — caught, continuing.")
            traceback.print_exc()
        time.sleep(30)
