import json
import os
import threading
import time
import datetime as dt
import csv
import io
import difflib
import re
from dataclasses import dataclass, asdict, fields, field
from typing import Optional, Dict, Any, List, Tuple

import requests
import usb.core
from flask import Flask, request, jsonify, Response, send_file, abort

from skyfield.api import EarthSatellite, load, wgs84

# ============================================================
# SatFinder : Satellite Beam Tracker Lab (Web)
# ============================================================
# Changes in this revision:
# 1) Safe Start Brightness:
#    - Ensure Dimmer and White start at 255 to prevent "looks dead" confusion.
#    - If saved config has dimmer==0 or w==0, auto reset to 255 on startup and save.
# 2) Improved Layout:
#    - Rebalanced grid for better visibility of Sky Dome & reduced empty areas.
#    - Search result table fixed height with scroll.
#    - Manual Lab + Calibration moved to bottom full width.
# ============================================================

# -------------------------
# N2YO API
# -------------------------
API_KEY = "CHANGE-ME-PLS"
N2YO_BASE = "https://api.n2yo.com/rest/v1/satellite"

# -------------------------
# uDMX IDs (VID_16C0 PID_05DC)
# -------------------------
UDMX_VID = 0x16C0
UDMX_PID = 0x05DC

# uDMX vendor requests (common for uDMX)
REQ_SET_MULTI = 2
BM_REQUEST_TYPE = 0x40  # Host-to-device | Vendor | Device
DMX_UNIVERSE_SIZE = 512

# -------------------------
# Persisted config
# -------------------------
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".sat_mh_web_config.json")

# -------------------------
# SATCAT cache (for name -> NORAD ID)
# -------------------------
SATCAT_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".satcat_cache.csv")
SATCAT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "satcat.csv")
SATCAT_URLS = [
    "https://celestrak.org/pub/satcat.csv",
    "https://www.celestrak.org/pub/satcat.csv",
    "https://celestrak.com/pub/satcat.csv",
    "https://www.celestrak.com/pub/satcat.csv",
]

# -------------------------
# Embedded default pan calibration (from user's measured data)
# -------------------------
DEFAULT_DMX_90 = 50
DEFAULT_DMX_180 = 101
DEFAULT_DMX_270 = 152
DEFAULT_DMX_360 = 203
DEFAULT_PAN_MAX_DEG = 452.961  # from user's previous auto-calibrate result

DEFAULT_PAN_LUT = [
    {"deg": 0.0, "dmx": 0},
    {"deg": 90.0, "dmx": DEFAULT_DMX_90},
    {"deg": 180.0, "dmx": DEFAULT_DMX_180},
    {"deg": 270.0, "dmx": DEFAULT_DMX_270},
    {"deg": 360.0, "dmx": DEFAULT_DMX_360},
]


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def normalize_sat_name(s: str) -> str:
    s = s.strip().upper()
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


def unwrap_pan(pan_0_360: float, pan_prev: Optional[float], pan_max_deg: float) -> float:
    """
    Convert pan_0_360 to a "continuous" pan by choosing among:
      pan_0_360, pan_0_360 + 360
    that is closest to previous. Then clamp to [0..pan_max_deg].
    """
    candidates = [pan_0_360, pan_0_360 + 360.0]
    if pan_prev is None:
        chosen = candidates[0]
    else:
        chosen = min(candidates, key=lambda x: abs(x - pan_prev))
    return clamp(chosen, 0.0, float(pan_max_deg))


def fetch_tle_from_n2yo(norad_id: int) -> Dict[str, str]:
    """
    N2YO Get TLE:
      /tle/{id}&apiKey=... returns JSON with 'tle' containing 2 lines separated by \\r\\n
    """
    if not API_KEY:
        raise RuntimeError("ยังไม่ได้ตั้งค่า API_KEY")

    url = f"{N2YO_BASE}/tle/{int(norad_id)}&apiKey={API_KEY}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    j = r.json()

    info = j.get("info", {})
    satname = info.get("satname", f"NORAD {norad_id}")
    tle = j.get("tle", "")

    parts = tle.split("\r\n")
    if len(parts) < 2:
        parts = tle.splitlines()
    if len(parts) < 2:
        raise RuntimeError("รูปแบบ TLE จาก N2YO ไม่ถูกต้อง/ไม่ครบ 2 บรรทัด")

    return {"satname": satname, "l1": parts[0].strip(), "l2": parts[1].strip()}


class SatCatalog:
    """
    Lightweight SATCAT loader + fuzzy search.
    Uses CelesTrak satcat.csv as the primary source; caches locally.
    """
    def __init__(self, cache_path: str = SATCAT_CACHE_PATH):
        self.cache_path = cache_path
        self.loaded = False
        self.rows: List[Tuple[str, int, str]] = []  # (OBJECT_NAME, NORAD_CAT_ID, normalized_name)
        self.last_load_epoch = 0.0

    def _download_satcat_csv(self) -> str:
        last_err = None
        headers = {"User-Agent": "SatFinder-SatelliteBeamTrackerLab/1.0"}
        for url in SATCAT_URLS:
            try:
                r = requests.get(url, headers=headers, timeout=25)
                r.raise_for_status()
                text = r.text
                if "NORAD_CAT_ID" in text and "OBJECT_NAME" in text:
                    return text
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"ดาวน์โหลด SATCAT ไม่สำเร็จ: {last_err}")

    def _load_from_csv_text(self, csv_text: str):
        f = io.StringIO(csv_text)
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            raise RuntimeError("SATCAT CSV ไม่มี header")

        try:
            idx_name = header.index("OBJECT_NAME")
            idx_norad = header.index("NORAD_CAT_ID")
        except ValueError:
            h = [x.strip().upper() for x in header]
            idx_name = h.index("OBJECT_NAME")
            idx_norad = h.index("NORAD_CAT_ID")

        rows = []
        for row in reader:
            if not row or len(row) <= max(idx_name, idx_norad):
                continue
            name = (row[idx_name] or "").strip()
            norad_raw = (row[idx_norad] or "").strip()
            if not name or not norad_raw:
                continue
            try:
                norad = int(norad_raw)
            except Exception:
                continue
            rows.append((name, norad, normalize_sat_name(name)))

        self.rows = rows
        self.loaded = True
        self.last_load_epoch = time.time()

    def ensure_loaded(self, force: bool = False):
        if self.loaded and not force and (time.time() - self.last_load_epoch) < 86400:
            return

        # 0) prefer local satcat.csv beside this script if present (offline-first; faster first run)
        if os.path.exists(SATCAT_LOCAL_PATH):
            try:
                with open(SATCAT_LOCAL_PATH, "r", encoding="utf-8") as rf:
                    text_local = rf.read()
                if "NORAD_CAT_ID" in text_local and "OBJECT_NAME" in text_local:
                    self._load_from_csv_text(text_local)
                    # refresh cache as well (best-effort)
                    try:
                        with open(self.cache_path, "w", encoding="utf-8", newline="") as wf:
                            wf.write(text_local)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        # 1) try download
        try:
            text = self._download_satcat_csv()
            try:
                with open(self.cache_path, "w", encoding="utf-8", newline="") as wf:
                    wf.write(text)
            except Exception:
                pass
            self._load_from_csv_text(text)
            return
        except Exception:
            pass

        # 2) fallback to cache
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as rf:
                    text = rf.read()
                self._load_from_csv_text(text)
                return
            except Exception as e:
                raise RuntimeError(f"โหลด SATCAT จาก cache ไม่สำเร็จ: {e}")

        raise RuntimeError("ไม่สามารถโหลด SATCAT ได้ (ทั้งออนไลน์และ cache)")

    def search(self, query: str, limit: int = 12) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if len(q) < 2:
            return []
        self.ensure_loaded()

        q_norm = normalize_sat_name(q)
        if not q_norm:
            return []

        scored = []
        for (name, norad, name_norm) in self.rows:
            score = 0.0
            if q_norm in name_norm:
                ratio = len(q_norm) / max(len(name_norm), 1)
                score = 0.92 + 0.08 * clamp(ratio, 0.0, 1.0)
            else:
                score = difflib.SequenceMatcher(None, q_norm, name_norm).ratio() * 0.90

            if q.upper() in name.upper():
                score += 0.05

            if score >= 0.55:
                scored.append((score, name, norad))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for (s, name, norad) in scored[:limit]:
            out.append({"name": name, "norad_id": norad, "score": round(float(s), 3)})
        return out

    def best_match(self, query: str) -> Optional[Dict[str, Any]]:
        matches = self.search(query, limit=3)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        if matches[0]["score"] >= 0.85 and (matches[0]["score"] - matches[1]["score"]) >= 0.08:
            return matches[0]
        return matches[0]


SATCAT = SatCatalog()


class UDMX:
    """uDMX output (VID_16C0 PID_05DC)."""
    def __init__(self, vid=UDMX_VID, pid=UDMX_PID):
        self.dev = usb.core.find(idVendor=vid, idProduct=pid)
        if self.dev is None:
            raise RuntimeError("ไม่พบ uDMX (VID_16C0 PID_05DC) — ตรวจ Zadig driver เป็น libusbK/WinUSB")

    def set_multi(self, start_ch_1based: int, values):
        start0 = start_ch_1based - 1
        data = bytes([clamp(int(v), 0, 255) for v in values])
        ln = len(data)
        if ln == 0:
            return
        if not (0 <= start0 < DMX_UNIVERSE_SIZE):
            raise ValueError("Start channel out of range.")
        if start0 + ln > DMX_UNIVERSE_SIZE:
            raise ValueError("Block exceeds DMX universe.")
        self.dev.ctrl_transfer(BM_REQUEST_TYPE, REQ_SET_MULTI, ln, start0, data)


