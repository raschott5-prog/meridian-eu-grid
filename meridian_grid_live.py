#!/usr/bin/env python3
"""
MERIDIAN Grid Live  –  ENTSO-E-Kopplung für den zonalen Netzsimulator
=====================================================================

Verbindet meridian_grid.py mit echten ENTSO-E-Daten und hängt sich als
Blueprint into an existing MERIDIAN Energy Terminal application.

Integration in energy_trader_finale.py  (2 Zeilen, ganz unten vor app.run):

    import meridian_grid_live
    meridian_grid_live.register(app, entsoe_key=ENTSOE_KEY, http=HTTP)
    meridian_grid_live.start_background_loop()

Danach:
    http://localhost:5001/grid       → interaktive Netzkarte auf Live-Daten
    http://localhost:5001/api/grid   → JSON: Flüsse, Auslastung, Datenqualität


UNTERSCHIEDE zum Parsing im Bestands-Terminal (bewusst korrigiert)
-----------------------------------------------------------------
1) PUMPSPEICHER-VORZEICHEN
   A75 liefert Pumpbetrieb als eigene TimeSeries mit outBiddingZone_Domain.
   Der Bestandscode addiert diese zur Erzeugung. Für ein Signal ist das egal,
   für einen Lastfluss ist es ein Vorzeichenfehler (AT: bis 5,5 GW). Hier wird
   Pumpen als LAST verbucht – physikalisch korrekt.

2) KONSISTENTE ZEITSCHEIBE
   Statt "letzter Punkt je Produktionstyp" (die können auf verschiedenen
   Zeitstempeln liegen) wird pro Zone der jüngste Zeitstempel gewählt, für den
   ausreichend Produktionstypen vorliegen. Ein Lastfluss braucht einen
   konsistenten Schnitt, sonst mischt man 14:00-Wind mit 13:45-Kernkraft.

3) DATENHERKUNFT IST SICHTBAR
   Jede Zone trägt source = live | stale | default. Nie wird stillschweigend
   synthetischer Default als Realdatum ausgegeben – sonst rechnet man ein
   halb-echtes Netz und hält das Ergebnis für gemessen.


EHRLICHE GRENZEN DES MODELLS  (bitte mitlesen, bevor Zahlen zitiert werden)
--------------------------------------------------------------------------
• A75 erfasst nur meldepflichtige Einheiten (i. d. R. > 100 MW). Kleine PV und
  dezentrale Anlagen fehlen → Erzeugung wird systematisch unterschätzt.
• Das 15-Zonen-Modell ist KEIN geschlossenes System: reale Flüsse nach GB,
  Skandinavien, Balkan, UA und Marokko fehlen. Der Rest erscheint als
  external_balance_mw und wird vom verteilten Slack aufgefangen.
• Die Suszeptanzen sind Plausibilitätsannahmen, keine gemessenen Reaktanzen.
  Flussrichtungen und Umverteilungsmuster sind damit qualitativ belastbar,
  absolute MW-Werte NICHT. Kalibrierung gegen A11 ist der nächste Schritt.
"""

from __future__ import annotations
import os
import json
import time
import threading
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from meridian_grid import (GridModel, ZONES, INTERCONNECTORS, SYNC_ZONES,
                           n_minus_1, _ENTSOE_TO_CAT)

log = logging.getLogger("grid.live")

# ══════════════════════════════════════════════════════════════════════════════
# API KEY — never hardcode (repo is public)
# ══════════════════════════════════════════════════════════════════════════════
# Bezugsquellen in dieser Reihenfolge:
#   1. Umgebungsvariable ENTSOE_KEY   (GitHub Actions: Repository Secret)
#   2. .env-Datei neben diesem Modul  (lokal: cp .env.example .env, Key rein)
# Ohne Key läuft das Modell sichtbar auf Defaults – mit klarer Meldung.

def _load_dotenv():
    """Minimaler .env-Loader ohne Zusatzabhängigkeit."""
    for d in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        p = os.path.join(d, ".env")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass

_load_dotenv()
ENTSOE_KEY = os.environ.get("ENTSOE_KEY", "")

log = logging.getLogger("grid.live")

# ══════════════════════════════════════════════════════════════════════════════
# API-KEY
# ══════════════════════════════════════════════════════════════════════════════
# Default = der Key aus energy_trader_finale.py, damit das Modul ohne weitere
# Konfiguration läuft. Per Umgebungsvariable überschreibbar:
#     export ENTSOE_KEY="anderer-key"
# Hinweis: Wenn dieser Ordner je in ein öffentliches Repo wandert, den Key
# vorher in die Env verschieben – ENTSO-E-Keys sind personengebunden.

# ══════════════════════════════════════════════════════════════════════════════
# ZONEN-DOMAINS  (EIC-Codes)
# ══════════════════════════════════════════════════════════════════════════════
# Die ersten acht sind aus dem Bestands-Terminal übernommen (dort verifiziert
# funktionierend). Die sieben neuen sind Standard-EIC-Codes und sollten beim
# ersten Live-Lauf gegengeprüft werden – /api/grid zeigt pro Zone, ob Daten
# ankommen. Zonen ohne Daten fallen sichtbar auf source=default zurück.

ZONE_DOMAINS = {
    # verifiziert (Bestands-Terminal bzw. --check-Lauf am 23.07.)
    "AT": "10YAT-APG------L", "DE": "10Y1001A1001A83F",
    "CZ": "10YCZ-CEPS-----N", "SK": "10YSK-SEPS-----K",
    "HU": "10YHU-MAVIR----U", "CH": "10YCH-SWISSGRIDZ",
    "IT": "10YIT-GRTN-----B", "SI": "10YSI-ELES-----O",
    "FR": "10YFR-RTE------C", "NL": "10YNL----------L",
    "BE": "10YBE----------2", "PL": "10YPL-AREA-----S",
    "HR": "10YHR-HEP------M", "ES": "10YES-REE------0",
    "PT": "10YPT-REN------W",
    # neu für das EU-weite Modell – per --check gegen die echte API prüfen!
    "RO": "10YRO-TEL------P", "BG": "10YCA-BULGARIA-R",
    "GR": "10YGR-HTSO-----Y", "RS": "10YCS-SERBIATSOV",
    "BA": "10YBA-JPCC-----D", "ME": "10YCS-CG-TSO---S",
    "MK": "10YMK-MEPSO----8",   # Lauf 23.07.: liefert weder A75 noch A65
    "AL": "10YAL-KESH-----5",   # Lauf 23.07.: nur Last, keine Erzeugung
    "DK1": "10YDK-1--------W", "DK2": "10YDK-2--------M",
    "EE": "10Y1001A1001A39I", "LV": "10YLV-1001A00074",
    "LT": "10YLT-1001A0008Q",
    "NO1": "10YNO-1--------2", "NO2": "10YNO-2--------T",
    "NO3": "10YNO-3--------J", "NO4": "10YNO-4--------9",
    "NO5": "10Y1001A1001A48H",
    "SE1": "10Y1001A1001A44P", "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L", "SE4": "10Y1001A1001A47J",
    "FI": "10YFI-1--------U",
    "GB": "10YGB----------A",   # post-Brexit oft lückig → läuft dann default
    "IE": "10Y1001A1001A59C",   # SEM (gesamte Insel)
}

# Alternative Domains, falls die primäre keine Daten liefert.
ZONE_DOMAIN_FALLBACK = {
    "DE": "10Y1001A1001A82H",   # DE-LU Gebotszone
    "IT": "10Y1001A1001A73I",   # IT-Nord
    "IE": "10YIE-1001A00010",   # EirGrid-Area
}

ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
NS = {"ns": "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0"}

PSR_MAP = {
    "B01": "Biomass", "B02": "Fossil Brown Coal/Lignite",
    "B03": "Fossil Coal-derived Gas", "B04": "Fossil Gas",
    "B05": "Fossil Hard Coal", "B06": "Fossil Oil",
    "B09": "Geothermal", "B10": "Hydro Pumped Storage",
    "B11": "Hydro Run-of-river and poundage",
    "B12": "Hydro Water Reservoir", "B13": "Marine",
    "B14": "Nuclear", "B15": "Other renewable",
    "B16": "Solar", "B17": "Waste", "B18": "Wind Offshore",
    "B19": "Wind Onshore", "B20": "Other",
}

STALE_AFTER_MIN = 180      # ab hier gilt eine Zone als veraltet
REFRESH_INTERVAL = 900     # 15 Min – ENTSO-E veröffentlicht nicht schneller


