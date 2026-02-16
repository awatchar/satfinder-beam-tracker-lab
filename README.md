Language: English | [ไทย](./README.th.md)

# SatFinder: Satellite Beam Tracker Lab
![SatBeamTrack Logo](/docs/images/SatBeamTrack.png)

An educational, web-based laboratory for demonstrating satellite motion in the sky. The system visualizes a satellite pass (Sky Dome) and optionally drives a **DMX512 moving-head light** to “point a beam” along the satellite’s real-time **Azimuth/Elevation** track (Az/El → Pan/Tilt), using **TLE/NORAD**-based propagation.

This repository is designed for classroom/STEM delivery: predictable startup behavior, simple operational flow, and a calibration workflow that turns “real hardware imperfections” into a teachable engineering moment.

---

## Screenshot
![Screenshot](/docs/images/Screenshot.png)

---

## Table of Contents

- [What this project does](#what-this-project-does)
- [Key features](#key-features)
- [Requirements](#requirements)
- [Install from GitHub Releases (recommended for schools)](#install-from-github-releases-recommended-for-schools)
- [Windows 11 driver setup for LIXADA/uDMX (VID_16C0&PID_05DC)](#windows-11-driver-setup-for-lixadaudmx-vid_16c0pid_05dc)
- [Quick start (classroom flow)](#quick-start-classroom-flow)
- [Calibration Lab (Pan 90/180/270/360) + auto model](#calibration-lab-pan-90180270360--auto-model)
- [Run from source (developer mode)](#run-from-source-developer-mode)
- [Troubleshooting](#troubleshooting)
- [Safety notes for classroom demonstrations](#safety-notes-for-classroom-demonstrations)
---

## What this project does

1. You set an observation location (lat/lon; optionally via browser geolocation).
2. You search for a satellite by **NORAD ID** or by name (tolerant search).
3. The UI retrieves/uses **TLE** and computes the pass trajectory.
4. The UI renders a **Sky Dome** path from **AOS** (rise above horizon) to **LOS** (set below horizon).
5. Optionally, the system drives a **DMX moving head** to track the satellite by converting **Az/El** to **Pan/Tilt**.
6. A **Calibration Lab** lets you measure 90/180/270/360° and fit an automatic **degree → DMX** mapping model to improve pointing realism.

---

## Key features

- Thai-first classroom UI (short operational steps; suitable for students).
- Satellite search by NORAD ID or approximate name.
- TLE display and pass-path rendering (Sky Dome).
- Manual Lab in **Azimuth/Elevation** (more intuitive than raw DMX values for learners).
- Tracking mode:
  - audible cue (optional `ding.mp3`) at a fixed interval during active tracking
  - safe stop at LOS: lights off + return to home (Pan 0 / Tilt 0)
- Calibration Lab: guided 90/180/270/360° capture + automatic model generation for Pan mapping.
- Offline-first satellite catalog support (recommended): bundle `satcat.csv` alongside the executable for instant searches.
- Optional “simulation-only” operation if DMX hardware is not connected.

---

## Requirements

### Operating system
- Windows 11 (recommended; primary classroom target)
- Windows 10 (usually works)

### Hardware (optional but recommended for full demo)
![Hardware](/docs/images/Hardware.png)
- DMX512 moving-head light fixture
- USB-DMX interface (commonly uDMX/LIXADA-compatible)
- DMX cable(s)
- Fixture starting address set to **1** (or configure in the UI)

### Network / APIs
- Internet connectivity recommended for satellite metadata / TLE retrieval workflows
- If using N2YO, an API key is required. N2YO provides a REST API with per-endpoint transaction limits (e.g., `tle`, `positions`, etc.). See official documentation. (Reference: N2YO API) 

---

## Install from GitHub Releases (recommended for schools)

This installation path is intended for teachers/students who do not want to install Python.

### 1) Download the latest Release asset
1. Open the **Releases** page of this repository.
2. Download the Windows ZIP asset (example naming convention):
   - `satfinder-beam-tracker-lab-win11-x64.zip`

### 2) Extract the ZIP
- Extract to a writable folder, e.g.:
  - `C:\SatFinderBeamLab\`

### 3) Verify the extracted contents
You should see (names may vary per release):
- `sat_mh_web.exe` (the application)
- `ding.mp3` (optional audio cue)
- `satcat.csv` (highly recommended; improves first-use search speed/offline readiness)

**Recommendation for classroom deployments:** include `satcat.csv` in the ZIP so the first search is immediate (no large download wait).

### 4) Install USB-DMX driver (first time only)
Proceed to:  
[Windows 11 driver setup for LIXADA/uDMX (VID_16C0&PID_05DC)](#windows-11-driver-setup-for-lixadaudmx-vid_16c0pid_05dc)

### 5) Launch
- Double-click `sat_mh_web.exe`
- If Windows Firewall prompts: allow on **Private networks** (typical classroom LAN)

### 6) Open the Web UI
Open a browser and go to:

```text
http://127.0.0.1:5000
````

---

## Windows 11 driver setup for LIXADA/uDMX (VID_16C0&PID_05DC)

Many uDMX-style USB-DMX interfaces use the VID/PID pair `16c0:05dc`. A common, robust approach on modern Windows is installing a generic USB driver (WinUSB/libusbK) using **Zadig**, a Windows tool for installing generic USB drivers such as WinUSB and libusbK. ([zadig.akeo.ie][1])

A practical example from the community explicitly describes using Zadig to replace the driver to **libusbK** for uDMX and notes the default VID/PID `16c0:05dc`. ([GitHub][2])

### Driver installation steps (Windows 11)

1. Plug in the USB-DMX interface.
2. Run Zadig as Administrator.
3. In Zadig, enable:

   * **Options → List All Devices** (so devices with an existing driver are visible). ([GitHub][3])
4. Select the USB-DMX device:

   * Look for a device labeled `uDMX`, `USB-DMX`, or match VID/PID `16c0:05dc` (as shown by Zadig).
5. Choose a driver:

   * Start with **WinUSB** (often simplest).
   * If your specific interface/stack requires it, try **libusbK** (commonly used in uDMX workflows). ([zadig.akeo.ie][1])
6. Click **Install Driver** / **Replace Driver**.
7. Unplug and replug the USB-DMX interface.

**Notes**

* Driver installation has become more challenging across newer Windows versions for some uDMX-style devices; multiple driver options may need to be tried. ([illutzmination.de][4])
* If your classroom PCs are managed (restricted admin rights), perform driver installation once on a prepared machine image or request IT support.

---

## Quick start (classroom flow)

### Step 1: Location
![Step1](/docs/images/Step1.png)
* Choose either:

  * Browser geolocation (if allowed), or
  * manual latitude/longitude input

### Step 2: Satellite selection
![Step2](/docs/images/Step2.png)
* Search by:

  * NORAD ID (e.g., `25544`), or
  * approximate name (case-insensitive partial matches)

### Step 3: View TLE + path
![Step3](/docs/images/Step3.png)
* Confirm the displayed TLE.
* The Sky Dome path shows the satellite track from AOS to LOS.

### Step 4: Tracking (beam pointing)
![Step4](/docs/images/Step4.png)
* Start tracking.
* Confirm the fixture’s “North reference” orientation if prompted (classroom alignment step).
* During tracking:

  * the fixture points to Az/El-derived pan/tilt angles
  * optional periodic audio cue indicates “active tracking”
* After LOS:

  * lights go off
  * fixture returns to home (Pan 0 / Tilt 0)

### Step 5: Manual Lab (Az/El)

For teaching:

* Set **Azimuth** and **Elevation** directly.
* Students can relate:

  * Azimuth to compass direction (north/east/south/west)
  * Elevation to “above the horizon” intuition

---

## Calibration Lab (Pan 90/180/270/360) + auto model
![Calibration](/docs/images/Calibration.png)
### Why calibration is necessary

Moving-head pan position is rarely perfectly linear with DMX values across the full range, and some fixtures reverse direction (e.g., DMX increases → CCW). A calibration workflow improves pointing realism and makes the “engineering measurement” component explicit for students.

### What the lab does

1. Reset to Pan = 0 (reference start)
2. For each target angle: **90°, 180°, 270°, 360°**

   * adjust Pan DMX until the beam matches the physical angle marker on your scale
   * save the measured DMX value for that angle
3. Generate an automatic model:

   * builds a monotonic piecewise mapping (degree → DMX)
   * saves the model to configuration for use in Manual/Tracking modes

### Recommended physical setup

* Place a simple degree scale around the fixture base (paper ring or printed marker).
* Define **Pan 0°** as “North reference” for consistent classroom explanation.

---

## Run from source (developer mode)

### 1) Install Python

* Python 3.10+ (3.11/3.12 recommended)

### 2) Create and activate a virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
```

### 4) Place required local files

* Put `ding.mp3` next to the main Python file
* (Recommended) Put `satcat.csv` next to the main file for fast/offline-first searches

### 5) Run

```bash
python sat_mh_web.py
```

Open:

```text
http://127.0.0.1:5000
```
---

## Troubleshooting

### A) The web page does not open

* Confirm the app is running (console window not closed).
* Ensure Windows Firewall allows local access on private networks.
* Port conflict: if `5000` is used, change the port in configuration.

### B) DMX connected but the fixture does not move

* Confirm DMX address on the fixture (start at 1).
* Confirm correct driver installation (see Zadig steps).
* Try switching between WinUSB and libusbK in Zadig (device-dependent). ([zadig.akeo.ie][1])

### C) Satellite search is slow on first use

* Bundle or place `satcat.csv` alongside the executable for immediate local searching.
* If you are using an online SATCAT endpoint, consider switching to the CelesTrak SATCAT Records API format/query approach. ([celestrak.org][5])

### D) N2YO API errors / rate limits

* N2YO free API is transaction-limited per endpoint type (e.g., `tle`, `positions`, etc.). Confirm usage against the official limits. ([N2YO][6])

---

## Safety notes for classroom demonstrations

* Prefer **White/Dimmer** modes for demonstrations; keep strobe/laser disabled by default.
* Avoid directing the beam into eyes.
* Maintain a safe perimeter around rotating hardware.
* Use “simulation-only mode” when demonstrating concepts without the physical fixture.

---

## Credits and references

* Zadig (generic USB driver installation tool; WinUSB/libusbK). ([zadig.akeo.ie][1])
* Community uDMX workflow example (VID/PID `16c0:05dc`, libusbK via Zadig). ([GitHub][2])
* N2YO REST API documentation and endpoint transaction limits. ([N2YO][6])
* CelesTrak SATCAT Records API formatting and query examples (CSV/JSON). ([celestrak.org][5])

---

## Useful links (copy/paste)

To comply with some environments that restrict clickable links in documents, here are references in plain text:

```text
Zadig official site: https://zadig.akeo.ie/
Zadig wiki:          https://github.com/pbatard/libwdi/wiki/Zadig
N2YO API:            https://www.n2yo.com/api/
CelesTrak SATCAT:    https://celestrak.org/satcat/satcat-format.php
```

---
## About This Project

SatFinder: Satellite Beam Tracker Lab
Telecommunications Learning Promotion Project for Schools Nationwide
Faculty of Engineering, Thammasat University, and Thammasat University Research and Consultancy Institute
Supported by the Broadcasting and Telecommunications Research and Development Fund for the Public Interest