def sanitize_pan_lut_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return sorted unique LUT points by deg."""
    out = []
    for p in (points or []):
        try:
            deg = float(p.get("deg"))
            dmx = int(p.get("dmx"))
            deg = clamp(deg, 0.0, 540.0)
            dmx = int(clamp(dmx, 0, 255))
            out.append({"deg": float(deg), "dmx": int(dmx)})
        except Exception:
            continue

    merged: Dict[float, int] = {}
    for p in out:
        d = round(float(p["deg"]), 6)
        merged[d] = max(int(p["dmx"]), merged.get(d, -1))

    pts = [{"deg": float(k), "dmx": int(v)} for k, v in merged.items()]
    pts.sort(key=lambda x: x["deg"])
    return pts


def interpolate_deg_to_dmx(points: List[Dict[str, Any]], deg: float) -> int:
    """
    Piecewise-linear interpolation/extrapolation on points sorted by deg.
    """
    if not points or len(points) < 2:
        return 0

    deg = float(deg)
    if deg <= points[0]["deg"]:
        x0, y0 = points[0]["deg"], points[0]["dmx"]
        x1, y1 = points[1]["deg"], points[1]["dmx"]
        if x1 == x0:
            return int(clamp(y0, 0, 255))
        y = y0 + (deg - x0) * (y1 - y0) / (x1 - x0)
        return int(clamp(round(y), 0, 255))

    if deg >= points[-1]["deg"]:
        x0, y0 = points[-2]["deg"], points[-2]["dmx"]
        x1, y1 = points[-1]["deg"], points[-1]["dmx"]
        if x1 == x0:
            return int(clamp(y1, 0, 255))
        y = y0 + (deg - x0) * (y1 - y0) / (x1 - x0)
        return int(clamp(round(y), 0, 255))

    for i in range(len(points) - 1):
        x0, y0 = points[i]["deg"], points[i]["dmx"]
        x1, y1 = points[i + 1]["deg"], points[i + 1]["dmx"]
        if x0 <= deg <= x1:
            if x1 == x0:
                return int(clamp(y0, 0, 255))
            y = y0 + (deg - x0) * (y1 - y0) / (x1 - x0)
            return int(clamp(round(y), 0, 255))

    return int(clamp(points[-1]["dmx"], 0, 255))


@dataclass
class Config:
    # Location (persisted)
    lat: float = 13.7563
    lon: float = 100.5018
    alt_m: float = 10.0

    # Output (persisted)
    output_hz: float = 20.0
    base_addr: int = 1

    # Offsets (persisted; optional calibration)
    pan_offset_deg: float = 0.0
    tilt_offset_deg: float = 0.0

    # Pan direction model:
    pan_dmx_increases_ccw: bool = True

    # Pan effective range + LUT
    pan_max_deg: float = DEFAULT_PAN_MAX_DEG
    pan_lut_points: List[Dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_PAN_LUT))

    # Fixture channels (persisted; safe defaults)
    xy_speed: int = 20
    dimmer: int = 255          # SAFE default: full brightness
    strobe: int = 0
    r: int = 0
    g: int = 0
    b: int = 0
    w: int = 255               # SAFE default: full white
    laser: int = 0
    belt_effect: int = 0
    belt_speed: int = 0
    macro: int = 0

    # Manual lab (persisted)
    manual_az_deg: float = 0.0
    manual_el_deg: float = 0.0

    # Demo without hardware
    simulate_only: bool = False


def load_config() -> Config:
    cfg = Config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}

            allowed = {fld.name for fld in fields(Config)}
            filtered = {k: v for k, v in data.items() if k in allowed}

            if "pan_lut_points" in filtered and not isinstance(filtered["pan_lut_points"], list):
                filtered["pan_lut_points"] = []

            cfg = Config(**filtered)

            cfg.manual_az_deg = clamp(float(cfg.manual_az_deg), 0.0, 360.0)
            cfg.manual_el_deg = clamp(float(cfg.manual_el_deg), 0.0, 90.0)

            cfg.pan_max_deg = clamp(float(cfg.pan_max_deg), 90.0, 540.0)
            cfg.pan_lut_points = sanitize_pan_lut_points(cfg.pan_lut_points)

            # Brightness sanitize (avoid None / invalid)
            try:
                cfg.dimmer = int(clamp(int(cfg.dimmer), 0, 255))
            except Exception:
                cfg.dimmer = 255
            try:
                cfg.w = int(clamp(int(cfg.w), 0, 255))
            except Exception:
                cfg.w = 255

        except Exception:
            pass
    else:
        cfg.pan_lut_points = sanitize_pan_lut_points(cfg.pan_lut_points)
        cfg.pan_max_deg = clamp(float(cfg.pan_max_deg), 90.0, 540.0)
        cfg.dimmer = 255
        cfg.w = 255
    return cfg


def save_config(cfg: Config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[WARN] Config save failed:", e)


class Tracker:
    def __init__(self):
        self.cfg = load_config()
        self.lock = threading.Lock()

        # SAFE START (strict): always start with full brightness (Dimmer/White=255) every program run.
        # This is intentional for classroom demos: prevents "device looks dead" due to prior saved dimming.
        # We also force Strobe/Laser off at boot for safety.
        self.cfg.dimmer = 255
        self.cfg.w = 255
        self.cfg.strobe = 0
        self.cfg.laser = 0
        # Default indicator: show "connected" once DMX is connected (set in _connect_udmx).

        self.ts = load.timescale()
        self.sat: Optional[EarthSatellite] = None
        self.satname: str = ""
        self.tle_l1: Optional[str] = None
        self.tle_l2: Optional[str] = None
        self.last_tle_fetch = 0.0

        self.session_norad_id: Optional[int] = None
        self.session_sat_query: str = ""
        self.session_sat_official_name: Optional[str] = None
        self.session_tle_refresh_minutes: int = 360

        self.udmx: Optional[UDMX] = None

        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.path_cache: Dict[str, Any] = {"key": None, "timestamp": 0.0, "result": None}

        # Calibration session (not persisted until commit)
        self.calib_pan_points: Dict[int, Optional[int]] = {90: None, 180: None, 270: None, 360: None}
        self.calib_pan_step: int = 90

        self.state: Dict[str, Any] = {
            "running": False,
            "mode": "idle",
            "error": None,

            "sat_query": "",
            "norad_id": None,
            "sat_official_name": None,

            "lat": self.cfg.lat,
            "lon": self.cfg.lon,
            "alt_m": self.cfg.alt_m,

            "az_deg": None,
            "el_deg": None,

            "pan_deg": None,
            "tilt_deg": None,

            "pan_dmx": None,
            "tilt_dmx": None,

            "timestamp": None,
            "tle": None,
        }

    def _connect_udmx(self):
        if self.cfg.simulate_only:
            self.udmx = None
            return
        self.udmx = UDMX()
        # Indicator: connected to Moving Head (belt LED)
        try:
            with self.lock:
                # do not override "tracking" indicator
                if self.state.get("mode") in (None, "idle"):
                    self.cfg.belt_effect = 10
                    self.cfg.belt_speed = 0
        except Exception:
            pass


    def _observer(self):
        return wgs84.latlon(self.cfg.lat, self.cfg.lon, elevation_m=self.cfg.alt_m)

    def set_session_satellite(self, sat_query: str, norad_id: Optional[int], tle_refresh_minutes: Optional[int] = None) -> Dict[str, Any]:
        sat_query = (sat_query or "").strip()
        resolved_norad = None
        resolved_official = None

        if tle_refresh_minutes is not None:
            try:
                self.session_tle_refresh_minutes = int(clamp(int(tle_refresh_minutes), 60, 1440))
            except Exception:
                self.session_tle_refresh_minutes = 360

        if norad_id is not None and str(norad_id).strip() != "":
            try:
                resolved_norad = int(norad_id)
            except Exception:
                resolved_norad = None

        if resolved_norad is None and sat_query:
            best = SATCAT.best_match(sat_query)
            if not best:
                raise RuntimeError("ไม่พบดาวเทียมจากชื่อที่ใส่ (ลองพิมพ์ให้ยาวขึ้น หรือใช้ NORAD ID โดยตรง)")
            resolved_norad = int(best["norad_id"])
            resolved_official = best["name"]

        with self.lock:
            self.session_sat_query = sat_query
            self.session_norad_id = resolved_norad
            self.session_sat_official_name = resolved_official
            self.state["sat_query"] = sat_query
            self.state["norad_id"] = resolved_norad
            self.state["sat_official_name"] = resolved_official

            self.sat = None
            self.tle_l1 = None
            self.tle_l2 = None
            self.last_tle_fetch = 0.0
            self.path_cache["key"] = None

        return {
            "ok": True,
            "sat_query": sat_query,
            "norad_id": resolved_norad,
            "sat_official_name": resolved_official,
            "tle_refresh_minutes": self.session_tle_refresh_minutes,
        }

    def _ensure_satellite(self):
        if self.session_norad_id is None:
            raise RuntimeError("ยังไม่ได้เลือกดาวเทียม: ใส่ NORAD ID หรือค้นหาชื่อก่อน")

        now = time.time()
        refresh_sec = max(60, int(self.session_tle_refresh_minutes * 60))

        if self.sat is None or (now - self.last_tle_fetch) > refresh_sec:
            tle = fetch_tle_from_n2yo(self.session_norad_id)
            self.satname = tle["satname"]
            self.tle_l1 = tle["l1"]
            self.tle_l2 = tle["l2"]
            self.sat = EarthSatellite(self.tle_l1, self.tle_l2, self.satname, self.ts)
            self.last_tle_fetch = now
            self.path_cache["key"] = None
            with self.lock:
                self.state["tle"] = {"satname": self.satname, "l1": self.tle_l1, "l2": self.tle_l2}

    def _az_to_fixture_pan_0_360(self, az_deg_cw_from_north: float) -> float:
        az = az_deg_cw_from_north % 360.0
        if self.cfg.pan_dmx_increases_ccw:
            pan = (360.0 - az + self.cfg.pan_offset_deg) % 360.0
        else:
            pan = (az + self.cfg.pan_offset_deg) % 360.0
        return pan

    def _el_to_fixture_tilt_0_180(self, el_deg_up_from_horizon: float) -> float:
        return clamp(el_deg_up_from_horizon + self.cfg.tilt_offset_deg, 0.0, 180.0)

    def pan_deg_to_dmx(self, pan_deg: float) -> int:
        pan_deg = clamp(float(pan_deg), 0.0, float(self.cfg.pan_max_deg))
        pts = sanitize_pan_lut_points(self.cfg.pan_lut_points)

        if pts and len(pts) >= 2:
            return int(clamp(interpolate_deg_to_dmx(pts, pan_deg), 0, 255))

        if self.cfg.pan_max_deg <= 1e-6:
            return 0
        dmx = round((pan_deg / float(self.cfg.pan_max_deg)) * 255.0)
        return int(clamp(dmx, 0, 255))

    def tilt_deg_to_dmx(self, tilt_deg: float) -> int:
        tilt_deg = clamp(float(tilt_deg), 0.0, 180.0)
        dmx = round((tilt_deg / 180.0) * 255.0)
        return int(clamp(dmx, 0, 255))

    def _frame_tracking(self, az_deg: float, el_deg: float, last_pan_deg: Optional[float]) -> Dict[str, Any]:
        pan0_360 = self._az_to_fixture_pan_0_360(az_deg)
        pan_deg = unwrap_pan(pan0_360, last_pan_deg, self.cfg.pan_max_deg)
        tilt_deg = self._el_to_fixture_tilt_0_180(el_deg)

        pan_dmx = self.pan_deg_to_dmx(pan_deg)
        tilt_dmx = self.tilt_deg_to_dmx(tilt_deg)

        macro = int(clamp(self.cfg.macro, 0, 50))
        frame = [
            pan_dmx,
            tilt_dmx,
            int(clamp(self.cfg.xy_speed, 0, 255)),
            int(clamp(self.cfg.dimmer, 0, 255)),
            int(clamp(self.cfg.strobe, 0, 255)),
            int(clamp(self.cfg.r, 0, 255)),
            int(clamp(self.cfg.g, 0, 255)),
            int(clamp(self.cfg.b, 0, 255)),
            int(clamp(self.cfg.w, 0, 255)),
            0,
            int(clamp(self.cfg.belt_effect, 0, 255)),
            int(clamp(self.cfg.belt_speed, 0, 255)),
            macro,
        ]
        return {
            "frame": frame,
            "pan_deg": pan_deg,
            "tilt_deg": tilt_deg,
            "pan_dmx": pan_dmx,
            "tilt_dmx": tilt_dmx,
            "last_pan_deg": pan_deg,
        }

    def _frame_manual_azel(self, last_pan_deg: Optional[float]) -> Dict[str, Any]:
        az_deg = clamp(float(self.cfg.manual_az_deg), 0.0, 360.0)
        el_deg = clamp(float(self.cfg.manual_el_deg), 0.0, 90.0)
        out = self._frame_tracking(az_deg, el_deg, last_pan_deg)
        out["az_deg"] = az_deg
        out["el_deg"] = el_deg
        return out

    def _frame_home_blackout(self) -> List[int]:
        return [0, 0, int(clamp(self.cfg.xy_speed, 0, 255)), 0, 0, 0, 0, 0, 0, 0, int(clamp(self.cfg.belt_effect, 0, 255)), int(clamp(self.cfg.belt_speed, 0, 255)), 0]

    def _frame_north_align(self) -> List[int]:
        return [
            0, 0,
            int(clamp(self.cfg.xy_speed, 0, 255)),
            int(clamp(self.cfg.dimmer, 0, 255)),
            0,
            0, 0, 0,
            int(clamp(self.cfg.w, 0, 255)),
            0,
            0, 0,
            0,
        ]

    def send_frame_once(self, frame_13ch: List[int]):
        if self.cfg.simulate_only:
            return
        self._connect_udmx()
        if self.udmx:
            self.udmx.set_multi(self.cfg.base_addr, frame_13ch)

    def set_north_now_once(self):
        try:
            frame = self._frame_north_align()
            self.send_frame_once(frame)
            with self.lock:
                self.state.update({
                    "pan_deg": 0.0,
                    "tilt_deg": 0.0,
                    "pan_dmx": 0,
                    "tilt_dmx": 0,
                    "timestamp": time.time(),
                })
        except Exception as e:
            with self.lock:
                self.state["error"] = f"Set North Now failed: {e}"
            raise

    def send_raw_pan_tilt(self, pan_dmx: int, tilt_dmx: int = 0):
        pan_dmx = int(clamp(int(pan_dmx), 0, 255))
        tilt_dmx = int(clamp(int(tilt_dmx), 0, 255))

        frame = [
            pan_dmx,
            tilt_dmx,
            int(clamp(self.cfg.xy_speed, 0, 255)),
            int(clamp(self.cfg.dimmer, 0, 255)),
            int(clamp(self.cfg.strobe, 0, 255)),
            int(clamp(self.cfg.r, 0, 255)),
            int(clamp(self.cfg.g, 0, 255)),
            int(clamp(self.cfg.b, 0, 255)),
            int(clamp(self.cfg.w, 0, 255)),
            0,
            int(clamp(self.cfg.belt_effect, 0, 255)),
            int(clamp(self.cfg.belt_speed, 0, 255)),
            int(clamp(self.cfg.macro, 0, 50)),
        ]
        if self.cfg.simulate_only:
            return
        self._connect_udmx()
        if self.udmx:
            self.udmx.set_multi(self.cfg.base_addr, frame)

    
    def _estimate_pan_max_deg_from_lut(self, pts: List[Dict[str, Any]]) -> float:
        """
        Estimate pan_max_deg (deg at DMX=255) from LUT points.

        Preferred method (more physical for moving heads):
        - Extrapolate from the last segment (270°→360°) to DMX=255.

        Fallback:
        - Least-squares fit of dmx = a*deg (through origin) from available points.
        """
        pts = sanitize_pan_lut_points(pts)
        # Prefer last segment 270->360 (if present)
        d270 = next((p["dmx"] for p in pts if abs(p["deg"] - 270.0) < 1e-6), None)
        d360 = next((p["dmx"] for p in pts if abs(p["deg"] - 360.0) < 1e-6), None)

        if d270 is not None and d360 is not None and int(d360) > int(d270):
            slope_deg_per_dmx = 90.0 / float(int(d360) - int(d270))  # deg per dmx count near the high end
            pan_max_est = 360.0 + (255.0 - float(int(d360))) * slope_deg_per_dmx
            return float(clamp(pan_max_est, 90.0, 540.0))

        # Fallback: least squares fit dmx = a*deg through origin
        s1 = 0.0
        s2 = 0.0
        for p in pts:
            deg = float(p["deg"])
            dmx = float(p["dmx"])
            if deg <= 0:
                continue
            s1 += deg * dmx
            s2 += deg * deg

        if s2 <= 1e-9 or s1 <= 1e-9:
            return float(DEFAULT_PAN_MAX_DEG)

        a = s1 / s2
        pan_max_est = 255.0 / a
        return float(clamp(pan_max_est, 90.0, 540.0))

    def auto_calibrate_pan(self, dmx_90: int, dmx_180: int, dmx_270: int, dmx_360: int) -> Dict[str, Any]:
        """
        Backward-compatible auto calibration (from four measured DMX points).
        It now builds a piecewise LUT and estimates pan_max_deg via high-end extrapolation.
        """
        pts = [
            {"deg": 0.0, "dmx": 0},
            {"deg": 90.0, "dmx": int(clamp(int(dmx_90), 0, 255))},
            {"deg": 180.0, "dmx": int(clamp(int(dmx_180), 0, 255))},
            {"deg": 270.0, "dmx": int(clamp(int(dmx_270), 0, 255))},
            {"deg": 360.0, "dmx": int(clamp(int(dmx_360), 0, 255))},
        ]
        pts = sanitize_pan_lut_points(pts)

        monotonic = True
        for i in range(1, len(pts)):
            if pts[i]["dmx"] < pts[i - 1]["dmx"]:
                monotonic = False
                break

        if not monotonic:
            raise RuntimeError("ค่า DMX ที่วัดได้ควรเพิ่มขึ้นตามองศา (90 < 180 < 270 < 360)")

        pan_max_est = self._estimate_pan_max_deg_from_lut(pts)

        with self.lock:
            self.cfg.pan_lut_points = pts
            self.cfg.pan_max_deg = float(pan_max_est)
            save_config(self.cfg)
            self.path_cache["key"] = None

        return {
            "ok": True,
            "pan_max_deg_est": round(float(pan_max_est), 3),
            "lut_points": pts,
            "monotonic": monotonic,
            "note": "บันทึก LUT และ pan_max_deg แล้ว (จะถูกใช้ใน Manual/Tracking ทันที)",
        }

    # -------------------------
    # Calibration Lab (Pan 90/180/270/360)
    # -------------------------
    def calib_pan_start(self) -> Dict[str, Any]:
        """Reset calibration session and force Pan=0 (safe start for measurement)."""
        with self.lock:
            self.calib_pan_points = {90: None, 180: None, 270: None, 360: None}
            self.calib_pan_step = 90
        try:
            # ensure fixture is at a known start point
            self.send_raw_pan_tilt(0, 0)
        except Exception:
            pass
        return self.calib_pan_get_session()

    def calib_pan_clear(self) -> Dict[str, Any]:
        with self.lock:
            self.calib_pan_points = {90: None, 180: None, 270: None, 360: None}
            self.calib_pan_step = 90
        return self.calib_pan_get_session()

    def calib_pan_set_point(self, deg: int, dmx: int) -> Dict[str, Any]:
        deg_i = int(deg)
        if deg_i not in (90, 180, 270, 360):
            raise RuntimeError("องศาที่อนุญาตคือ 90/180/270/360 เท่านั้น")
        dmx_i = int(clamp(int(dmx), 0, 255))
        with self.lock:
            self.calib_pan_points[deg_i] = dmx_i
            self.calib_pan_step = deg_i
        return self.calib_pan_get_session()

    def calib_pan_get_session(self) -> Dict[str, Any]:
        with self.lock:
            pts = dict(self.calib_pan_points)
            step = int(self.calib_pan_step)
        return {"ok": True, "points": pts, "step": step}

    def calib_pan_commit(self) -> Dict[str, Any]:
        """
        Build LUT + estimate pan_max_deg automatically and persist to config.
        Requirements:
          - All four points 90/180/270/360 are provided.
          - DMX is monotonic increasing with degrees.
        """
        with self.lock:
            pts_map = dict(self.calib_pan_points)

        missing = [k for k, v in pts_map.items() if v is None]
        if missing:
            raise RuntimeError(f"ยังไม่ได้บันทึกค่าครบ: {missing}")

        d90, d180, d270, d360 = (int(pts_map[90]), int(pts_map[180]), int(pts_map[270]), int(pts_map[360]))
        if not (0 <= d90 <= d180 <= d270 <= d360 <= 255):
            raise RuntimeError("ค่า DMX ต้องเพิ่มขึ้นตามองศา (90 ≤ 180 ≤ 270 ≤ 360)")

        pts = [
            {"deg": 0.0, "dmx": 0},
            {"deg": 90.0, "dmx": d90},
            {"deg": 180.0, "dmx": d180},
            {"deg": 270.0, "dmx": d270},
            {"deg": 360.0, "dmx": d360},
        ]
        pts = sanitize_pan_lut_points(pts)
        pan_max_est = self._estimate_pan_max_deg_from_lut(pts)

        with self.lock:
            self.cfg.pan_lut_points = pts
            self.cfg.pan_max_deg = float(pan_max_est)
            save_config(self.cfg)
            self.path_cache["key"] = None

        return {
            "ok": True,
            "pan_max_deg_est": round(float(pan_max_est), 3),
            "lut_points": pts,
            "note": "คำนวณ/บันทึก Calibration แล้ว: ระบบจะใช้ LUT นี้ใน Manual/Tracking ทันที",
        }

    def calib_pan_restore_defaults(self) -> Dict[str, Any]:
        with self.lock:
            self.cfg.pan_lut_points = sanitize_pan_lut_points(list(DEFAULT_PAN_LUT))
            self.cfg.pan_max_deg = float(DEFAULT_PAN_MAX_DEG)
            save_config(self.cfg)
            self.path_cache["key"] = None
        return {"ok": True, "pan_max_deg": self.cfg.pan_max_deg, "lut_points": self.cfg.pan_lut_points}

    def _start_mode_thread(self, mode: str):
        with self.lock:
            if self.state["running"]:
                self.state["mode"] = mode
                # Belt indicator by mode
                if mode == "tracking":
                    self.cfg.belt_effect = 100
                    self.cfg.belt_speed = 10
                elif mode == "manual":
                    self.cfg.belt_effect = 10
                    self.cfg.belt_speed = 0
                elif mode == "idle":
                    # keep "connected" indicator if DMX is present
                    pass
                else:
                    pass

                return
            self.stop_event.clear()
            self.state["error"] = None
            self.state["running"] = True
            self.state["mode"] = mode

        def loop():
            last_pan_deg = None
            was_up = False
            try:
                self._connect_udmx()
                while not self.stop_event.is_set():
                    with self.lock:
                        mode_snapshot = self.state["mode"]

                    if mode_snapshot == "tracking":
                        self._ensure_satellite()
                        obs = self._observer()
                        now_t = self.ts.now()
                        topoc = (self.sat - obs).at(now_t)
                        alt, az, _ = topoc.altaz()

                        az_deg = float(az.degrees)
                        el_deg = float(alt.degrees)
                        # LOS auto-stop: once the satellite has been above the horizon in this session,
                        # stop tracking when it goes below the horizon again (returns home + blackout).
                        if el_deg >= 0.0:
                            was_up = True
                        elif was_up:
                            # below horizon after being up: trigger safe stop
                            self.stop_event.set()
                            break



                        if el_deg < 0.0:
                            frame = self._frame_home_blackout()
                            if (not self.cfg.simulate_only) and self.udmx:
                                self.udmx.set_multi(self.cfg.base_addr, frame)

                            with self.lock:
                                self.state.update({
                                    "az_deg": az_deg,
                                    "el_deg": el_deg,
                                    "pan_deg": 0.0,
                                    "tilt_deg": 0.0,
                                    "pan_dmx": 0,
                                    "tilt_dmx": 0,
                                    "timestamp": time.time(),
                                })
                            self.stop_event.set()
                            break

                        out = self._frame_tracking(az_deg, el_deg, last_pan_deg)
                        last_pan_deg = out["last_pan_deg"]

                        if (not self.cfg.simulate_only) and self.udmx:
                            self.udmx.set_multi(self.cfg.base_addr, out["frame"])

                        with self.lock:
                            self.state.update({
                                "lat": self.cfg.lat,
                                "lon": self.cfg.lon,
                                "alt_m": self.cfg.alt_m,
                                "az_deg": az_deg,
                                "el_deg": el_deg,
                                "pan_deg": out["pan_deg"],
                                "tilt_deg": out["tilt_deg"],
                                "pan_dmx": out["pan_dmx"],
                                "tilt_dmx": out["tilt_dmx"],
                                "timestamp": time.time(),
                            })

                    elif mode_snapshot == "manual":
                        out = self._frame_manual_azel(last_pan_deg)
                        last_pan_deg = out["last_pan_deg"]

                        if (not self.cfg.simulate_only) and self.udmx:
                            self.udmx.set_multi(self.cfg.base_addr, out["frame"])

                        with self.lock:
                            self.state.update({
                                "az_deg": out["az_deg"],
                                "el_deg": out["el_deg"],
                                "pan_deg": out["pan_deg"],
                                "tilt_deg": out["tilt_deg"],
                                "pan_dmx": out["pan_dmx"],
                                "tilt_dmx": out["tilt_dmx"],
                                "timestamp": time.time(),
                            })

                    time.sleep(1.0 / max(self.cfg.output_hz, 1.0))

            except Exception as e:
                with self.lock:
                    self.state["error"] = str(e)
            finally:
                try:
                    if (not self.cfg.simulate_only) and self.udmx:
                        self.udmx.set_multi(self.cfg.base_addr, self._frame_home_blackout())
                except Exception:
                    pass
                with self.lock:
                    self.state["running"] = False
                    self.state["mode"] = "idle"

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def start_tracking(self):
        self._start_mode_thread("tracking")

    def start_manual(self):
        self._start_mode_thread("manual")

    def stop(self):
        self.stop_event.set()

    def blackout(self):
        with self.lock:
            self.cfg.dimmer = 0
            self.cfg.strobe = 0
            self.cfg.r = self.cfg.g = self.cfg.b = self.cfg.w = 0
            self.cfg.belt_effect = 0
            self.cfg.belt_speed = 0
            save_config(self.cfg)
        try:
            if not self.cfg.simulate_only:
                self._connect_udmx()
                if self.udmx:
                    self.udmx.set_multi(self.cfg.base_addr, self._frame_home_blackout())
        except Exception:
            pass

    def get_config(self) -> Dict[str, Any]:
        with self.lock:
            return asdict(self.cfg)

    def update_config(self, updates: Dict[str, Any]):
        with self.lock:
            for k, v in (updates or {}).items():
                if hasattr(self.cfg, k):
                    cur = getattr(self.cfg, k)
                    try:
                        if isinstance(cur, bool):
                            setattr(self.cfg, k, bool(v))
                        elif isinstance(cur, int):
                            setattr(self.cfg, k, int(v))
                        elif isinstance(cur, float):
                            setattr(self.cfg, k, float(v))
                        elif isinstance(cur, list):
                            setattr(self.cfg, k, v if isinstance(v, list) else cur)
                        else:
                            setattr(self.cfg, k, v)
                    except Exception:
                        pass

            self.cfg.manual_az_deg = clamp(float(self.cfg.manual_az_deg), 0.0, 360.0)
            self.cfg.manual_el_deg = clamp(float(self.cfg.manual_el_deg), 0.0, 90.0)
            self.cfg.pan_max_deg = clamp(float(self.cfg.pan_max_deg), 90.0, 540.0)
            self.cfg.pan_lut_points = sanitize_pan_lut_points(self.cfg.pan_lut_points)

            # Keep brightness in range
            try:
                self.cfg.dimmer = int(clamp(int(self.cfg.dimmer), 0, 255))
            except Exception:
                self.cfg.dimmer = 255
            try:
                self.cfg.w = int(clamp(int(self.cfg.w), 0, 255))
            except Exception:
                self.cfg.w = 255

            # Location saved indicator (belt LED)
            if any(key in (updates or {}) for key in ("lat", "lon", "alt_m")):
                self.cfg.belt_effect = 120
                self.cfg.belt_speed = 10

            save_config(self.cfg)

            if any(x in updates for x in ("lat", "lon", "alt_m", "pan_offset_deg", "tilt_offset_deg",
                                          "pan_dmx_increases_ccw", "pan_max_deg", "pan_lut_points")):
                self.path_cache["key"] = None

    def get_state(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.state)

    def get_tle(self) -> Dict[str, Any]:
        try:
            self._ensure_satellite()
        except Exception as e:
            return {"ok": False, "error": str(e), "tle": self.state.get("tle")}
        return {"ok": True, "tle": self.state.get("tle")}

    def _path_cache_key(self) -> Tuple:
        return (
            self.session_norad_id,
            round(self.cfg.lat, 6),
            round(self.cfg.lon, 6),
            round(self.cfg.alt_m, 1),
            self.tle_l1,
            self.tle_l2,
            round(self.cfg.pan_offset_deg, 3),
            round(self.cfg.tilt_offset_deg, 3),
            bool(self.cfg.pan_dmx_increases_ccw),
            round(self.cfg.pan_max_deg, 3),
            json.dumps(self.cfg.pan_lut_points, ensure_ascii=True, sort_keys=True),
        )

    def get_path_points(self) -> Dict[str, Any]:
        try:
            self._ensure_satellite()
        except Exception as e:
            return {"ok": False, "error": str(e), "points": []}

        key = self._path_cache_key()
        now = time.time()
        if self.path_cache["key"] == key and (now - self.path_cache["timestamp"]) < 10 and self.path_cache["result"] is not None:
            return self.path_cache["result"]

        obs = self._observer()
        t0 = self.ts.now()
        t1 = self.ts.utc((dt.datetime.utcnow() + dt.timedelta(days=1)).replace(tzinfo=dt.timezone.utc))

        try:
            topoc_now = (self.sat - obs).at(t0)
            alt_now, _, _ = topoc_now.altaz()
            el_now = float(alt_now.degrees)
            currently_up = el_now >= 0.0
        except Exception as e:
            return {"ok": False, "error": f"คำนวณสถานะปัจจุบันไม่สำเร็จ: {e}", "points": []}

        try:
            times, events = self.sat.find_events(obs, t0, t1, altitude_degrees=0.0)
        except Exception as e:
            return {"ok": False, "error": f"ค้นหา AOS/LOS ไม่สำเร็จ: {e}", "points": []}

        aos_t = None
        los_t = None

        if currently_up:
            for tt, ev in zip(times, events):
                if int(ev) == 2:
                    los_t = tt
                    break
            aos_t = t0
        else:
            for i, (tt, ev) in enumerate(zip(times, events)):
                if int(ev) == 0:
                    aos_t = tt
                    for tt2, ev2 in zip(times[i+1:], events[i+1:]):
                        if int(ev2) == 2:
                            los_t = tt2
                            break
                    break

        if aos_t is None or los_t is None:
            res = {"ok": True, "points": [], "aos": None, "los": None, "note": "ไม่พบ pass ใน 24 ชั่วโมงถัดไป"}
            self.path_cache.update({"key": key, "timestamp": now, "result": res})
            return res

        step_sec = 10
        aos_dt = aos_t.utc_datetime().replace(tzinfo=dt.timezone.utc)
        los_dt = los_t.utc_datetime().replace(tzinfo=dt.timezone.utc)

        total = max(0, int((los_dt - aos_dt).total_seconds()))
        if total <= 0:
            res = {"ok": True, "points": [], "aos": aos_dt.isoformat(), "los": los_dt.isoformat(), "note": "pass time window invalid"}
            self.path_cache.update({"key": key, "timestamp": now, "result": res})
            return res

        max_points = 2500
        count = min(max_points, int(total // step_sec) + 1)
        dts = [aos_dt + dt.timedelta(seconds=i * step_sec) for i in range(count)]
        if dts[-1] < los_dt and len(dts) < max_points:
            dts.append(los_dt)

        try:
            if hasattr(self.ts, "from_datetimes"):
                sf_times = self.ts.from_datetimes(dts)
            else:
                years = [d.year for d in dts]
                months = [d.month for d in dts]
                days = [d.day for d in dts]
                hours = [d.hour for d in dts]
                minutes_arr = [d.minute for d in dts]
                seconds = [d.second + d.microsecond / 1e6 for d in dts]
                sf_times = self.ts.utc(years, months, days, hours, minutes_arr, seconds)
        except Exception as e:
            return {"ok": False, "error": f"สร้างเวลาให้ Skyfield ไม่สำเร็จ: {e}", "points": []}

        try:
            topoc = (self.sat - obs).at(sf_times)
            alt, az, _ = topoc.altaz()
        except Exception as e:
            return {"ok": False, "error": f"คำนวณเส้นทางไม่สำเร็จ: {e}", "points": []}

        points = []
        for a, z in zip(alt.degrees, az.degrees):
            el = float(a)
            azd = float(z)
            if el >= 0.0:
                points.append({"az": azd, "el": el})

        # pass summary: maximum elevation and its time (TCA-like; at max elevation)
        max_el = None
        tca_dt = None
        try:
            el_all = [float(a) for a in alt.degrees]
            if el_all:
                max_el = max(el_all)
                idx2 = el_all.index(max_el)
                if 0 <= idx2 < len(dts):
                    tca_dt = dts[idx2]
        except Exception:
            pass

        res = {
            "ok": True,
            "points": points,
            "aos": aos_dt.isoformat(),
            "los": los_dt.isoformat(),
            "currently_up": currently_up,
            "max_el": max_el,
            "tca": (tca_dt.isoformat() if tca_dt else None),
            "step_seconds": step_sec,
        }
        self.path_cache.update({"key": key, "timestamp": now, "result": res})
        return res


tracker = Tracker()
app = Flask(__name__)

# -------------------------
# Static: ding.mp3
# -------------------------
DING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ding.mp3")


@app.get("/ding.mp3")
def ding_mp3():
    if not os.path.exists(DING_PATH):
        abort(404)
    return send_file(DING_PATH, mimetype="audio/mpeg", as_attachment=False)


# ------------------------------------------------------------
# HTML (Improved Layout)
# ------------------------------------------------------------
INDEX_HTML = r"""
<!doctype html>
<html lang="th">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>SatFinder : Satellite Beam Tracker Lab</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background: radial-gradient(1200px 800px at 20% 10%, rgba(99,102,241,.25), transparent 60%),
                     radial-gradient(1000px 700px at 80% 20%, rgba(34,197,94,.18), transparent 55%),
                     radial-gradient(900px 650px at 60% 90%, rgba(250,204,21,.12), transparent 50%),
                     #070A12; }
    .card { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); }
    .chip { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12); }
    .glow { box-shadow: 0 0 40px rgba(99,102,241,.18); }
    canvas { background: rgba(0,0,0,0.18); border: 1px solid rgba(255,255,255,0.12); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .hint { font-size: 12px; color: rgba(255,255,255,.62); }

    .tbl { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; border-radius: 14px; }
    .tbl thead th { font-size: 12px; color: rgba(255,255,255,.75); text-align: left; padding: 10px 10px; background: rgba(255,255,255,.06); border-bottom: 1px solid rgba(255,255,255,.10); }
    .tbl tbody td { padding: 10px 10px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: middle; }
    .tbl tbody tr:hover { background: rgba(255,255,255,.06); }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.14); font-size: 12px; color: rgba(255,255,255,.85); }
  </style>