# ══════════════════════════════════════════════════════════════════════════════
# XML-PARSING  (zeitreihen-basiert)
# ══════════════════════════════════════════════════════════════════════════════
# Es werden bewusst VOLLSTÄNDIGE Zeitreihen geparst, nicht nur der letzte Punkt.
# Grund: ENTSO-E veröffentlicht je Zone und je Produktionstyp mit sehr
# unterschiedlichem Verzug (real gemessen: 38 bis 248 Minuten). Wer pro Zone
# einzeln "den letzten Wert" nimmt, rechnet am Ende einen Lastfluss, in dem
# Zone A dreieinhalb Stunden vor Zone B liegt. Für ein Netz ist das sinnlos.
# Deshalb: erst alle Reihen holen, dann EINEN gemeinsamen Zeitpunkt wählen.

def _resolution_minutes(res: str) -> int:
    return {"PT15M": 15, "PT30M": 30, "PT60M": 60, "PT1H": 60}.get(res, 60)


def _parse_periods(ts_elem) -> dict[datetime, float]:
    """Alle Punkte einer TimeSeries als {timestamp_utc: MW}."""
    out: dict[datetime, float] = {}
    for period in ts_elem.findall(".//ns:Period", NS):
        start_el = period.find(".//ns:timeInterval/ns:start", NS)
        res_el = period.find("ns:resolution", NS)
        if start_el is None or not start_el.text:
            continue
        try:
            t0 = datetime.strptime(start_el.text, "%Y-%m-%dT%H:%MZ").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        step = _resolution_minutes(res_el.text if res_el is not None else "PT60M")
        for pt in period.findall("ns:Point", NS):
            pos_el = pt.find("ns:position", NS)
            qty_el = pt.find("ns:quantity", NS)
            if pos_el is None or qty_el is None:
                continue
            try:
                pos = int(pos_el.text)
                mw = float(qty_el.text)
            except (ValueError, TypeError):
                continue
            out[t0 + timedelta(minutes=step * (pos - 1))] = mw
    return out


def parse_generation_series(xml_bytes: bytes) -> tuple[dict, dict]:
    """
    A75 → (gen_series, pump_series)
      gen_series  = {Produktionstyp: {ts: MW}}
      pump_series = {ts: MW}   (Pumpbetrieb = Verbrauch, NICHT Erzeugung)

    Pumpbetrieb steht in TimeSeries mit outBiddingZone_Domain. Wird er zur
    Erzeugung addiert, entsteht im Lastfluss der doppelte Fehler: zu viel
    Erzeugung UND zu wenig Last (bei AT bis zu 3 GW).
    """
    root = ET.fromstring(xml_bytes)
    gen_series: dict[str, dict[datetime, float]] = {}
    pump_series: dict[datetime, float] = {}

    for ts in root.findall(".//ns:TimeSeries", NS):
        psr_el = ts.find(".//ns:psrType", NS)
        if psr_el is None:
            continue
        fuel = PSR_MAP.get(psr_el.text, psr_el.text)
        points = _parse_periods(ts)
        if not points:
            continue
        if ts.find(".//ns:outBiddingZone_Domain.mRID", NS) is not None:
            for t, mw in points.items():
                pump_series[t] = pump_series.get(t, 0.0) + mw
        else:
            bucket = gen_series.setdefault(fuel, {})
            for t, mw in points.items():
                bucket[t] = bucket.get(t, 0.0) + mw
    return gen_series, pump_series


def parse_load_series(xml_bytes: bytes) -> dict[datetime, tuple[float, int]]:
    """
    A65 → {ts: (Summe_MW, Anzahl_beitragender_Reihen)}

    Die Anzahl ist entscheidend: Italien meldet unter der nationalen Domain
    mehrere Gebotszonen (Nord, Mitte-Nord, Mitte-Süd, Süd, Sizilien, Sardinien)
    als separate TimeSeries. Nimmt man einfach den jüngsten Zeitstempel, hat
    dort oft erst EINE Teilzone gemeldet – gemessen ergab das 15,4 GW statt
    realistischer ~48 GW. Nur Zeitpunkte mit vollständiger Teilzonen-Abdeckung
    sind brauchbar.
    """
    root = ET.fromstring(xml_bytes)
    merged: dict[datetime, list] = {}
    for ts in root.findall(".//ns:TimeSeries", NS):
        for t, mw in _parse_periods(ts).items():
            e = merged.setdefault(t, [0.0, 0])
            e[0] += mw
            e[1] += 1
    return {t: (v[0], v[1]) for t, v in merged.items()}


# ── Rückwärtskompatible Kurzformen (nutzen die Reihen oben) ──────────────────

def parse_generation(xml_bytes: bytes) -> tuple[dict, float, datetime | None]:
    """A75 → (generation, pump_mw, ts) für den jüngsten gut abgedeckten Punkt."""
    gen_series, pump_series = parse_generation_series(xml_bytes)
    if not gen_series:
        return {}, 0.0, None
    n_types = len(gen_series)
    all_ts = sorted({t for s in gen_series.values() for t in s}, reverse=True)
    chosen = next((t for t in all_ts
                   if sum(1 for s in gen_series.values() if t in s)
                   >= max(1, int(n_types * 0.6))), all_ts[0])
    generation = {f: s[chosen] for f, s in gen_series.items() if chosen in s}
    return generation, pump_series.get(chosen, 0.0), chosen


def parse_load(xml_bytes: bytes) -> tuple[float | None, datetime | None]:
    """A65 → (load_mw, ts) für den jüngsten Punkt mit voller Teilzonen-Abdeckung."""
    series = parse_load_series(xml_bytes)
    if not series:
        return None, None
    max_n = max(n for _, n in series.values())
    full = [t for t, (_, n) in series.items() if n == max_n]
    t = max(full) if full else max(series)
    return series[t][0], t


# ══════════════════════════════════════════════════════════════════════════════
# ZEITLICHE AUSRICHTUNG  –  ein Netzschnitt, ein Zeitpunkt
# ══════════════════════════════════════════════════════════════════════════════

MAX_FILL_MIN = 240       # so weit darf ein Zonen-Schnitt vorgetragen werden
TARGET_COVERAGE = 0.70   # Anteil Zonen, der am Zielzeitpunkt frisch sein soll

# MW-GEWICHTETE VOLLSTÄNDIGKEIT
# Lehre aus dem Realdaten-Lauf vom 23.07., 11:26: Die alte Regel verlangte
# 90% der TYPEN gleichzeitig. Kleinkategorien (Waste, Geothermal, Marine, ...)
# melden aber mit stundenlangem Verzug – bei ES mit 21 Typen war deshalb der
# letzte "vollständige" Schnitt 8h alt, obwohl 98% der MEGAWATT topaktuell
# waren. Der 8h-Zielzeitpunkt warf dann sogar frische Zonen (DK1/DK2) auf
# default und ließ die A11-Kalibrierung ins Leere greifen (0/77 Grenzen).
# Neu: Ein Schnitt ist vollständig, wenn die FEHLENDEN Typen zusammen unter
# 5% der typischen Zonen-Erzeugung (mind. 150 MW) liegen. Fehlende KLEINE
# Typen werden einzeln begrenzt vorgetragen; ein fehlender GROSSER Typ
# (Kernkraft!) blockiert weiterhin – das ist der Anti-Phantom-Schutz.
MINOR_SHARE = 0.05
MINOR_FLOOR_MW = 150.0