</head>

<body class="text-white min-h-screen">
  <audio id="ding" src="/ding.mp3" preload="auto"></audio>

  <div class="max-w-7xl mx-auto px-4 py-5">
    <!-- Header -->
    <header class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
      <div>
        <h1 class="text-3xl font-extrabold tracking-tight">SatFinder : Satellite Beam Tracker Lab</h1>
        <p class="text-white/70 mt-1">
          เรียนรู้แนวคิด <span class="font-semibold">Azimuth</span> (มุมทิศ) และ <span class="font-semibold">Elevation</span> (มุมเงย)
          ด้วยการ “ชี้ลำแสง” เหมือนสถานีภาคพื้นดินติดตามดาวเทียม
        </p>
        <div class="mt-2 flex flex-wrap gap-2">
          <span class="chip text-xs px-2 py-1 rounded-full" id="locStatus">โหลดค่าเดิมแล้ว</span>
          <span class="chip text-xs px-2 py-1 rounded-full" id="runMode">โหมด: idle</span>
          <span class="chip text-xs px-2 py-1 rounded-full" id="simChip">ฮาร์ดแวร์: uDMX</span>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <button id="btnBlackout" class="px-4 py-2 rounded-xl bg-rose-500/85 hover:bg-rose-500 font-semibold">ปิดไฟ</button>
        <button id="btnStop" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">Stop</button>
        <button id="btnTrack" class="px-4 py-2 rounded-xl bg-sky-500/85 hover:bg-sky-500 font-semibold">เริ่มติดตาม (Tracking)</button>
        <button id="btnManual" class="px-4 py-2 rounded-xl bg-amber-500/85 hover:bg-amber-500 font-semibold">โหมดทดลอง (Manual Lab)</button>
      </div>
    </header>

    <!-- Main: balanced 2-column (left controls + right visualization) -->
    <section class="grid lg:grid-cols-2 gap-4 mt-5 items-start">
      <!-- LEFT: Location + Satellite -->
      <div class="space-y-4">
        <div class="card rounded-2xl p-4 glow">
          <h2 class="text-xl font-bold">ขั้นที่ 1) ตำแหน่งผู้สังเกต (Location)</h2>
          <p class="text-white/70 text-sm mt-1">ทิศทางที่เห็นดาวเทียมจะเปลี่ยนไปตามตำแหน่งของเราบนโลก</p>

          <div class="mt-4 grid grid-cols-3 gap-3">
            <div>
              <label class="text-sm text-white/70">Latitude</label>
              <input id="lat" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
            <div>
              <label class="text-sm text-white/70">Longitude</label>
              <input id="lon" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
            <div>
              <label class="text-sm text-white/70">Altitude (m)</label>
              <input id="alt_m" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
          </div>

          <div class="mt-3 flex flex-wrap gap-2">
            <button id="btnGeo" class="px-4 py-2 rounded-xl bg-emerald-500/85 hover:bg-emerald-500 font-semibold">ใช้ตำแหน่งของฉัน (Geolocation)</button>
            <button id="btnSaveLoc" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">บันทึกตำแหน่ง</button>
          </div>

          <div class="mt-3 hint">
            แนะนำ: ถ้าสาธิตในโรงเรียน ให้บันทึกตำแหน่งไว้ครั้งเดียว แล้วใช้ซ้ำได้
          </div>
        </div>

        <div class="card rounded-2xl p-4">
          <h2 class="text-xl font-bold">ขั้นที่ 2) เลือกดาวเทียม + TLE</h2>
          <p class="text-white/70 text-sm mt-1">
            พิมพ์ชื่อแบบไม่ต้องตรงเป๊ะ แล้วกดค้นหา จากนั้น “คลิกเลือกในตาราง” เพื่อยืนยันดาวเทียมทันที
          </p>

          <div class="mt-4 grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-white/70">ชื่อดาวเทียม (ค้นหาแบบคล้าย)</label>
              <input id="sat_query" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" placeholder="เช่น Tianqi-22" />
            </div>
            <div>
              <label class="text-sm text-white/70">NORAD ID (ถ้ารู้)</label>
              <input id="norad_id" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" placeholder="เช่น 25544" />
            </div>
          </div>

          <div class="mt-3 flex flex-wrap gap-2">
            <button id="btnSearchSat" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">ค้นหาชื่อดาวเทียม</button>
            <button id="btnFetchTLE" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">ดึง TLE</button>
          </div>

          <div class="mt-3">
            <div class="text-sm text-white/70">ผลค้นหา (คลิกแถวเพื่อเลือก):</div>

            <div class="mt-2 overflow-hidden rounded-2xl border border-white/10">
              <div class="max-h-[360px] overflow-auto">
                <table class="tbl" aria-label="ผลค้นหาดาวเทียม">
                  <thead class="sticky top-0 backdrop-blur">
                    <tr>
                      <th style="width:56px;">เลือก</th>
                      <th>ชื่อ (OBJECT_NAME)</th>
                      <th style="width:140px;">NORAD</th>
                      <th style="width:120px;">ความมั่นใจ</th>
                    </tr>
                  </thead>
                  <tbody id="satResultsBody">
                    <tr>
                      <td colspan="4" class="hint">— ยังไม่มีผลค้นหา —</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="mt-2 text-xs text-white/60" id="satPickHint">ยังไม่ได้เลือกจากตาราง</div>
          </div>

          <div class="mt-4">
            <div class="text-sm text-white/70">กำลังใช้งาน:</div>
            <div class="text-lg font-bold" id="satName">—</div>
            <div class="text-xs text-white/60 mt-1" id="errBox"></div>
            <div class="text-xs text-white/60 mt-1" id="autoNote"></div>
          </div>

          <div class="mt-4 card rounded-xl p-3">
            <div class="text-sm font-semibold">TLE (Two-Line Element)</div>
            <div class="text-xs text-white/60 mt-1">ระบบจะคำนวณตำแหน่งดาวเทียมจาก TLE ในเครื่อง</div>
            <pre id="tleBox" class="mono text-xs text-white/80 mt-2 whitespace-pre-wrap break-words">—</pre>
          </div>
        </div>
      </div>

      <!-- RIGHT: Sky Dome + DMX output -->
      <div class="space-y-4">
        <div class="card rounded-2xl p-4">
          <h2 class="text-xl font-bold">Sky Dome (Azimuth/Elevation)</h2>
          <p class="text-white/70 text-sm mt-1">
            จุดกึ่งกลาง = เงย 90° (เหนือหัว) | ขอบวง = เงย 0° (ขอบฟ้า) | รอบวง = มุมทิศ (0° = เหนือ)
          </p>
          <div class="mt-3">
            <canvas id="sky" width="640" height="640" class="rounded-2xl w-full h-auto"></canvas>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-3">
            <div class="card rounded-xl p-3">
              <div class="text-xs text-white/60">Azimuth (°)</div>
              <div class="text-2xl font-extrabold" id="azVal">—</div>
            </div>
            <div class="card rounded-xl p-3">
              <div class="text-xs text-white/60">Elevation (°)</div>
              <div class="text-2xl font-extrabold" id="elVal">—</div>
            </div>
          </div>

          <div class="mt-3 text-xs text-white/70" id="passWindow">Pass: —</div>
          <div class="mt-1 text-xs text-white/60">
            เส้นทางที่วาดคือ “ช่วงที่ดาวเทียมอยู่เหนือขอบฟ้า (Elevation ≥ 0°)” ตั้งแต่พ้นขอบฟ้าถึงลับขอบฟ้า
          </div>
        </div>

        <div class="card rounded-2xl p-4">
          <h2 class="text-xl font-bold">เอาต์พุต Moving Head (DMX)</h2>
          <p class="text-white/70 text-sm mt-1">ระบบแปลงมุมบนท้องฟ้าเป็นค่า DMX ของ Pan/Tilt (0–255)</p>

          <div class="mt-4 grid grid-cols-2 gap-3">
            <div class="card rounded-xl p-3">
              <div class="text-xs text-white/60">Pan DMX (CH1)</div>
              <div class="text-2xl font-extrabold" id="panDmx">—</div>
              <div class="text-xs text-white/50 mt-1" id="panDeg">—</div>
            </div>
            <div class="card rounded-xl p-3">
              <div class="text-xs text-white/60">Tilt DMX (CH2)</div>
              <div class="text-2xl font-extrabold" id="tiltDmx">—</div>
              <div class="text-xs text-white/50 mt-1" id="tiltDeg">—</div>
            </div>
          </div>

          <div class="mt-4 grid grid-cols-3 gap-3">
            <div>
              <label class="text-sm text-white/70">DMX Start Addr</label>
              <input id="base_addr" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
            <div>
              <label class="text-sm text-white/70">Pan offset (deg)</label>
              <input id="pan_offset_deg" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
            <div>
              <label class="text-sm text-white/70">Tilt offset (deg)</label>
              <input id="tilt_offset_deg" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-white/70">ความถี่ส่งออก (Hz)</label>
              <input id="output_hz" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            </div>
            <div class="flex items-end gap-2">
              <label class="inline-flex items-center gap-2">
                <input id="simulate_only" type="checkbox" class="scale-125">
                <span class="text-sm text-white/70">จำลองอย่างเดียว (ไม่ส่ง uDMX)</span>
              </label>
            </div>
          </div>

          <div class="mt-3">
            <label class="inline-flex items-center gap-2">
              <input id="pan_dmx_increases_ccw" type="checkbox" class="scale-125">
              <span class="text-sm text-white/70">Pan: เมื่อ DMX เพิ่ม = หมุนซ้าย (ทวนเข็ม)</span>
            </label>
          </div>

          <div class="mt-3">
            <label class="text-sm text-white/70">Pan Max (deg) ที่ระบบใช้ (ได้จาก Calibration)</label>
            <input id="pan_max_deg" class="w-full mt-1 px-3 py-2 rounded-xl bg-black/30 border border-white/10" />
            <div class="hint mt-1">ค่าเริ่มต้นถูกฝังไว้แล้วตามที่คุณวัด (≈ 452.961°)</div>
          </div>

          <div class="mt-3 flex gap-2">
            <button id="btnSaveOut" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">บันทึกการตั้งค่าเอาต์พุต</button>
            <button id="btnRefreshPath" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">อัปเดตเส้นทาง</button>
          </div>
        </div>
      </div>
    </section>

    <!-- Bottom: Manual + Calibration full width -->
    <section class="grid lg:grid-cols-2 gap-4 mt-4">
      <div class="card rounded-2xl p-4">
        <h3 class="text-xl font-bold">โหมดทดลอง (Manual Lab)</h3>
        <p class="text-white/70 text-sm mt-1">
          ปรับเป็น “มุมบนท้องฟ้า”:
          <span class="font-semibold">Azimuth 0–360°</span> (0° = เหนือ, ตามเข็ม),
          <span class="font-semibold">Elevation 0–90°</span> (0° = ขอบฟ้า)
        </p>

        <div class="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label class="text-sm text-white/70">Manual Azimuth (0–360°)</label>
            <input id="manual_az_deg" type="range" min="0" max="360" step="1" class="w-full">
            <div class="text-sm mt-1" id="manualAzLabel">—</div>
            <div class="hint" id="manualAzToPanHint">—</div>
          </div>
          <div>
            <label class="text-sm text-white/70">Manual Elevation (0–90°)</label>
            <input id="manual_el_deg" type="range" min="0" max="90" step="1" class="w-full">
            <div class="text-sm mt-1" id="manualElLabel">—</div>
            <div class="hint" id="manualElToTiltHint">—</div>
          </div>
        </div>

        <div class="mt-3 card rounded-xl p-3">
          <div class="text-sm font-semibold">ผลการแปลง (ภายในระบบ)</div>
          <div class="mt-2 text-sm" id="manualDerivedSummary">—</div>
        </div>

        <div class="mt-3 grid md:grid-cols-2 gap-3">
          <div>
            <label class="text-sm text-white/70">Dimmer</label>
            <input id="dimmer" type="range" min="0" max="255" class="w-full">
            <div class="text-sm mt-1" id="dimmerLabel">—</div>
            <div class="hint">เริ่มต้นเป็น 255 เพื่อกันพลาด (ไฟติดสว่างสุด)</div>
          </div>
          <div>
            <label class="text-sm text-white/70">White</label>
            <input id="w" type="range" min="0" max="255" class="w-full">
            <div class="text-sm mt-1" id="whiteLabel">—</div>
            <div class="hint">เริ่มต้นเป็น 255 เพื่อกันพลาด (ไฟติดสว่างสุด)</div>
          </div>
        </div>

        <div class="mt-3 flex flex-wrap gap-2">
          <button id="btnSaveBrightness" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">บันทึกค่าความสว่าง</button>
          <button id="btnPushManual" class="px-4 py-2 rounded-xl bg-amber-500/85 hover:bg-amber-500 font-semibold">เริ่มส่ง Manual Output</button>
        </div>
      </div>

      <div class="card rounded-2xl p-4">
        <h3 class="text-xl font-bold">Calibration Lab (Pan 90/180/270/360)</h3>
        <p class="text-white/70 text-sm mt-1">
          เป้าหมายของ Lab นี้คือ “วัด” ความสัมพันธ์ระหว่าง <span class="font-semibold">องศา Pan</span> กับ <span class="font-semibold">ค่า DMX (0–255)</span>
          แล้วให้ระบบสร้างโมเดลแปลงองศา→DMX อัตโนมัติ (เป็น LUT แบบแบ่งช่วง) เพื่อใช้ใน Manual/Tracking
        </p>

        <div class="mt-3 flex flex-wrap gap-2">
          <button id="btnCalStart" class="px-4 py-2 rounded-xl bg-emerald-500/85 hover:bg-emerald-500 font-semibold">เริ่ม Calibration Lab (กลับ Pan=0)</button>
          <button id="btnCalClear" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">ล้างค่าที่วัด (Session)</button>
          <button id="btnCalDefaults" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">กลับค่า Default</button>
        </div>

        <div class="mt-3 card rounded-xl p-3">
          <div class="text-sm font-semibold">1) เลือกมุมเป้าหมาย แล้วปรับค่า DMX ให้ตรงมุมจริง</div>
          <div class="hint mt-1">
            แนะนำให้มี “สเกลมุม” รอบฐาน (เช่น กระดาษวงกลม) เพื่อให้นักเรียนอ่านมุมได้ง่าย
            โดยนิยามว่า <span class="font-semibold">Pan=0°</span> คือทิศที่คุณตั้งเป็น “ทิศเหนือ”
            และมุมเพิ่มขึ้นไปทางซ้ายตามธรรมชาติของ Moving Head ตัวนี้
          </div>

          <div class="mt-3 overflow-hidden rounded-2xl border border-white/10">
            <table class="tbl" aria-label="จุดคาลิเบรต Pan">
              <thead class="sticky top-0 backdrop-blur">
                <tr>
                  <th style="width:120px;">มุมเป้าหมาย</th>
                  <th>ค่า DMX ที่บันทึก</th>
                  <th style="width:160px;">จัดการ</th>
                </tr>
              </thead>
              <tbody id="calibTableBody">
                <tr><td colspan="3" class="hint">—</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="mt-3 card rounded-xl p-3">
          <div class="text-sm font-semibold">2) ปรับ DMX สำหรับมุม <span id="calStepDeg" class="pill">90</span></div>
          <div class="hint mt-1">
            ลากสไลเดอร์ แล้วสังเกตว่าลำแสงหันไปถึงมุมเป้าหมายหรือยัง จากนั้นกด “บันทึก DMX ของมุมนี้”
          </div>

          <div class="mt-3">
            <label class="text-sm text-white/70">Pan DMX (0–255)</label>
            <input id="calPanSlider" type="range" min="0" max="255" step="1" class="w-full">
            <div class="text-sm mt-1" id="calPanSliderLabel">—</div>
          </div>

          <div class="mt-3 flex flex-wrap gap-2">
            <button id="btnCalSavePoint" class="px-4 py-2 rounded-xl bg-indigo-500/85 hover:bg-indigo-500 font-semibold">บันทึก DMX ของมุมนี้</button>
            <button id="btnCalPanZero" class="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 font-semibold">กลับ Pan=0</button>
          </div>
        </div>

        <div class="mt-3 card rounded-xl p-3">
          <div class="text-sm font-semibold">3) สร้างโมเดล (องศา→DMX) อัตโนมัติ แล้วบันทึก</div>
          <div class="hint mt-1">
            ระบบจะสร้าง LUT แบบแบ่งช่วง 0/90/180/270/360 แล้วประมาณค่า <span class="font-semibold">Pan Max (deg)</span>
            ด้วยการ extrapolate จากช่วง 270→360 ไปจนถึง DMX=255 (เหมาะกับพฤติกรรมมอเตอร์ช่วงปลายมากกว่าเส้นตรงทั้งช่วง)
          </div>

          <div class="mt-3 flex flex-wrap gap-2">
            <button id="btnCalCommit" class="px-4 py-2 rounded-xl bg-amber-500/85 hover:bg-amber-500 font-semibold">คำนวณและบันทึก Calibration</button>
          </div>

          <div class="mt-3 text-xs text-white/70" id="calibResult">—</div>
        </div>
      </div>
    </section>

    <footer class="mt-8 text-xs text-white/60 whitespace-pre-line">