def _typical_mw(gen_series: dict) -> tuple[dict, float]:
    """Median-MW je Typ über das Fenster + Summe (robust gegen Ausreißer)."""
    typ = {}
    for f, s in gen_series.items():
        vals = sorted(s.values())
        typ[f] = float(vals[len(vals) // 2]) if vals else 0.0
    return typ, sum(typ.values())


def _gen_ok_timestamps(gen_series: dict) -> set:
    """Zeitpunkte, an denen die fehlende Erzeugung unwesentlich ist (MW-gewichtet)."""
    if not gen_series:
        return set()
    typ, total = _typical_mw(gen_series)
    thresh = max(total * MINOR_SHARE, MINOR_FLOOR_MW)
    all_ts = {t for s in gen_series.values() for t in s}
    ok = set()
    for t in all_ts:
        missing = sum(typ[f] for f, s in gen_series.items() if t not in s)
        if missing <= thresh:
            ok.add(t)
    return ok


def _value_at(series: dict, target, max_fill: int) -> tuple:
    """Wert zum Zielzeitpunkt, sonst jüngster davor (bounded forward-fill)."""
    if target in series:
        return series[target], 0
    earlier = [t for t in series if t <= target]
    if not earlier:
        return None, -1
    t = max(earlier)
    off = int((target - t).total_seconds() / 60)
    if off > max_fill:
        return None, off
    return series[t], off


def _zone_latest_good(zd: dict):
    """Jüngster Zeitpunkt mit wesentlicher Erzeugung UND vollständiger Last."""
    gen_series = zd.get("gen_series") or {}
    load_series = zd.get("load_series") or {}
    if not gen_series or not load_series:
        return None
    gen_ok = _gen_ok_timestamps(gen_series)
    if not gen_ok:
        return None
    max_n = max(n for _, n in load_series.values())
    load_ok = {t for t, (_, n) in load_series.items() if n == max_n}
    both = gen_ok & load_ok
    return max(both) if both else None


def align_zone(zd: dict, target, max_fill: int = MAX_FILL_MIN) -> dict | None:
    """
    Bringt eine Zone als konsistenten Schnitt auf den Zielzeitpunkt.

    Regeln (Anti-Phantom bleibt, Nachzügler-Toleranz kommt dazu):
      • Der Schnitt liegt am jüngsten Zeitpunkt ≤ Ziel, an dem die WESENTLICHE
        Erzeugung (MW-gewichtet) und die volle Last vorliegen.
      • Fehlende KLEINE Typen (< 5% / 150 MW) werden einzeln aus ihrem letzten
        Wert vorgetragen (begrenzt) – ein träges Geothermie-Band darf die Zone
        nicht um Stunden zurückwerfen.
      • Fehlt ein GROSSER Typ, gilt der Zeitpunkt als unvollständig – lieber
        ein älterer kompletter Schnitt als eine halbe Erzeugung (BE-Lektion).
    """
    gen_series = zd.get("gen_series") or {}
    load_series = zd.get("load_series") or {}
    if not gen_series or not load_series:
        return None

    gen_ok = _gen_ok_timestamps(gen_series)
    max_n = max(n for _, n in load_series.values())
    load_full = {t: mw for t, (mw, n) in load_series.items() if n == max_n}

    candidates = [t for t in (gen_ok & set(load_full)) if t <= target]
    if not candidates:
        return None
    t = max(candidates)
    fill = int((target - t).total_seconds() / 60)
    if fill > max_fill:
        return None

    generation = {f: s[t] for f, s in gen_series.items() if t in s}
    # kleine Nachzügler einzeln vortragen
    typ, total = _typical_mw(gen_series)
    minor_thresh = max(total * MINOR_SHARE, MINOR_FLOOR_MW)
    for f, s in gen_series.items():
        if t in s or typ[f] > minor_thresh:
            continue
        v, off = _value_at(s, t, max_fill)
        if v is not None:
            generation[f] = v

    pump, _ = _value_at(zd.get("pump_series") or {}, t, max_fill)
    return {
        "generation": generation,
        "load_mw": load_full[t],
        "pump_mw": pump or 0.0,
        "ts": target.isoformat(),
        "slice_ts": t.isoformat(),
        "fill_min": fill,
        "load_subzones": max_n,
        "n_types": len(generation),
    }


def choose_target_ts(zones: dict, coverage: float = TARGET_COVERAGE) -> datetime | None:
    """
    Gemeinsamer Zielzeitpunkt für ALLE Zonen.
    Gewählt wird der jüngste Zeitpunkt, zu dem noch `coverage` der Zonen
    frische Daten haben – die übrigen werden begrenzt vorgetragen.
    """
    latest = [t for t in (_zone_latest_good(z) for z in zones.values()) if t]
    if not latest:
        return None
    latest.sort(reverse=True)
    idx = min(len(latest) - 1, max(0, int(len(latest) * coverage) - 1))
    return latest[idx]


# ══════════════════════════════════════════════════════════════════════════════
# FETCHER
# ══════════════════════════════════════════════════════════════════════════════

class EntsoeGridFetcher:
    """Holt Generation (A75) + Load (A65) für alle Modellzonen."""

    def __init__(self, api_key: str, http=None, url: str = ENTSOE_URL):
        self.key = api_key
        self.url = url
        if http is not None:
            self.http = http
        else:
            import requests
            self.http = requests.Session()
            self.http.headers.update({"User-Agent": "MERIDIAN-Grid/1.0"})

    def _window(self):
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=8)).replace(second=0, microsecond=0)
        start = start.replace(minute=(start.minute // 15) * 15)
        end = now.replace(second=0, microsecond=0)
        end = end.replace(minute=(end.minute // 15) * 15)
        f = "%Y%m%d%H%M"
        return start.strftime(f), end.strftime(f)

    def _get(self, params: dict) -> bytes | None:
        """
        Mit Retry gegen Drosselung. Lauf 23.07., 16:49: BG/RS fielen
        wiederholt aus, weil die Zonen in FESTER Reihenfolge abgerufen
        werden und die 429-Drosselung ab Request ~N systematisch dieselben
        hinteren Zonen trifft – der A11-Fetcher hatte den Retry, dieser
        hier noch nicht.
        """
        for attempt in range(3):
            try:
                r = self.http.get(self.url, params=params, timeout=25)
            except Exception as e:
                log.debug("ENTSO-E request failed: %s", e)
                time.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code == 200 and b"<GL_MarketDocument" in r.content:
                return r.content
            if r.status_code == 429 or r.status_code >= 500:
                log.debug("ENTSO-E HTTP %s — retrying", r.status_code)
                time.sleep(1.0 * (attempt + 1))
                continue
            return None      # 200-Acknowledgement (keine Daten) oder 4xx
        return None

    def fetch_zone(self, code: str) -> dict:
        """
        Eine Zone abrufen und die VOLLSTÄNDIGEN Zeitreihen zurückgeben.
        Die Auswahl des Zeitpunkts passiert bewusst erst später global
        (choose_target_ts), damit alle Zonen denselben Schnitt bekommen.
        """
        p_start, p_end = self._window()
        domains = [ZONE_DOMAINS.get(code)]
        if code in ZONE_DOMAIN_FALLBACK:
            domains.append(ZONE_DOMAIN_FALLBACK[code])

        gen_series, pump_series, used_domain = {}, {}, None
        for dom in [d for d in domains if d]:
            raw = self._get({
                "securityToken": self.key, "documentType": "A75",
                "processType": "A16", "in_Domain": dom, "out_Domain": dom,
                "periodStart": p_start, "periodEnd": p_end,
            })
            if raw:
                try:
                    gen_series, pump_series = parse_generation_series(raw)
                except ET.ParseError as e:
                    log.debug("%s A75 XML parse error: %s", code, e)
                    gen_series = {}
            if gen_series:
                used_domain = dom
                break
            time.sleep(0.2)

        load_series = {}
        for dom in [used_domain] + [d for d in domains if d and d != used_domain]:
            if not dom:
                continue
            raw = self._get({
                "securityToken": self.key, "documentType": "A65",
                "processType": "A16", "outBiddingZone_Domain": dom,
                "periodStart": p_start, "periodEnd": p_end,
            })
            if raw:
                try:
                    load_series = parse_load_series(raw)
                except ET.ParseError:
                    load_series = {}
            if load_series:
                break
            time.sleep(0.2)

        latest = None
        if gen_series and load_series:
            latest = _zone_latest_good({"gen_series": gen_series,
                                        "load_series": load_series})
        return {
            "zone": code,
            "ok": bool(gen_series and load_series),
            "gen_series": gen_series,
            "pump_series": pump_series,
            "load_series": load_series,
            "domain": used_domain,
            "latest_good": latest.isoformat() if latest else None,
            "n_types": len(gen_series),
            "load_subzones": (max(n for _, n in load_series.values())
                              if load_series else 0),
        }

    def fetch_all(self, codes=None, pause: float = 0.3, workers: int = 4) -> dict:
        """
        Alle Zonen abrufen (40 × 2 Requests, parallel mit wenigen Workern).
        Die Reihenfolge wird GEMISCHT: Falls die 429-Drosselung ab Request
        ~N zuschlägt, trifft sie so jeden Zyklus andere Zonen – zusammen
        mit dem Serien-Behalt in STATE.update heilt sich das über die
        Zyklen, statt dass immer dieselben hinteren Zonen (BG/RS …)
        ausfallen (Lauf 23.07., 16:49).
        """
        import random
        codes = list(codes or ZONE_DOMAINS.keys())
        random.shuffle(codes)
        out = {}
        if workers <= 1:
            for c in codes:
                out[c] = self.fetch_zone(c)
                time.sleep(pause)
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for c, d in zip(codes, ex.map(self.fetch_zone, codes)):
                    out[c] = d
        ok = [c for c, d in out.items() if d["ok"]]
        failed = [c for c, d in out.items() if not d["ok"]]
        log.info("ENTSO-E Grid: %d/%d zones live%s",
                 len(ok), len(codes),
                 (" | keine Daten: " + ", ".join(failed)) if failed else "")
        return out


# ══════════════════════════════════════════════════════════════════════════════
# A11  –  GEMESSENE GRENZFLÜSSE  +  KALIBRIERUNG
# ══════════════════════════════════════════════════════════════════════════════
# A11 "Physical Flows" ist die Messung dessen, was das Modell berechnet: der
# tatsächliche Fluss über jede Grenze. Damit lässt sich das Modell dreifach
# an die Realität heften:
#
#   1. DC-SOLLWERTE ÜBERNEHMEN. HVDC-Kabel folgen keinem Kirchhoff – ihre
#      Messung IST der Sollwert. Kein Fit nötig, direkt setzen.
#   2. AC-SUSZEPTANZEN FITTEN. Modellfluss vs. Messfluss je AC-Grenze →
#      Suszeptanzen iterativ nachziehen (gedämpft, mit Schranken), bis die
#      Verteilung der Messung entspricht.
#   3. UNMODELLIERTE INJEKTION JE ZONE. Die Messflüsse implizieren die
#      PHYSISCHE Netto-Position je Zone (Summe der gemessenen Abflüsse).
#      Die Differenz zur A75/A65-Bilanz wird als korrigierende Injektion
#      angesetzt und je Zone ausgewiesen. Inhaltlich ist das die Summe aus:
#      Kleinanlagen unter der A75-Meldeschwelle, Austausch mit Nachbarn
#      außerhalb des Modells (UA, MA, TR, ...) und momentaner Regelleistung.
#      WICHTIG: Diese drei Anteile sind aus Flussmessungen allein
#      mathematisch NICHT trennbar (die Trennung ist unteridentifiziert) –
#      deshalb wird ehrlich nur ihre Summe geführt. Für den Zweck reicht
#      das exakt: der Basiszustand reproduziert die Messung, Schocks sind
#      Deltas darauf.
#
# Ergebnis: Der Basiszustand reproduziert die gemessenen Flüsse; Szenario-
# Schocks sind dann Deltas auf einem realitätsverankerten Zustand.

A11_NS = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0"}


def parse_flow_series(xml_bytes: bytes) -> dict[datetime, float]:
    """A11 → {ts: MW} (eine Richtung einer Grenze)."""
    root = ET.fromstring(xml_bytes)
    out: dict[datetime, float] = {}
    for ts in root.findall(".//ns:TimeSeries", A11_NS):
        for period in ts.findall(".//ns:Period", A11_NS):
            start_el = period.find(".//ns:timeInterval/ns:start", A11_NS)
            res_el = period.find("ns:resolution", A11_NS)
            if start_el is None or not start_el.text:
                continue
            try:
                t0 = datetime.strptime(start_el.text, "%Y-%m-%dT%H:%MZ").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                continue
            step = _resolution_minutes(res_el.text if res_el is not None else "PT60M")
            for pt in period.findall("ns:Point", A11_NS):
                pos_el = pt.find("ns:position", A11_NS)
                qty_el = pt.find("ns:quantity", A11_NS)
                if pos_el is None or qty_el is None:
                    continue
                try:
                    t = t0 + timedelta(minutes=step * (int(pos_el.text) - 1))
                    out[t] = out.get(t, 0.0) + float(qty_el.text)
                except (ValueError, TypeError):
                    continue
    return out


class FlowFetcher:
    """Holt A11 (Physical Flows) für alle Modellgrenzen, beide Richtungen."""

    def __init__(self, api_key: str, http=None, url: str = ENTSOE_URL):
        self.key = api_key
        self.url = url
        self.stats = {"ok": 0, "throttled": 0, "empty": 0, "error": 0}
        if http is not None:
            self.http = http
        else:
            import requests
            self.http = requests.Session()
            self.http.headers.update({"User-Agent": "MERIDIAN-Grid/2.0"})

    def _get(self, params: dict) -> bytes | None:
        """
        Mit Retry: ENTSO-E drosselt bei ~400 Requests/min. Unser Refresh
        (80 Zonen- + 166 A11-Requests) kratzt an dieser Grenze – Realdaten-
        Lauf 23.07., 15:01: A11 brach von 76 auf 33 Grenzen ein. Transiente
        429/5xx werden daher 2× mit Backoff wiederholt und gezählt.
        """
        for attempt in range(3):
            try:
                r = self.http.get(self.url, params=params, timeout=25)
            except Exception:
                self.stats["error"] += 1
                time.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code == 200 and b"Publication_MarketDocument" in r.content:
                self.stats["ok"] += 1
                return r.content
            if r.status_code == 429 or r.status_code >= 500:
                self.stats["throttled"] += 1
                time.sleep(1.0 * (attempt + 1))   # Backoff, dann Retry
                continue
            self.stats["empty"] += 1              # 200 ohne Daten / 400er
            return None
        return None

    def fetch_border(self, a: str, b: str, p_start: str, p_end: str
                     ) -> dict[datetime, float]:
        """Netto-Fluss a→b = Fluss(a→b) − Fluss(b→a), je Zeitstempel."""
        da, db = ZONE_DOMAINS.get(a), ZONE_DOMAINS.get(b)
        if not da or not db:
            return {}
        net: dict[datetime, float] = {}
        for out_d, in_d, sign in ((da, db, +1.0), (db, da, -1.0)):
            raw = self._get({
                "securityToken": self.key, "documentType": "A11",
                "out_Domain": out_d, "in_Domain": in_d,
                "periodStart": p_start, "periodEnd": p_end,
            })
            if not raw:
                continue
            try:
                for t, mw in parse_flow_series(raw).items():
                    net[t] = net.get(t, 0.0) + sign * mw
            except ET.ParseError:
                continue
            time.sleep(0.15)
        return net

    def fetch_all(self, hours: int = 12, workers: int = 3) -> dict:
        """
        Alle Modellgrenzen. Rückgabe: {(a,b,label): {ts: net_mw}}.
        DC-Parallelkabel derselben Grenze (GB–IE: EWIC + Moyle) lassen sich in
        A11 nicht trennen – die Messung wird dem ersten zugeordnet, das zweite
        auf 0 gesetzt (Summe stimmt; Aufteilung ist ohnehin Betreiberwahl).
        """
        self.stats = {"ok": 0, "throttled": 0, "empty": 0, "error": 0}
        now = datetime.now(timezone.utc)
        f = "%Y%m%d%H%M"
        p_start = (now - timedelta(hours=hours)).strftime(f)
        p_end = now.strftime(f)

        borders, seen = [], set()
        for ln in INTERCONNECTORS:
            key = (ln.a, ln.b)
            first = key not in seen
            seen.add(key)
            borders.append((ln.a, ln.b, ln.label or None, first))

        results = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {}
            for a, b, label, first in borders:
                if first:
                    futs[(a, b)] = ex.submit(self.fetch_border, a, b,
                                             p_start, p_end)
            fetched = {k: fu.result() for k, fu in futs.items()}
        for a, b, label, first in borders:
            results[(a, b, label)] = fetched[(a, b)] if first else {}
        n_ok = sum(1 for k, v in results.items() if v)
        log.info("A11: %d/%d borders with measurements | Requests: %d ok, "
                 "%d gedrosselt, %d leer, %d Fehler",
                 n_ok, len(results), self.stats["ok"], self.stats["throttled"],
                 self.stats["empty"], self.stats["error"])
        return results


def measured_at(flows: dict, target: datetime,
                max_fill: int = 240) -> dict:
    """
    Messflüsse auf den Netzschnitt-Zeitpunkt bringen (bounded fill).
    240 min Vortrag: Einige Balkan-TSOs melden A11 mit Stunden Verzug.
    Ein 2–4 h alter GEMESSENER Fluss ist für Pinning und Fit immer noch
    deutlich besser als gar keiner (sonst: Zone ungepinnt → Phantom-
    flüsse, Lauf 16:49: "10 ohne Messung" → 140% im Balkan). Der Fit
    ist gedämpft; der Fehler bleibt begrenzt und wird ausgewiesen.
    """
    out = {}
    for key, series in flows.items():
        if not series:
            continue
        v, off = _value_at(series, target, max_fill)
        if v is not None:
            out[key] = v
    return out


class Calibration:
    """
    Hält den Kalibrierzustand und wendet ihn auf ein GridModel an.
      dc_setpoints      {(a,b,label): MW}   aus A11 direkt
      suscept_scale     {(a,b): Faktor}     aus iterativem Fit
      zone_adjust       {zone: MW}          unmodellierte Injektion
      fit_report        Liste je AC-Grenze: Modell vs. Messung
    """

    # Fit-Schranken: bewusst weit. Die Basis-Suszeptanzen sind grobe Proxys;
    # der Realdaten-Lauf 11:50 zeigte Grenzen, deren wahre Kopplung um Faktor
    # >6 von der Annahme abwich (RS-BG). Die Dämpfung (0.5/Iteration) und der
    # Richtungs-Guard verhindern Überanpassung an Messrauschen.
    SCALE_MIN, SCALE_MAX = 0.15, 8.0
    DAMP = 0.5                            # Dämpfung je Iteration
    ITERATIONS = 12

    NTC_HEADROOM = 1.10   # gemessenes Maximum + 10% = empirische Untergrenze
    #   des wahren Limits. Kein TSO fährt dauerhaft am thermischen Limit;
    #   was gemessen floss, geht sicher – und noch etwas mehr.

    def __init__(self):
        self.dc_setpoints: dict = {}
        self.suscept_scale: dict = {}
        self.zone_adjust: dict = {}
        self.ntc_raise: dict = {}       # (a,b,label) -> angehobene NTC MW
        self.unmeasured: list = []      # Grenzen ohne A11 (GB-Kabel etc.)
        self.unpinned: list = []        # Zonen ohne Bilanz-Pinning (Verdächtige!)
        self.last_summary: dict = {}
        self.fit_report: list = []
        self.ts: datetime | None = None
        self.n_measured = 0

    def apply(self, g: GridModel):
        for (a, b, label), mw in self.dc_setpoints.items():
            g.set_dc(a, b, mw, label)
        g.suscept_scale = dict(self.suscept_scale)      # vom Modell gelesen
        g.ntc_override = dict(self.ntc_raise)
        for z, mw in self.zone_adjust.items():
            if z in ZONES:
                g.zone_injection[z] = mw

    def calibrate_limits(self, flow_series: dict):
        """
        Empirische Limit-Kalibrierung: Für jede Grenze das Betrags-Maximum
        über das A11-Fenster. Liegt es über der angenommenen NTC, ist die
        Annahme WIDERLEGT (Lauf 23.07., 11:50: BG→RS 1405 MW über einer
        geratenen 600er-NTC → "234% Auslastung" im Startzustand – physisch
        unmöglich; nicht der Fluss war falsch, sondern das Limit).
        Die NTC wird auf max_gemessen × HEADROOM angehoben. Nie abgesenkt
        UND NIE VERGESSEN (Lauf 15:01: nach einem A11-Drossel-Aussetzer war
        BG–RS wieder bei "188%", weil clear() das gelernte 1550er-Limit
        verwarf – dass eine Grenze 1405 MW getragen hat, ist ein Fakt, der
        nicht verfällt, nur weil die Messung gerade klemmt).
        """
        by_border: dict = {}
        for (a, b, label), series in flow_series.items():
            if not series:
                continue
            m = max(abs(v) for v in series.values())
            key = (a, b, label)
            by_border[key] = max(by_border.get(key, 0.0), m)
        for ln in INTERCONNECTORS:
            key = (ln.a, ln.b, ln.label or None)
            m = by_border.get(key)
            if m and m > ln.ntc * 0.97:
                # Auch Dauerbetrieb AM Limit (MONITA: exakt 600/600 gemessen)
                # belegt: das wahre Limit liegt nicht darunter.
                new = float(round(max(m, ln.ntc) * self.NTC_HEADROOM))
                self.ntc_raise[key] = max(self.ntc_raise.get(key, 0.0), new)
        return self.ntc_raise

    # ── Kern: Kalibrieren gegen einen Mess-Schnitt ────────────────────────────
    def calibrate(self, measured: dict, target: datetime) -> dict:
        """
        measured: {(a,b,label): net_mw} am Zielzeitpunkt (a→b positiv).
        Reihenfolge ist wichtig:
          (1) DC-Sollwerte setzen (beeinflussen die Zonen-Injektionen)
          (2) Zonen-Anpassung aus Mess-Nettopositionen (schließt die Bilanz)
          (3) Suszeptanz-Fit auf die AC-Flüsse
        """
        self.ts = target
        self.n_measured = len(measured)
        self.dc_setpoints.clear()
        self.zone_adjust.clear()
        # suscept_scale wird NICHT geleert: Der Fit ist iterativ – er setzt
        # auf den zuletzt gelernten Faktoren auf. Grenzen, die diesmal keine
        # Messung liefern, behalten ihren letzten Fit statt auf die grobe
        # 1.0-Annahme zurückzufallen (gleiche Lektion wie bei den Limits).
        self.fit_report = []
        # ntc_raise wird von calibrate_limits() separat gepflegt (12h-Maxima)

        # (1) DC direkt aus Messung
        for ln in INTERCONNECTORS:
            key = (ln.a, ln.b, ln.label or None)
            if ln.kind == "DC" and key in measured:
                self.dc_setpoints[key] = measured[key]

        # (2) Unmodellierte Injektion je Zone:
        #     gemessene Netto-Position − Modell-Netto-Position
        #
        #     Realdaten-Lektion (Lauf 23.07., 12:15): Die 7 GB-Kabel liefern
        #     kein A11 (post-Brexit). Die frühere Alles-oder-nichts-Regel
        #     ließ dadurch FR/BE/NL/DK1/NO2/IE/GB ungepinnt – zwischen lauter
        #     gepinnten Nachbarn. Der Abgleichfehler konzentrierte sich
        #     sichtbar auf Norwegens Binnengrenzen (NO3–NO5 "194%"), weil
        #     NO2 als großer Kabel-Exporteur ungepinnt dazwischen hing.
        #
        #     Neu: Unbemessene DC-KABEL zählen mit ihrem SOLLWERT als
        #     abgedeckt – ein Sollwert ist im Modell ohnehin gesetzt, und
        #     der (ehrlich unbekannte) Fehler landet damit in der Zonen-
        #     korrektur der Kabel-Endpunkte statt in fremden AC-Leitungen.
        #     Nur unbemessene AC-Grenzen verhindern weiterhin das Pinning –
        #     dort wissen wir wirklich nichts.
        meas_net = {c: 0.0 for c in ZONES}
        meas_cov = {c: 0 for c in ZONES}
        n_borders = {c: 0 for c in ZONES}
        self.unmeasured = []
        for ln in INTERCONNECTORS:
            n_borders[ln.a] += 1
            n_borders[ln.b] += 1
            key = (ln.a, ln.b, ln.label or None)
            if key in measured:
                flow = measured[key]
            elif ln.kind == "DC":
                # Sollwert-Annahme statt Messung – dokumentiert unbemessen
                flow = self.dc_setpoints.get(key, ln.setpoint)
                self.unmeasured.append({"a": ln.a, "b": ln.b,
                                        "label": ln.label or None,
                                        "kind": "DC",
                                        "assumed_mw": int(round(flow))})
            else:
                self.unmeasured.append({"a": ln.a, "b": ln.b, "label": None,
                                        "kind": "AC", "assumed_mw": None})
                continue
            meas_net[ln.a] += flow
            meas_net[ln.b] -= flow
            meas_cov[ln.a] += 1
            meas_cov[ln.b] += 1

        self.unpinned = []
        for c, z in ZONES.items():
            if n_borders[c] == 0 or meas_cov[c] < n_borders[c]:
                self.unpinned.append(c)
                continue
            model_net = z.base_gen - z.base_load
            self.zone_adjust[c] = meas_net[c] - model_net

        # (3) Suszeptanz-Fit (iterativ, gedämpft)
        ac_meas = {(a, b): mw for (a, b, label), mw in measured.items()
                   if label is None}
        g = GridModel()
        self.apply(g)
        for _ in range(self.ITERATIONS):
            res = g.solve()
            changed = False
            model_flows = {(f["a"], f["b"]): f["flow_mw"] for f in res["flows"]
                           if f["kind"] == "AC"}
            for key, m in ac_meas.items():
                f = model_flows.get(key)
                if f is None or abs(f) < 30 or abs(m) < 30:
                    continue
                if f * m < 0:
                    continue    # Richtungswiderspruch: nicht per Skalierung fitten
                ratio = abs(m) / abs(f)
                cur = self.suscept_scale.get(key, 1.0)
                new = cur * (ratio ** self.DAMP)
                new = max(self.SCALE_MIN, min(self.SCALE_MAX, new))
                if abs(new - cur) > 1e-3:
                    self.suscept_scale[key] = new
                    changed = True
            g = GridModel()
            self.apply(g)
            if not changed:
                break

        # Abschlussbericht: Modell vs. Messung
        res = g.solve()
        model_flows = {(f["a"], f["b"]): f for f in res["flows"]}
        err_abs = []
        for (a, b, label), m in measured.items():
            f = model_flows.get((a, b))
            if not f:
                continue
            e = f["flow_mw"] - m
            err_abs.append(abs(e))
            self.fit_report.append({
                "a": a, "b": b, "label": label, "kind": f["kind"],
                "measured_mw": int(round(m)),
                "model_mw": f["flow_mw"],
                "error_mw": int(round(e)),
                "scale": round(self.suscept_scale.get((a, b), 1.0), 2)
                         if f["kind"] == "AC" else None,
            })
        self.fit_report.sort(key=lambda r: -abs(r["error_mw"]))
        self.last_summary = {
            "n_measured": self.n_measured,
            "n_dc_set": len(self.dc_setpoints),
            "n_scaled": len(self.suscept_scale),
            "n_zone_adjust": len(self.zone_adjust),
            "mean_abs_error_mw": int(sum(err_abs) / len(err_abs)) if err_abs else None,
            "max_abs_error_mw": max(err_abs) if err_abs else None,
        }
        return self.last_summary


CALIBRATION = Calibration()
_FLOW_FETCHER: "FlowFetcher | None" = None
_FLOW_SERIES: dict = {}


# ══════════════════════════════════════════════════════════════════════════════
# LIVE-STATE  (thread-sicher, wie im Bestands-Terminal per RLock)
# ══════════════════════════════════════════════════════════════════════════════

class LiveGridState:
    def __init__(self):
        self._lock = threading.RLock()
        self._zones: dict[str, dict] = {}      # code -> Rohreihen des letzten Abrufs
        self._last_refresh: datetime | None = None
        self._target_ts: datetime | None = None
        self._defaults = {c: (dict(z.gen), z.base_load) for c, z in ZONES.items()}

    def update(self, fetched: dict):
        with self._lock:
            for code, d in fetched.items():
                if d.get("ok"):
                    self._zones[code] = d
            self._last_refresh = datetime.now(timezone.utc)

    def snapshot(self) -> tuple[dict, datetime | None]:
        with self._lock:
            return dict(self._zones), self._last_refresh

    def apply_to_model(self) -> dict:
        """
        Richtet ALLE Zonen auf einen gemeinsamen Zeitpunkt aus und spielt sie
        ins Modell ein. Meldet je Zone die Herkunft und wie weit ihr Wert für
        den gemeinsamen Schnitt vorgetragen werden musste.

        Pumpbetrieb wird der Last zugeschlagen – Pumpen ist Verbrauch.
        """
        live, _ = self.snapshot()
        now = datetime.now(timezone.utc)
        target = choose_target_ts(live) if live else None
        with self._lock:
            self._target_ts = target

        zones_meta = {}
        for code, z in ZONES.items():
            zd = live.get(code)
            aligned = align_zone(zd, target) if (zd and zd.get("ok") and target) else None

            if aligned:
                g = {k: 0.0 for k in
                     ("nuclear", "coal", "gas", "hydro", "wind", "solar",
                      "biomass", "other")}
                for typ, mw in aligned["generation"].items():
                    g[_ENTSOE_TO_CAT.get(typ, "other")] += float(mw or 0)
                z.gen = g
                z.base_gen = sum(g.values())
                z.base_load = float(aligned["load_mw"]) + float(aligned["pump_mw"])
                age = int((now - target).total_seconds() / 60)
                zones_meta[code] = {
                    "source": "live" if age <= STALE_AFTER_MIN else "stale",
                    "age_min": age,
                    "ts": aligned["ts"],
                    "fill_min": aligned["fill_min"],
                    "pump_mw": round(aligned["pump_mw"]),
                    "load_subzones": aligned["load_subzones"],
                }
            else:
                gen_def, load_def = self._defaults[code]
                z.gen = dict(gen_def)
                z.base_gen = sum(gen_def.values())
                z.base_load = load_def
                reason = "keine Daten"
                if zd and zd.get("ok") and target:
                    reason = f"kein Wert innerhalb {MAX_FILL_MIN} min um Zielzeitpunkt"
                zones_meta[code] = {"source": "default", "age_min": None,
                                    "ts": None, "fill_min": None,
                                    "pump_mw": 0, "reason": reason}
        return zones_meta

    @property
    def target_ts(self):
        with self._lock:
            return self._target_ts


STATE = LiveGridState()

# Guards against concurrent ZONES mutation + solve().
# apply_to_model() mutates global Zone objects (z.gen, z.base_load, …);
# GridModel.solve() reads them. Without this lock, two parallel Flask
# threads can race on the same Zone fields.
_GRID_LOCK = threading.Lock()
_FETCHER: EntsoeGridFetcher | None = None


# ══════════════════════════════════════════════════════════════════════════════
# LÖSEN + DATENQUALITÄT
# ══════════════════════════════════════════════════════════════════════════════

def solve_live(shocks: dict | None = None) -> dict:
    """
    Run DC load flow on the current live-calibrated state.
    Optional shocks (from frontend):
      {"gen": {"FR": {"nuclear": -20000}},
       "load": {"PL": 5000},
       "line": {"DE-AT": 0.5}}

    _GRID_LOCK is held for the duration: apply_to_model() mutates global
    Zone objects; solve() reads them. The lock prevents concurrent requests
    from racing on the same Zone fields in threaded Flask.
    """
    with _GRID_LOCK:
        meta = STATE.apply_to_model()
        g = GridModel()

        # Apply calibration if it matches the current grid snapshot timestamp
        cal_active = False
        tgt = STATE.target_ts
        if CALIBRATION.ts and tgt and abs((CALIBRATION.ts - tgt).total_seconds()) <= 3600:
            CALIBRATION.apply(g)
            cal_active = True

        # Frequency reference: unperturbed (calibrated only) state = 50.000 Hz.
        # The data residual balance is a measurement artefact, not a frequency deviation.
        gref = GridModel()
        if cal_active:
            CALIBRATION.apply(gref)
        rref = gref.solve()
        g.freq_reference = {c: float(z["reg_mw"]) for c, z in rref["zones"].items()}

        if shocks:
            for zone, cats in (shocks.get("gen") or {}).items():
                for cat, delta in cats.items():
                    g.shock_generation(zone, cat, float(delta))
            for zone, delta in (shocks.get("load") or {}).items():
                g.shock_load(zone, float(delta))
            for key, factor in (shocks.get("line") or {}).items():
                a, _, b = key.partition("-")
                if a and b:
                    g.shock_line(a, b, float(factor))

        res = g.solve()
    # _GRID_LOCK released — ZONES are no longer being read or written

    # Offene Bilanz gegenüber Nachbarn außerhalb des Modells (GB, Nordics,
    # Balkan, UA, MA) + Meldelücken in A75. Wird vom verteilten Slack getragen –
    # hier explizit ausgewiesen, damit man weiß, wie viel "unerklärt" ist.
    scheduled = sum(z["net_mw"] for z in res["zones"].values())
    n_live = sum(1 for m in meta.values() if m["source"] == "live")

    fills_q = [m["fill_min"] for m in meta.values() if m.get("fill_min") is not None]
    worst_fill = max(fills_q) if fills_q else 0
    if abs(scheduled) > 15000 or n_live < 6 or worst_fill > 180:
        quality = "poor"
    elif abs(scheduled) > 6000 or n_live < 11 or worst_fill > 90:
        quality = "partial"
    else:
        quality = "ok"

    _, last_refresh = STATE.snapshot()
    tgt = STATE.target_ts
    fills = [m["fill_min"] for m in meta.values() if m.get("fill_min") is not None]
    res["live"] = {
        "calibrated": cal_active,
        "calibration": ({
            "ts": CALIBRATION.ts.isoformat() if CALIBRATION.ts else None,
            "n_measured": CALIBRATION.n_measured,
            "n_dc_set": len(CALIBRATION.dc_setpoints),
            "n_scaled": len(CALIBRATION.suscept_scale),
            "n_zone_adjust": len(CALIBRATION.zone_adjust),
            "n_ntc_raised": len(CALIBRATION.ntc_raise),
            "mean_abs_error_mw": CALIBRATION.last_summary.get("mean_abs_error_mw"),
            "n_unmeasured": len(CALIBRATION.unmeasured),
            "unpinned_zones": CALIBRATION.unpinned,
            "zone_adjust_gw": {z: round(v / 1000, 2)
                               for z, v in sorted(CALIBRATION.zone_adjust.items(),
                                                  key=lambda kv: -abs(kv[1]))[:12]},
        } if cal_active else None),
        "target_ts": tgt.isoformat() if tgt else None,
        "max_fill_min": max(fills) if fills else None,
        "zones_meta": meta,
        "zones_live": n_live,
        "zones_total": len(ZONES),
        "external_balance_mw": round(scheduled),
        "quality": quality,
        "last_refresh": last_refresh.isoformat() if last_refresh else None,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "caveat": ("Suszeptanzen sind Annahmen, nicht gemessene Reaktanzen. "
                   "Flussrichtungen und Umverteilung sind belastbar, "
                   "absolute MW-Werte nicht."),
    }
    # Zonendaten für das Frontend (Ist-Erzeugung je Kategorie)
    res["zone_input"] = {
        c: {"gen": dict(z.gen), "load": round(z.base_load), "name": z.name,
            "sync": z.sync}
        for c, z in ZONES.items()
    }
    # Kalibrierten Zustand mitgeben, damit der Browser-Solver identisch rechnet
    if cal_active:
        res["dc_setpoints"] = {
            f"{a}-{b}" + (f" [{l}]" if l else ""): int(v)
            for (a, b, l), v in CALIBRATION.dc_setpoints.items()}
        res["suscept_scale"] = {f"{a}-{b}": round(v, 3)
                                for (a, b), v in CALIBRATION.suscept_scale.items()}
        res["zone_injection"] = {z: int(v)
                                 for z, v in CALIBRATION.zone_adjust.items()}
        res["ntc_override"] = {
            f"{a}-{b}" + (f" [{l}]" if l else ""): int(v)
            for (a, b, l), v in CALIBRATION.ntc_raise.items()}
    return res


def init_fetchers(entsoe_key: str | None = None, http=None):
    """Fetcher ohne Flask initialisieren (Snapshot-/Skriptbetrieb)."""
    global _FETCHER, _FLOW_FETCHER
    key = entsoe_key or ENTSOE_KEY
    _FETCHER = EntsoeGridFetcher(key, http=http)
    _FLOW_FETCHER = FlowFetcher(key, http=http)


def refresh_now(codes=None, with_flows: bool = True) -> dict:
    """
    Einmaliger Abruf: A75/A65 je Zone, optional A11 je Grenze, danach
    Kalibrierung auf den gemeinsamen Netzschnitt.
    """
    global _FLOW_SERIES
    if _FETCHER is None:
        raise RuntimeError("register() muss zuerst aufgerufen werden")
    fetched = _FETCHER.fetch_all(codes)
    STATE.update(fetched)

    if with_flows and _FLOW_FETCHER is not None:
        try:
            fresh = _FLOW_FETCHER.fetch_all()
        except Exception:
            log.warning("A11 fetch failed", exc_info=True)
            fresh = {}
        # FORTSCHREIBEN statt ersetzen: Ein Drossel-Aussetzer (Lauf 15:01:
        # 33 statt 76 Grenzen) darf nicht die Historie der übrigen Grenzen
        # löschen. Neue Punkte überschreiben alte je Zeitstempel; Einträge
        # älter als 12 h werden beschnitten.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
        carried = 0
        for key, series in fresh.items():
            if series:
                merged = dict(_FLOW_SERIES.get(key) or {})
                merged.update(series)
                _FLOW_SERIES[key] = {t: v for t, v in merged.items()
                                     if t >= cutoff}
            elif _FLOW_SERIES.get(key):
                _FLOW_SERIES[key] = {t: v for t, v in _FLOW_SERIES[key].items()
                                     if t >= cutoff}
                if _FLOW_SERIES[key]:
                    carried += 1
        if carried:
            log.info("A11: %d borders without fresh data — using history "
                     "from carry-forward", carried)

    # Calibrate — only after apply_to_model so the grid snapshot is current.
    # Hold _GRID_LOCK: apply_to_model mutates global ZONES; concurrent solve()
    # calls must not read while we write.
    with _GRID_LOCK:
        STATE.apply_to_model()
    tgt = STATE.target_ts
    if tgt and _FLOW_SERIES:
        raised = CALIBRATION.calibrate_limits(_FLOW_SERIES)
        if raised:
            log.info("NTC calibration: %d limits raised (measured flow > assumed NTC): %s",
                     len(raised),
                     ", ".join(f"{a}-{b} {int(v)}" for (a, b, l), v in
                               sorted(raised.items(), key=lambda kv: -kv[1])[:6]))
        meas = measured_at(_FLOW_SERIES, tgt)
        if len(meas) >= 10:
            summary = CALIBRATION.calibrate(meas, tgt)
            log.info("Calibration: %d borders measured, %d DC pinned, "
                     "%d AC skaliert, %d Zonen korrigiert, "
                     "Ø-Fehler %s MW",
                     summary["n_measured"], summary["n_dc_set"],
                     summary["n_scaled"], summary["n_zone_adjust"],
                     summary["mean_abs_error_mw"])
        else:
            log.info("Calibration skipped: only %d measured borders", len(meas))
    return fetched


def _loop(interval: int):
    while True:
        try:
            refresh_now()
        except Exception:
            log.warning("Grid refresh failed", exc_info=True)
        time.sleep(interval)


def start_background_loop(interval: int = REFRESH_INTERVAL):
    """Startet den Hintergrund-Refresh (Daemon-Thread, wie im Terminal üblich)."""
    t = threading.Thread(target=_loop, args=(interval,), daemon=True,
                         name="grid-live")
    t.start()
    log.info("Grid live loop started (%d s interval)", interval)
    return t


# ══════════════════════════════════════════════════════════════════════════════
# FLASK-ANBINDUNG
# ══════════════════════════════════════════════════════════════════════════════

def register(app, entsoe_key: str | None = None, http=None,
             viz_path: str | None = None, auth=None):
    """
    Hängt /grid und /api/grid in eine bestehende Flask-App.

    entsoe_key : Default = ENTSOE_KEY (Key aus dem Terminal bzw. $ENTSOE_KEY)
    http       : optional die vorhandene Retry-Session (HTTP) mitgeben
    viz_path   : Pfad zu meridian_grid_viz.html (Default: neben diesem Modul)
    auth       : optionale Funktion () -> bool, z. B. das _auth() des Terminals
    """
    global _FETCHER, _FLOW_FETCHER
    from flask import jsonify, request, Response

    init_fetchers(entsoe_key, http=http)
    viz = viz_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "meridian_grid_viz.html")

    def _guard():
        return True if auth is None else bool(auth())

    @app.route("/api/grid")
    def api_grid():
        if not _guard():
            return jsonify({"error": "unauthorized"}), 401
        shocks = None
        if request.method == "GET" and request.args.get("shocks"):
            try:
                shocks = json.loads(request.args["shocks"])
            except ValueError:
                return jsonify({"error": "shocks: invalid JSON"}), 400
        return jsonify(solve_live(shocks))

    @app.route("/api/grid/refresh", methods=["POST"])
    def api_grid_refresh():
        if not _guard():
            return jsonify({"error": "unauthorized"}), 401
        try:
            fetched = refresh_now()
        except Exception as e:
            return jsonify({"error": str(e)}), 503
        return jsonify({
            "refreshed": [c for c, d in fetched.items() if d["ok"]],
            "failed": [c for c, d in fetched.items() if not d["ok"]],
        })

    @app.route("/api/grid/calibration")
    def api_grid_calibration():
        if not _guard():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({
            "ts": CALIBRATION.ts.isoformat() if CALIBRATION.ts else None,
            "n_measured": CALIBRATION.n_measured,
            "dc_setpoints": {f"{a}-{b}" + (f" [{l}]" if l else ""): int(v)
                             for (a, b, l), v in CALIBRATION.dc_setpoints.items()},
            "suscept_scale": {f"{a}-{b}": round(v, 2)
                              for (a, b), v in CALIBRATION.suscept_scale.items()},
            "zone_adjust_mw": {z: int(v)
                               for z, v in CALIBRATION.zone_adjust.items()},
            "ntc_raised": {f"{a}-{b}" + (f" [{l}]" if l else ""): int(v)
                           for (a, b, l), v in CALIBRATION.ntc_raise.items()},
            "unmeasured_borders": CALIBRATION.unmeasured,
            "fit_report": CALIBRATION.fit_report,
        })

    @app.route("/api/grid/n1")
    def api_grid_n1():
        if not _guard():
            return jsonify({"error": "unauthorized"}), 401
        STATE.apply_to_model()
        g = GridModel()
        tgt = STATE.target_ts
        if CALIBRATION.ts and tgt and \
           abs((CALIBRATION.ts - tgt).total_seconds()) <= 3600:
            CALIBRATION.apply(g)
        if request.args.get("shocks"):
            try:
                sh = json.loads(request.args["shocks"])
            except ValueError:
                return jsonify({"error": "shocks: invalid JSON"}), 400
            for zone, cats in (sh.get("gen") or {}).items():
                for cat, delta in cats.items():
                    g.shock_generation(zone, cat, float(delta))
            for zone, delta in (sh.get("load") or {}).items():
                g.shock_load(zone, float(delta))
            for key, factor in (sh.get("line") or {}).items():
                a, _, b = key.partition("-")
                if a and b:
                    g.shock_line(a, b, float(factor))
        return jsonify(n_minus_1(g))

    @app.route("/grid")
    def grid_page():
        if not _guard():
            return "<h1>401</h1>", 401
        try:
            with open(viz, encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            return (f"<h1>meridian_grid_viz.html nicht gefunden</h1>"
                    f"<p>Erwartet unter: {viz}</p>"), 500
        # Live-Endpoint in die Seite injizieren
        html = html.replace(
            "<script>",
            '<script>window.GRID_LIVE_ENDPOINT="/api/grid";</script>\n<script>',
            1)
        return Response(html, mimetype="text/html")

    log.info("MERIDIAN Grid registered: /grid, /api/grid, /api/grid/refresh")
    return app


# ══════════════════════════════════════════════════════════════════════════════
# EIC-CODE-VERIFIKATION
# ══════════════════════════════════════════════════════════════════════════════

def check_domains(key: str | None = None) -> dict:
    """
    Prüft je Zone, ob der EIC-Code Daten liefert, und zeigt anschließend die
    zeitliche Ausrichtung: welcher gemeinsame Zeitpunkt gewählt wurde und wie
    weit einzelne Zonen dafür vorgetragen werden mussten.
    """
    f = EntsoeGridFetcher(key or ENTSOE_KEY)
    print("=" * 78)
    print("  EIC CODE VERIFICATION  +  TIME ALIGNMENT CHECK")
    print("=" * 78)
    try:
        fetched = f.fetch_all()
    except Exception as e:
        print(f"  fetch_all() failed: {e}")
        print("=" * 78)
        return {}
    STATE.update(fetched)

    print(f"  {'ZONE':<5} {'EIC-CODE':<20} {'TYPES':>6} {'TW':>3} {'LATEST':>9}  STATUS")
    print("  " + "-" * 74)
    now = datetime.now(timezone.utc)
    for code in ZONE_DOMAINS:
        d = fetched.get(code, {})
        dom = d.get("domain") or ZONE_DOMAINS[code]
        lg = d.get("latest_good")
        age = (f"{int((now - datetime.fromisoformat(lg)).total_seconds() / 60)}m"
               if lg else "—")
        if d.get("ok"):
            status = "OK" + (" (Fallback-Domain)" if dom != ZONE_DOMAINS[code] else "")
        elif d.get("gen_series"):
            status = "generation only, no load data"
        elif d.get("load_series"):
            status = "load only, no generation data"
        else:
            status = "NO DATA — check EIC code"
        print(f"  {code:<5} {dom:<20} {d.get('n_types', 0):>6} "
              f"{d.get('load_subzones', 0):>3} {age:>9}  {status}")

    print("  " + "-" * 74)
    print("  TW = number of load sub-zones (IT reports multiple bidding zones separately)")

    meta = STATE.apply_to_model()
    tgt = STATE.target_ts
    print()
    print("  ALIGNMENT TO COMMON SNAPSHOT TIMESTAMP")
    print("  " + "-" * 74)
    if not tgt:
        print("  No common snapshot timestamp determinable — too few zones with data.")
    else:
        age = int((now - tgt).total_seconds() / 60)
        print(f"  Zielzeitpunkt: {tgt:%Y-%m-%d %H:%M} UTC  ({age} min alt)")
        print()
        print(f"  {'ZONE':<5} {'SOURCE':<9} {'CARRY':>8}  {'GEN':>7} {'LOAD':>8}  BAL")
        print("  " + "-" * 74)
        for code, z in ZONES.items():
            m = meta[code]
            fill = f"{m['fill_min']}m" if m.get("fill_min") is not None else "—"
            bal = z.base_gen - z.base_load
            print(f"  {code:<5} {m['source']:<9} {fill:>8}  "
                  f"{z.base_gen/1000:6.1f}G {z.base_load/1000:7.1f}G  {bal/1000:+6.1f}G")
        tg = sum(z.base_gen for z in ZONES.values()) / 1000
        tl = sum(z.base_load for z in ZONES.values()) / 1000
        print("  " + "-" * 74)
        print(f"  Σ Erzeugung {tg:.1f} GW | Σ Last {tl:.1f} GW | "
              f"offene Bilanz {tg-tl:+.1f} GW")
        print("  (offene Bilanz = Austausch mit GB/Nordics/Balkan/UA/MA + "
              "Meldelücken in A75)")

    # ── A11: Modell vs. Messung je Grenze ────────────────────────────────
    if tgt:
        print()
        print("  A11 CALIBRATION  —  Model vs. Measurement")
        print("  " + "-" * 74)
        ff = FlowFetcher(key or ENTSOE_KEY)
        try:
            flow_series = ff.fetch_all()
        except Exception as e:
            flow_series = {}
            print(f"  A11-Abruf fehlgeschlagen: {e}")
        st = ff.stats
        print(f"  Requests: {st['ok']} ok | {st['throttled']} gedrosselt/Retry "
              f"| {st['empty']} ohne Daten | {st['error']} Fehler")
        if st["throttled"] > 20:
            print("  ⚠ Deutliche ENTSO-E-Drosselung – läuft parallel noch das "
                  "alte Terminal (Port 5001) gegen dieselbe API?")
        meas = measured_at(flow_series, tgt) if flow_series else {}
        if len(meas) >= 10:
            summary = CALIBRATION.calibrate(meas, tgt)
            print(f"  {summary['n_measured']} Grenzen gemessen | "
                  f"{summary['n_dc_set']} DC-Sollwerte gesetzt | "
                  f"{summary['n_scaled']} AC-Suszeptanzen gefittet | "
                  f"{summary['n_zone_adjust']} Zonen bilanzkorrigiert")
            print(f"  Fit-Fehler: Ø {summary['mean_abs_error_mw']} MW, "
                  f"max {summary['max_abs_error_mw']} MW")
            print()
            print(f"  {'GRENZE':<12}{'TYP':<4}{'MESSUNG':>9}{'MODELL':>9}"
                  f"{'FEHLER':>8}  SKALIERUNG")
            for r in CALIBRATION.fit_report[:14]:
                lab = f"{r['a']}-{r['b']}"
                sc = f"×{r['scale']}" if r["scale"] else "(Fahrplan)"
                print(f"  {lab:<12}{r['kind']:<4}{r['measured_mw']:>+8} "
                      f"{r['model_mw']:>+8} {r['error_mw']:>+7}  {sc}")
            if CALIBRATION.unpinned:
                print(f"  ⚠ Unpinned zones (balance from A75/A65 only): "
                      + " ".join(CALIBRATION.unpinned)
                      + "  ← phantom overloads appear here first")
            if CALIBRATION.unmeasured:
                print()
                print(f"  {len(CALIBRATION.unmeasured)} Grenzen ohne A11-Messung "
                      "(DC mit Sollwert-Annahme in der Bilanz):")
                print("    " + ", ".join(
                    f"{u['a']}-{u['b']}"
                    + (f" [{u['label']}]" if u.get('label') else "")
                    + (f" ~{u['assumed_mw']:+} MW" if u.get('assumed_mw') is not None else " (AC!)")
                    for u in CALIBRATION.unmeasured))
            zin = sorted(CALIBRATION.zone_adjust.items(),
                         key=lambda kv: -abs(kv[1]))[:8]
            if zin:
                print()
                print("  Größte Bilanzkorrekturen (Kleinanlagen + Außenwelt "
                      "+ Regelanteil, nicht trennbar):")
                print("  " + "  ".join(f"{z} {v/1000:+.1f}G" for z, v in zin))
        else:
            print(f"  Nur {len(meas)} Messgrenzen am Netzschnitt – "
                  "Kalibrierung übersprungen.")
            print("  (A11 wird je TSO unterschiedlich schnell gemeldet; "
                  "nächster Versuch beim Refresh.)")

    ok = [c for c, d in fetched.items() if d.get("ok")]
    bad = [c for c, d in fetched.items() if not d.get("ok")]
    dflt = [c for c, m in meta.items() if m["source"] == "default"]
    print()
    print(f"  {len(ok)}/{len(ZONE_DOMAINS)} zones returning data.")
    if bad:
        print(f"  Ohne Daten: {', '.join(bad)}")
    if dflt:
        print(f"  Laufen auf Defaults: {', '.join(dflt)}")
        print("  → shown as dashed on map, never reported as live")
    print("=" * 78)
    return fetched


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from flask import Flask

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    if "--check" in sys.argv:
        check_domains()
        sys.exit(0)

    port = 5002
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    app = Flask(__name__)
    register(app)

    print("=" * 62)
    print("  MERIDIAN Grid Live  –  EU-Netzsimulator")
    if ENTSOE_KEY:
        print(f"  ENTSO-E-Key: ...{ENTSOE_KEY[-6:]}")
    else:
        print("  KEIN ENTSO-E-KEY – Modell läuft auf Defaults.")
        print("  Lokal:  cp .env.example .env  und Key eintragen")
        print("  Oder:   export ENTSOE_KEY='dein-key'")
    print("=" * 62)
    if not ENTSOE_KEY:
        pass
    else:
        print("  Erstabruf ENTSO-E läuft – 40 Zonen + A11, ca. 30 s ...")

    # Erstabruf synchron, damit die Seite sofort echte Daten zeigt
    try:
        first = refresh_now()
        ok = [c for c, d in first.items() if d["ok"]]
        bad = [c for c, d in first.items() if not d["ok"]]
        print(f"  {len(ok)}/{len(first)} Zonen live: {', '.join(ok)}")
        if bad:
            print(f"  Ohne Daten (laufen auf Defaults): {', '.join(bad)}")
            print("  Tipp: python3 meridian_grid_live.py --check")
    except Exception as e:
        print(f"  Erstabruf fehlgeschlagen: {e}")
        print("  Modell läuft auf Defaults, Loop versucht es weiter.")

    start_background_loop()

    print("=" * 62)
    print(f"  http://localhost:{port}/grid      → interaktive Netzkarte")
    print(f"  http://localhost:{port}/api/grid  → JSON")
    print("=" * 62)
    app.run(host="0.0.0.0", port=port, threaded=True)