โครงการส่งเสริมการเรียนรู้ทางด้านโทรคมนาคมในโรงเรียนทั่วประเทศ
โดย คณะวิศวกรรมศาสตร์ มหาวิทยาลัยธรรมศาสตร์ และ สถาบันวิจัยและให้คำปรึกษาแห่งมหาวิทยาลัยธรรมศาสตร์
สนับสนุนโดย กองทุนวิจัยและพัฒนากิจการกระจายเสียง กิจการโทรทัศน์ และกิจการโทรคมนาคม เพื่อประโยชน์สาธารณะ
    </footer>
  </div>

  <div id="modalNorth" class="fixed inset-0 hidden items-center justify-center bg-black/60">
    <div class="card rounded-2xl p-5 max-w-lg w-[92%]">
      <div class="text-xl font-bold">ยืนยันการหันไปทางทิศเหนือ</div>
      <p class="text-white/80 text-sm mt-2">
        ระบบได้สั่ง Moving Head ไปที่ <span class="font-semibold">Pan DMX = 0</span> และ <span class="font-semibold">Tilt DMX = 0</span>
        (ขนานกับพื้นโลก)
      </p>
      <div class="mt-3 p-3 rounded-xl bg-black/30 border border-white/10">
        <div class="font-semibold">ขั้นตอน</div>
        <ol class="list-decimal ml-5 text-sm text-white/80 mt-1 space-y-1">
          <li>หมุน/จัดฐานอุปกรณ์ให้ “หันไปทางทิศเหนือ”</li>
          <li>เมื่อจัดเสร็จแล้ว กด <span class="font-semibold">ยืนยัน</span> เพื่อเริ่ม Tracking</li>
        </ol>
      </div>
      <div class="mt-4 flex gap-2 justify-end">
        <button id="modalNorthConfirm" class="px-4 py-2 rounded-xl bg-indigo-500/85 hover:bg-indigo-500 font-semibold">ยืนยัน</button>
      </div>
    </div>
  </div>

<script>
  const $ = (id) => document.getElementById(id);

  let pathPoints = [];
  let satResultItems = [];
  let selectedSatIndex = null;

  let dingTimer = null;
  const ding = $("ding");

  let pendingStartTracking = false;

  async function apiGet(path){
    const r = await fetch(path);
    return await r.json();
  }
  async function apiPost(path, obj){
    const r = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(obj || {})
    });
    return await r.json();
  }

  function updateSimChip(sim){
    $("simChip").textContent = sim ? "ฮาร์ดแวร์: จำลอง" : "ฮาร์ดแวร์: uDMX";
  }

  function stopDing(){
    if (dingTimer){
      clearInterval(dingTimer);
      dingTimer = null;
    }
    try{
      ding.pause();
      ding.currentTime = 0;
    }catch(e){}
  }

  function startDing(){
    if (dingTimer) return;
    const playOnce = async () => {
      try{
        ding.currentTime = 0;
        await ding.play();
      }catch(e){}
    };
    playOnce();
    dingTimer = setInterval(playOnce, 10000);
  }

  function escapeHtml(s){
    return (s || "").replace(/[&<>"']/g, function(m){
      return ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#039;"
      })[m];
    });
  }

  async function applySessionSatellite(){
    const sat_query = $("sat_query").value || "";
    const norad_raw = ($("norad_id").value || "").trim();
    const norad_id = norad_raw ? parseInt(norad_raw) : null;

    const r = await apiPost("/api/set_sat", { sat_query, norad_id });
    if (!r.ok){
      $("errBox").textContent = "Error: " + (r.error || "กำหนดดาวเทียมไม่สำเร็จ");
      return null;
    }
    $("errBox").textContent = "";
    if (r.sat_official_name && !norad_raw){
      $("autoNote").textContent = `ระบบจับคู่ชื่อเป็น: ${r.sat_official_name} (NORAD ${r.norad_id})`;
      $("norad_id").value = r.norad_id;
    } else {
      $("autoNote").textContent = "";
    }
    return r;
  }

  function renderSatResultsTable(items){
    const body = $("satResultsBody");
    satResultItems = items || [];
    selectedSatIndex = null;
    $("satPickHint").textContent = "ยังไม่ได้เลือกจากตาราง";
    body.innerHTML = "";

    if (!satResultItems.length){
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="4" class="hint">— ไม่มีผลลัพธ์ —</td>`;
      body.appendChild(tr);
      return;
    }

    satResultItems.forEach((it, idx) => {
      const tr = document.createElement("tr");
      tr.className = "cursor-pointer";
      const cbId = `sat_cb_${idx}`;

      tr.innerHTML = `
        <td><input id="${cbId}" type="checkbox" class="scale-125" /></td>
        <td><div class="font-semibold">${escapeHtml(it.name)}</div></td>
        <td><span class="pill">NORAD ${it.norad_id}</span></td>
        <td><span class="pill">${it.score}</span></td>
      `;

      const chooseRow = async () => {
        for (let j=0; j<satResultItems.length; j++){
          const el = document.getElementById(`sat_cb_${j}`);
          if (el) el.checked = false;
        }
        const cb = document.getElementById(cbId);
        if (cb) cb.checked = true;

        selectedSatIndex = idx;
        $("satPickHint").textContent = `เลือกแล้ว: ${it.name} (NORAD ${it.norad_id})`;

        $("sat_query").value = it.name;
        $("norad_id").value = it.norad_id;

        const r = await apiPost("/api/set_sat", { sat_query: it.name, norad_id: it.norad_id });
        if (!r.ok){
          $("errBox").textContent = "Error: " + (r.error || "ยืนยันไม่สำเร็จ");
          return;
        }
        $("errBox").textContent = "";
        $("autoNote").textContent = `ยืนยันแล้ว: ${it.name} (NORAD ${it.norad_id})`;

        await refreshTLE();
        await refreshPath();
      };

      tr.addEventListener("click", async (ev) => {
        if (ev.target && ev.target.tagName && ev.target.tagName.toLowerCase() === "input") return;
        await chooseRow();
      });
      tr.querySelector(`#${cbId}`).addEventListener("click", async (ev) => {
        ev.stopPropagation();
        await chooseRow();
      });

      body.appendChild(tr);
    });
  }

  function drawSky(az, el){
    const c = $("sky");
    const ctx = c.getContext("2d");
    const W = c.width, H = c.height;
    ctx.clearRect(0,0,W,H);

    const cx=W/2, cy=H/2;
    const R=Math.min(W,H)*0.45;

    ctx.globalAlpha=1.0;
    ctx.strokeStyle="rgba(255,255,255,0.16)";
    ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.stroke();

    for (const e of [30,60]){
      const r = R*(1 - e/90);
      ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.stroke();
      ctx.fillStyle="rgba(255,255,255,0.35)";
      ctx.font="12px sans-serif";
      ctx.fillText(`${e}°`, cx+6, cy-r-6);
    }

    ctx.fillStyle="rgba(255,255,255,0.70)";
    ctx.font="14px sans-serif";
    ctx.fillText("N", cx-6, cy-R-10);
    ctx.fillText("E", cx+R+8, cy+5);
    ctx.fillText("S", cx-6, cy+R+20);
    ctx.fillText("W", cx-R-20, cy+5);

    if (pathPoints && pathPoints.length >= 2){
      ctx.strokeStyle="rgba(99,102,241,0.55)";
      ctx.lineWidth=2.5;
      ctx.beginPath();
      let first = true;
      for (const p of pathPoints){
        const azp = p.az, elp = p.el;
        const r = R*(1 - Math.max(0, Math.min(90, elp))/90);
        const theta = (azp-90) * Math.PI/180;
        const x = cx + r*Math.cos(theta);
        const y = cy + r*Math.sin(theta);
        if (first){
          ctx.moveTo(x,y);
          first = false;
        } else {
          ctx.lineTo(x,y);
        }
      }
      ctx.stroke();
    }

    if (az === null || el === null || isNaN(az) || isNaN(el)) return;

    const rr = R*(1 - Math.max(0, Math.min(90, el))/90);
    const theta = (az-90) * Math.PI/180;
    const x = cx + rr*Math.cos(theta);
    const y = cy + rr*Math.sin(theta);

    ctx.fillStyle="rgba(34,197,94,0.95)";
    ctx.beginPath(); ctx.arc(x,y,7,0,Math.PI*2); ctx.fill();
    ctx.fillStyle="rgba(34,197,94,0.25)";
    ctx.beginPath(); ctx.arc(x,y,16,0,Math.PI*2); ctx.fill();
  }

  async function refreshState(){
    const s = await apiGet("/api/state");
    $("runMode").textContent = `โหมด: ${s.mode}`;
    if (s.error) $("errBox").textContent = "Error: " + s.error;

    const displayName = s.sat_official_name || (s.tle && s.tle.satname) || s.sat_query || "—";
    const norad = s.norad_id ? ` (NORAD ${s.norad_id})` : "";
    $("satName").textContent = (displayName === "—") ? "—" : (displayName + norad);

    $("azVal").textContent = (s.az_deg==null) ? "—" : s.az_deg.toFixed(1);
    $("elVal").textContent = (s.el_deg==null) ? "—" : s.el_deg.toFixed(1);

    $("panDmx").textContent  = (s.pan_dmx==null) ? "—" : s.pan_dmx;
    $("tiltDmx").textContent = (s.tilt_dmx==null) ? "—" : s.tilt_dmx;

    $("panDeg").textContent  = (s.pan_deg==null) ? "—" : `Pan ≈ ${s.pan_deg.toFixed(1)}°`;
    $("tiltDeg").textContent = (s.tilt_deg==null) ? "—" : `Tilt ≈ ${s.tilt_deg.toFixed(1)}°`;

    if (s.tle && s.tle.l1 && s.tle.l2){
      $("tleBox").textContent = `${s.tle.satname}\n${s.tle.l1}\n${s.tle.l2}`;
    }

    drawSky(s.az_deg, s.el_deg);

    if (s.mode === "tracking" && s.el_deg != null && s.el_deg >= 0){
      startDing();
    } else {
      stopDing();
    }
  }

  async function refreshTLE(){
    const t = await apiGet("/api/tle");
    if (!t.ok){
      $("tleBox").textContent = "ดึง TLE ไม่สำเร็จ: " + (t.error || "");
      return;
    }
    if (t.tle && t.tle.l1 && t.tle.l2){
      $("tleBox").textContent = `${t.tle.satname}\n${t.tle.l1}\n${t.tle.l2}`;
    }
  }

  function fmtLocal(iso){
    try{
      const d = new Date(iso);
      return d.toLocaleString();
    }catch(e){
      return iso;
    }
  }

  async function refreshPath(){
    try{
      const res = await apiGet("/api/path");
      if (!res.ok){
        return;
      }
      pathPoints = res.points || [];
      if (res.aos && res.los){
        const extra = (res.max_el!=null) ? ` | Max EL ${res.max_el.toFixed(1)}° @ ${fmtLocal(res.tca)}` : "";
        $("passWindow").textContent = `Pass: AOS ${fmtLocal(res.aos)}  →  LOS ${fmtLocal(res.los)}  (step ${res.step_seconds}s)${extra}`;
      } else {
      }
      const s = await apiGet("/api/state");
      drawSky(s.az_deg, s.el_deg);
    }catch(e){}
  }

  function updateManualLabels(cfg){
    const az = $("manual_az_deg").value;
    const el = $("manual_el_deg").value;

    $("manualAzLabel").textContent = `Azimuth: ${az}°`;
    $("manualElLabel").textContent = `Elevation: ${el}°`;

    $("dimmerLabel").textContent = `Dimmer: ${$("dimmer").value}`;
    $("whiteLabel").textContent = `White: ${$("w").value}`;

    $("manualDerivedSummary").textContent = `Az ${az}° / El ${el}°  ⇒  ระบบจะคำนวณ Pan/Tilt และ DMX โดยใช้ pan_max_deg=${Number(cfg.pan_max_deg).toFixed(3)} และ LUT (ถ้ามี)`;
    $("manualAzToPanHint").textContent = `ค่าเริ่มต้นของ Pan mapping ถูกตั้งจากการวัดของคุณแล้ว`;
    $("manualElToTiltHint").textContent = `Tilt ใช้สเกล 0..180 → DMX 0..255 แบบเชิงเส้น`;
  }

  async function setInputs(cfg){
    $("lat").value = cfg.lat;
    $("lon").value = cfg.lon;
    $("alt_m").value = cfg.alt_m;

    $("base_addr").value = cfg.base_addr;
    $("pan_offset_deg").value = cfg.pan_offset_deg;
    $("tilt_offset_deg").value = cfg.tilt_offset_deg;
    $("output_hz").value = cfg.output_hz;
    $("simulate_only").checked = cfg.simulate_only;
    $("pan_dmx_increases_ccw").checked = !!cfg.pan_dmx_increases_ccw;

    $("pan_max_deg").value = cfg.pan_max_deg;

    $("manual_az_deg").value = cfg.manual_az_deg ?? 0;
    $("manual_el_deg").value = cfg.manual_el_deg ?? 0;

    // SAFE UI defaults
    const dim = (cfg.dimmer==null || isNaN(cfg.dimmer)) ? 255 : cfg.dimmer;
    const wv  = (cfg.w==null || isNaN(cfg.w)) ? 255 : cfg.w;

    $("dimmer").value = dim;
    $("w").value = wv;

    // Initialize calibration slider label (session data loaded separately)
    try{
      $("calPanSlider").value = 0;
      $("calPanSliderLabel").textContent = "Pan DMX: 0";
    }catch(e){}

    updateSimChip(cfg.simulate_only);
    updateManualLabels(cfg);
  }

  function openNorthModal(){
    $("modalNorth").classList.remove("hidden");
    $("modalNorth").classList.add("flex");
  }

  function closeNorthModal(){
    $("modalNorth").classList.add("hidden");
    $("modalNorth").classList.remove("flex");
  }

  async function doStartTrackingAfterConfirm(){
    pendingStartTracking = false;

    await $("btnSaveLoc").onclick();
    await $("btnSaveOut").onclick();

    const ok = await applySessionSatellite();
    if (!ok) return;

    await apiPost("/api/start_tracking", {});
    await refreshTLE();
    await refreshPath();
  }

  async function init(){
    const cfg = await apiGet("/api/config");
    await setInputs(cfg);

    $("modalNorthConfirm").onclick = async () => {
      closeNorthModal();
      await doStartTrackingAfterConfirm();
    };

    ["manual_az_deg","manual_el_deg","dimmer","w"].forEach(id=>{
      $(id).addEventListener("input", async ()=>{
        const cfg2 = await apiGet("/api/config");
        updateManualLabels(cfg2);
      });
    });

    $("btnGeo").onclick = async () => {
      if (!navigator.geolocation){
        $("locStatus").textContent = "เบราว์เซอร์นี้ไม่รองรับ Geolocation";
        return;
      }
      $("locStatus").textContent = "กำลังขอตำแหน่ง…";
      navigator.geolocation.getCurrentPosition((pos)=>{
        $("lat").value = pos.coords.latitude;
        $("lon").value = pos.coords.longitude;
        $("locStatus").textContent = "ได้ตำแหน่งแล้ว";
      }, ()=>{
        $("locStatus").textContent = "ขอตำแหน่งไม่สำเร็จ";
      }, {enableHighAccuracy:true, timeout:8000});
    };

    $("btnSaveLoc").onclick = async () => {
      const upd = {
        lat: parseFloat($("lat").value),
        lon: parseFloat($("lon").value),
        alt_m: parseFloat($("alt_m").value)
      };
      await apiPost("/api/config", upd);
      $("locStatus").textContent = "บันทึกตำแหน่งแล้ว";
      await refreshPath();
    };

    $("btnSaveOut").onclick = async () => {
      const upd = {
        base_addr: parseInt($("base_addr").value),
        pan_offset_deg: parseFloat($("pan_offset_deg").value),
        tilt_offset_deg: parseFloat($("tilt_offset_deg").value),
        output_hz: parseFloat($("output_hz").value),
        simulate_only: $("simulate_only").checked,
        pan_dmx_increases_ccw: $("pan_dmx_increases_ccw").checked,
        pan_max_deg: parseFloat($("pan_max_deg").value),
        manual_az_deg: parseFloat($("manual_az_deg").value),
        manual_el_deg: parseFloat($("manual_el_deg").value),
        dimmer: parseInt($("dimmer").value),
        w: parseInt($("w").value)
      };
      await apiPost("/api/config", upd);
      updateSimChip($("simulate_only").checked);
      const cfg2 = await apiGet("/api/config");
      updateManualLabels(cfg2);
      await refreshPath();
    };

    $("btnSaveBrightness").onclick = async () => {
      const upd = {
        dimmer: parseInt($("dimmer").value),
        w: parseInt($("w").value)
      };
      await apiPost("/api/config", upd);
      const cfg2 = await apiGet("/api/config");
      updateManualLabels(cfg2);
    };

    $("btnRefreshPath").onclick = async () => {
      await refreshPath();
    };

    $("btnSearchSat").onclick = async () => {
      const q = ($("sat_query").value || "").trim();
      if (q.length < 2){
        renderSatResultsTable([]);
        return;
      }
      const res = await apiGet(`/api/search_satellites?query=${encodeURIComponent(q)}`);
      if (!res.ok){
        $("errBox").textContent = "Error: " + (res.error || "ค้นหาไม่สำเร็จ");
        renderSatResultsTable([]);
        return;
      }
      $("errBox").textContent = "";
      renderSatResultsTable(res.items || []);
    };

    $("btnFetchTLE").onclick = async () => {
      const ok = await applySessionSatellite();
      if (!ok) return;
      await refreshTLE();
      await refreshPath();
    };

    $("btnTrack").onclick = async () => {
      stopDing();
      await apiPost("/api/stop", {});
      await $("btnSaveOut").onclick();

      // One-shot Set North: send Pan=0 Tilt=0
      await apiPost("/api/set_north_now", {});

      pendingStartTracking = true;
      openNorthModal();
    };

    $("btnManual").onclick = async () => {
      await $("btnSaveOut").onclick();
      await apiPost("/api/start_manual", {});
    };

    $("btnPushManual").onclick = async () => {
      await $("btnManual").onclick();
    };

    $("btnStop").onclick = async () => {
      stopDing();
      pendingStartTracking = false;
      closeNorthModal();
      await apiPost("/api/stop", {});
    };

    $("btnBlackout").onclick = async () => {
      stopDing();
      pendingStartTracking = false;
      closeNorthModal();
      await apiPost("/api/blackout", {});
    };

        // ---- Calibration Lab (Pan 90/180/270/360) ----
    const CAL_DEFAULTS = {90: 50, 180: 101, 270: 152, 360: 203};
    let calStep = 90;
    let calSendTimer = null;

    function calUpdateStepLabel(){
      $("calStepDeg").textContent = calStep + "°";
    }
    function calSetSlider(val){
      $("calPanSlider").value = val;
      $("calPanSliderLabel").textContent = `Pan DMX: ${val}`;
    }

    async function calSendRawNow(val){
      try{
        await apiPost("/api/send_raw_pan", { pan_dmx: val, tilt_dmx: 0 });
      }catch(e){}
    }
    function calThrottleSend(val){
      if (calSendTimer) clearTimeout(calSendTimer);
      calSendTimer = setTimeout(() => calSendRawNow(val), 120);
    }

    function calRenderTable(points){
      const body = $("calibTableBody");
      body.innerHTML = "";
      const degs = [90, 180, 270, 360];
      for (const d of degs){
        const v = (points || {})[d];
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><span class="pill">${d}°</span></td>
          <td>${(v===null || v===undefined) ? '<span class="hint">— ยังไม่บันทึก —</span>' : ('<span class="font-semibold">'+v+'</span>')}</td>
          <td>
            <button class="px-3 py-1.5 rounded-xl bg-white/10 hover:bg-white/15 font-semibold text-sm" data-deg="${d}">ปรับมุมนี้</button>
          </td>
        `;
        body.appendChild(tr);
      }

      body.querySelectorAll("button[data-deg]").forEach(btn => {
        btn.onclick = async () => {
          const d = parseInt(btn.getAttribute("data-deg"));
          calStep = d;
          calUpdateStepLabel();
          const pointsNow = await apiGet("/api/calib_pan/session");
          const v = (pointsNow.points || {})[d];
          const useVal = (v===null || v===undefined) ? (CAL_DEFAULTS[d] || 0) : v;
          calSetSlider(useVal);
          await calSendRawNow(useVal);
        };
      });
    }

    async function calRefreshUI(){
      const ses = await apiGet("/api/calib_pan/session");
      if (!ses.ok){
        $("calibResult").textContent = "Calibration session error: " + (ses.error || "");
        return;
      }
      calStep = ses.step || calStep;
      calUpdateStepLabel();
      calRenderTable(ses.points || {});
      const cur = (ses.points && ses.points[calStep] != null) ? ses.points[calStep] : (CAL_DEFAULTS[calStep] || 0);
      calSetSlider(cur);
    }

    $("calPanSlider").oninput = () => {
      const v = parseInt($("calPanSlider").value);
      $("calPanSliderLabel").textContent = `Pan DMX: ${v}`;
      calThrottleSend(v);
    };

    $("btnCalStart").onclick = async () => {
      // Start a fresh session and force Pan=0 (per user requirement)
      const res = await apiPost("/api/calib_pan/start", {});
      if (!res.ok){
        $("calibResult").textContent = "เริ่ม Calibration ไม่สำเร็จ: " + (res.error || "");
        return;
      }
      $("calibResult").textContent = "เริ่ม Calibration แล้ว (กลับ Pan=0) — เลือกมุม 90° แล้วปรับค่า DMX";
      calStep = 90;
      calUpdateStepLabel();
      calSetSlider(CAL_DEFAULTS[90] || 0);
      await calSendRawNow(parseInt($("calPanSlider").value));
      await calRefreshUI();
    };

    $("btnCalClear").onclick = async () => {
      const res = await apiPost("/api/calib_pan/clear", {});
      if (!res.ok){
        $("calibResult").textContent = "ล้าง Session ไม่สำเร็จ: " + (res.error || "");
        return;
      }
      $("calibResult").textContent = "ล้างค่าที่วัดแล้ว (Session) — เริ่มบันทึกใหม่ได้ทันที";
      calStep = 90;
      calUpdateStepLabel();
      calSetSlider(CAL_DEFAULTS[90] || 0);
      await calRefreshUI();
    };

    $("btnCalDefaults").onclick = async () => {
      const res = await apiPost("/api/calib_pan/defaults", {});
      if (!res.ok){
        $("calibResult").textContent = "กลับค่า Default ไม่สำเร็จ: " + (res.error || "");
        return;
      }
      $("calibResult").textContent = "กลับค่า Default แล้ว (LUT + pan_max_deg)";
      const cfg2 = await apiGet("/api/config");
      $("pan_max_deg").value = cfg2.pan_max_deg;
      updateManualLabels(cfg2);
      await calRefreshUI();
    };

    $("btnCalPanZero").onclick = async () => {
      calSetSlider(0);
      await calSendRawNow(0);
      $("calibResult").textContent = "กลับ Pan=0 แล้ว";
    };

    $("btnCalSavePoint").onclick = async () => {
      const v = parseInt($("calPanSlider").value);
      const res = await apiPost("/api/calib_pan/set_point", { deg: calStep, dmx: v });
      if (!res.ok){
        $("calibResult").textContent = "บันทึกจุดไม่สำเร็จ: " + (res.error || "");
        return;
      }
      $("calibResult").textContent = `บันทึกแล้ว: ${calStep}° → DMX ${v}`;

      // Auto-advance to next step for smoother lab flow
      const next = (calStep < 360) ? (calStep + 90) : 360;
      calStep = next;
      calUpdateStepLabel();
      const nextVal = (res.points && res.points[calStep] != null) ? res.points[calStep] : (CAL_DEFAULTS[calStep] || v);
      calSetSlider(nextVal);
      await calSendRawNow(nextVal);

      await calRefreshUI();
    };

    $("btnCalCommit").onclick = async () => {
      const res = await apiPost("/api/calib_pan/commit", {});
      if (!res.ok){
        $("calibResult").textContent = "คำนวณ/บันทึก Calibration ไม่สำเร็จ: " + (res.error || "");
        return;
      }
      $("calibResult").textContent = `สำเร็จ: pan_max_deg≈${res.pan_max_deg_est}°. ระบบบันทึก LUT แล้ว`;
      const cfg2 = await apiGet("/api/config");
      $("pan_max_deg").value = cfg2.pan_max_deg;
      updateManualLabels(cfg2);
      await calRefreshUI();
    };

    await calRefreshUI();

    renderSatResultsTable([]);

    setInterval(refreshState, 400);
    refreshState();
  }

  init();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/api/config")
def api_get_config():
    return jsonify(tracker.get_config())


@app.post("/api/config")
def api_update_config():
    updates = request.get_json(force=True, silent=True) or {}
    tracker.update_config(updates)
    return jsonify({"ok": True, "config": tracker.get_config()})


@app.get("/api/state")
def api_state():
    return jsonify(tracker.get_state())


@app.get("/api/search_satellites")
def api_search_satellites():
    q = (request.args.get("query") or "").strip()
    try:
        items = SATCAT.search(q, limit=12) if q else []
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "items": []})


@app.post("/api/set_sat")
def api_set_sat():
    payload = request.get_json(force=True, silent=True) or {}
    sat_query = payload.get("sat_query", "")
    norad_id = payload.get("norad_id", None)
    tle_refresh = payload.get("tle_refresh_minutes", None)
    try:
        res = tracker.set_session_satellite(sat_query=sat_query, norad_id=norad_id, tle_refresh_minutes=tle_refresh)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/tle")
def api_tle():
    return jsonify(tracker.get_tle())


@app.get("/api/path")
def api_path():
    return jsonify(tracker.get_path_points())


@app.post("/api/start_tracking")
def api_start_tracking():
    tracker.start_tracking()
    return jsonify({"ok": True})


@app.post("/api/start_manual")
def api_start_manual():
    tracker.start_manual()
    return jsonify({"ok": True})


@app.post("/api/set_north_now")
def api_set_north_now():
    try:
        tracker.set_north_now_once()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/stop")
def api_stop():
    tracker.stop()
    return jsonify({"ok": True})


@app.post("/api/blackout")
def api_blackout():
    tracker.blackout()
    return jsonify({"ok": True})


@app.post("/api/send_raw_pan")
def api_send_raw_pan():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        pan = int(payload.get("pan_dmx", 0))
        tilt = int(payload.get("tilt_dmx", 0))
        tracker.send_raw_pan_tilt(pan, tilt)
        return jsonify({"ok": True, "pan_dmx": pan, "tilt_dmx": tilt})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/auto_calibrate_pan")
def api_auto_calibrate_pan():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        res = tracker.auto_calibrate_pan(
            dmx_90=int(payload.get("dmx_90", 0)),
            dmx_180=int(payload.get("dmx_180", 0)),
            dmx_270=int(payload.get("dmx_270", 0)),
            dmx_360=int(payload.get("dmx_360", 0)),
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# -------------------------
# Calibration Lab API (Pan)
# -------------------------
@app.get("/api/calib_pan/session")
def api_calib_pan_session():
    return jsonify(tracker.calib_pan_get_session())


@app.post("/api/calib_pan/start")
def api_calib_pan_start():
    try:
        return jsonify(tracker.calib_pan_start())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/calib_pan/clear")
def api_calib_pan_clear():
    try:
        return jsonify(tracker.calib_pan_clear())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/calib_pan/set_point")
def api_calib_pan_set_point():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        deg = int(payload.get("deg"))
        dmx = int(payload.get("dmx"))
        return jsonify(tracker.calib_pan_set_point(deg=deg, dmx=dmx))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/calib_pan/commit")
def api_calib_pan_commit():
    try:
        return jsonify(tracker.calib_pan_commit())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/calib_pan/defaults")
def api_calib_pan_defaults():
    try:
        return jsonify(tracker.calib_pan_restore_defaults())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})




if __name__ == "__main__":
    print("Config file:", CONFIG_PATH)
    print("SATCAT cache:", SATCAT_CACHE_PATH)
    print("ding.mp3 path:", DING_PATH)
    print("Open: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
