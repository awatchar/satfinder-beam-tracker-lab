ภาษา: ไทย | [English](./README.md)

# SatFinder: Satellite Beam Tracker Lab
![SatBeamTrack Logo](/docs/images/SatBeamTrack.png)

แลบการเรียนรู้บนเว็บสำหรับสาธิตการเคลื่อนที่ของดาวเทียมบนท้องฟ้า ระบบจะแสดงผลเส้นทางผ่านของดาวเทียม (Sky Dome) และสามารถสั่ง **DMX512 moving-head light** เพื่อ “ชี้ลำแสง” ตามเส้นทาง **Azimuth/Elevation** แบบเรียลไทม์ของดาวเทียม (Az/El → Pan/Tilt) โดยใช้การคำนวณวงโคจรจาก **TLE/NORAD**

รีโปนี้ออกแบบมาสำหรับการสอนในห้องเรียน/STEM: เริ่มต้นใช้งานได้คาดเดาได้, ลำดับการใช้งานไม่ซับซ้อน, และมีเวิร์กโฟลว์คาลิเบรตที่เปลี่ยน “ความไม่สมบูรณ์ของฮาร์ดแวร์จริง” ให้เป็นช่วงเรียนรู้ด้านวิศวกรรม

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

1. คุณกำหนดตำแหน่งจุดสังเกต (lat/lon; จะใช้ browser geolocation ก็ได้)
2. คุณค้นหาดาวเทียมด้วย **NORAD ID** หรือชื่อ (ค้นหาแบบยืดหยุ่น)
3. UI จะดึง/ใช้งาน **TLE** และคำนวณเส้นทางการผ่าน
4. UI จะแสดงเส้นทาง **Sky Dome** ตั้งแต่ **AOS** (โผล่พ้นขอบฟ้า) ถึง **LOS** (ลับต่ำกว่าขอบฟ้า)
5. หากต้องการ ระบบจะสั่ง **DMX moving head** ให้ติดตามดาวเทียมโดยแปลง **Az/El** เป็น **Pan/Tilt**
6. **Calibration Lab** ให้คุณวัด 90/180/270/360° และฟิตโมเดลแมปปิงอัตโนมัติ **degree → DMX** เพื่อเพิ่มความสมจริงของการชี้ลำแสง

---

## Key features

- UI สำหรับห้องเรียนที่เน้นภาษาไทย (ขั้นตอนสั้น กระชับ เหมาะกับนักเรียน)
- ค้นหาดาวเทียมด้วย NORAD ID หรือชื่อแบบใกล้เคียง
- แสดง TLE และเรนเดอร์เส้นทางการผ่าน (Sky Dome)
- โหมด Manual Lab ใน **Azimuth/Elevation** (ผู้เรียนเข้าใจง่ายกว่าค่า DMX ดิบ)
- โหมดติดตาม (Tracking):
  - เสียงเตือน (เลือกใช้ได้ด้วย `ding.mp3`) ตามช่วงเวลาคงที่ระหว่างติดตาม
  - หยุดอย่างปลอดภัยเมื่อ LOS: ปิดไฟ + กลับตำแหน่ง home (Pan 0 / Tilt 0)
- Calibration Lab: เก็บค่าทีละขั้น 90/180/270/360° พร้อมสร้างโมเดลอัตโนมัติสำหรับแมป Pan
- รองรับออฟไลน์เป็นหลัก (offline-first) สำหรับคลังดาวเทียม: วาง `satcat.csv` ไว้ข้างไฟล์ executable เพื่อค้นหาได้ทันที
- โหมด “simulation-only” แบบทางเลือก หากยังไม่เชื่อมต่อฮาร์ดแวร์ DMX

---

## Requirements

### Operating system
- Windows 11 (แนะนำ; เป้าหมายหลักสำหรับห้องเรียน)
- Windows 10 (โดยทั่วไปใช้งานได้)

### Hardware (optional but recommended for full demo)
![Hardware](/docs/images/Hardware.png)
- โคม moving-head ที่รองรับ DMX512
- USB-DMX interface (โดยทั่วไป uDMX/LIXADA-compatible)
- สาย DMX
- ตั้งค่า Fixture starting address เป็น **1** (หรือกำหนดใน UI)

### Network / APIs
- แนะนำให้เชื่อมต่ออินเทอร์เน็ตสำหรับงานดึงข้อมูล metadata / TLE ของดาวเทียม
- หากใช้งาน N2YO ต้องมี API key โดย N2YO ให้บริการ REST API พร้อมข้อจำกัดจำนวน transaction ต่อ endpoint (เช่น `tle`, `positions` เป็นต้น) โปรดดูเอกสารทางการ (Reference: N2YO API)

---

## Install from GitHub Releases (recommended for schools)

เส้นทางติดตั้งนี้ออกแบบสำหรับครู/นักเรียนที่ไม่ต้องการติดตั้ง Python

### 1) Download the latest Release asset
1. เปิดหน้า **Releases** ของรีโปนี้
2. ดาวน์โหลดไฟล์ ZIP สำหรับ Windows (ตัวอย่างรูปแบบชื่อไฟล์):
   - `satfinder-beam-tracker-lab-win11-x64.zip`

### 2) Extract the ZIP
- แตกไฟล์ไปยังโฟลเดอร์ที่เขียนได้ เช่น:
  - `C:\SatFinderBeamLab\`

### 3) Verify the extracted contents
คุณควรเห็นไฟล์ต่อไปนี้ (ชื่ออาจต่างกันตามแต่ละรีลีส):
- `sat_mh_web.exe` (ตัวแอปพลิเคชัน)
- `ding.mp3` (เสียงเตือนแบบทางเลือก)
- `satcat.csv` (แนะนำอย่างยิ่ง; ช่วยให้การค้นหาครั้งแรกเร็วขึ้น/พร้อมใช้งานออฟไลน์)

**คำแนะนำสำหรับการใช้งานในห้องเรียน:** ใส่ `satcat.csv` มาใน ZIP เพื่อให้ค้นหาได้ทันทีตั้งแต่ครั้งแรก (ไม่ต้องรอดาวน์โหลดขนาดใหญ่)

### 4) Install USB-DMX driver (first time only)
ไปที่หัวข้อ:
[Windows 11 driver setup for LIXADA/uDMX (VID_16C0&PID_05DC)](#windows-11-driver-setup-for-lixadaudmx-vid_16c0pid_05dc)

### 5) Launch
- ดับเบิลคลิก `sat_mh_web.exe`
- หาก Windows Firewall แจ้งเตือน ให้อนุญาตบน **Private networks** (LAN ห้องเรียนทั่วไป)

### 6) Open the Web UI
เปิดเบราว์เซอร์แล้วไปที่:

```text
http://127.0.0.1:5000
````

---

## Windows 11 driver setup for LIXADA/uDMX (VID_16C0&PID_05DC)

อุปกรณ์ USB-DMX แบบ uDMX จำนวนมากใช้คู่ VID/PID `16c0:05dc` แนวทางที่ใช้งานได้จริงบน Windows รุ่นใหม่คือ ติดตั้งไดรเวอร์ USB แบบทั่วไป (WinUSB/libusbK) ผ่าน **Zadig** ซึ่งเป็นเครื่องมือบน Windows สำหรับติดตั้งไดรเวอร์ USB ทั่วไป เช่น WinUSB และ libusbK ([zadig.akeo.ie][1])

มีตัวอย่างจากชุมชนที่อธิบายชัดเจนเกี่ยวกับการใช้ Zadig เพื่อแทนที่ไดรเวอร์เป็น **libusbK** สำหรับ uDMX และระบุค่า VID/PID เริ่มต้น `16c0:05dc` ([GitHub][2])

### Driver installation steps (Windows 11)

1. เสียบ USB-DMX interface
2. เปิด Zadig แบบ Run as Administrator
3. ใน Zadig ให้เปิด:

   * **Options → List All Devices** (เพื่อให้เห็นอุปกรณ์ที่มีไดรเวอร์เดิมอยู่แล้ว) ([GitHub][3])
4. เลือกอุปกรณ์ USB-DMX:

   * มองหาอุปกรณ์ชื่อ `uDMX`, `USB-DMX` หรือเทียบจาก VID/PID `16c0:05dc` (ตามที่ Zadig แสดง)
5. เลือกไดรเวอร์:

   * เริ่มจาก **WinUSB** (มักง่ายที่สุด)
   * หากอินเทอร์เฟซ/สแตกของคุณต้องการ ให้ลอง **libusbK** (พบบ่อยในเวิร์กโฟลว์ uDMX) ([zadig.akeo.ie][1])
6. คลิก **Install Driver** / **Replace Driver**
7. ถอดแล้วเสียบ USB-DMX interface ใหม่

**Notes**

* การติดตั้งไดรเวอร์อาจยากขึ้นใน Windows รุ่นใหม่สำหรับอุปกรณ์ uDMX บางรุ่น; อาจต้องลองหลายตัวเลือกไดรเวอร์ ([illutzmination.de][4])
* หากคอมพิวเตอร์ห้องเรียนถูกจัดการส่วนกลาง (สิทธิ์แอดมินจำกัด) ควรติดตั้งไดรเวอร์ล่วงหน้าบน image เครื่องต้นแบบ หรือประสานงานฝ่าย IT

---

## Quick start (classroom flow)

### Step 1: Location
![Step1](/docs/images/Step1.png)
* เลือกอย่างใดอย่างหนึ่ง:

  * browser geolocation (ถ้าอนุญาต), หรือ
  * กรอก latitude/longitude ด้วยตนเอง

### Step 2: Satellite selection
![Step2](/docs/images/Step2.png)
* ค้นหาโดย:

  * NORAD ID (เช่น `25544`), หรือ
  * ชื่อแบบใกล้เคียง (จับคู่บางส่วนโดยไม่สนตัวพิมพ์เล็ก/ใหญ่)

### Step 3: View TLE + path
![Step3](/docs/images/Step3.png)
* ตรวจสอบ TLE ที่แสดง
* เส้นทาง Sky Dome จะแสดงการผ่านจาก AOS ถึง LOS

### Step 4: Tracking (beam pointing)
![Step4](/docs/images/Step4.png)
* เริ่มการติดตาม
* ยืนยันการวางแนว “North reference” ของโคมหากระบบแจ้ง (ขั้นตอนจัดแนวในห้องเรียน)
* ระหว่างการติดตาม:

  * โคมจะชี้ตามมุม pan/tilt ที่คำนวณจาก Az/El
  * เสียงเตือนเป็นช่วงเวลา (ทางเลือก) แสดงสถานะ “active tracking”
* หลัง LOS:

  * ปิดไฟ
  * โคมกลับตำแหน่ง home (Pan 0 / Tilt 0)

### Step 5: Manual Lab (Az/El)

สำหรับการสอน:

* ตั้งค่า **Azimuth** และ **Elevation** โดยตรง
* นักเรียนเชื่อมโยงได้ว่า:

  * Azimuth สัมพันธ์กับทิศเข็มทิศ (เหนือ/ตะวันออก/ใต้/ตะวันตก)
  * Elevation คือแนวคิด “สูงกว่าขอบฟ้า”

---

## Calibration Lab (Pan 90/180/270/360) + auto model
![Calibration](/docs/images/Calibration.png)
### Why calibration is necessary

ตำแหน่ง pan ของ moving-head โดยทั่วไปไม่เป็นเชิงเส้นสมบูรณ์กับค่า DMX ตลอดช่วงการทำงาน และโคมบางรุ่นกลับทิศการหมุน (เช่น DMX เพิ่ม → หมุนทวนเข็มนาฬิกา) เวิร์กโฟลว์คาลิเบรตช่วยให้การชี้ลำแสงสมจริงขึ้น และทำให้องค์ประกอบ “การวัดทางวิศวกรรม” ชัดเจนสำหรับผู้เรียน

### What the lab does

1. รีเซ็ตไปที่ Pan = 0 (จุดอ้างอิงเริ่มต้น)
2. สำหรับมุมเป้าหมายแต่ละจุด: **90°, 180°, 270°, 360°**

   * ปรับ Pan DMX จนลำแสงตรงกับเครื่องหมายมุมจริงบนสเกล
   * บันทึกค่า DMX ที่วัดได้สำหรับมุมนั้น
3. สร้างโมเดลอัตโนมัติ:

   * สร้างแมปปิงแบบ monotonic piecewise (degree → DMX)
   * บันทึกโมเดลลง configuration เพื่อใช้ในโหมด Manual/Tracking

### Recommended physical setup

* วางสเกลองศาอย่างง่ายรอบฐานโคม (วงแหวนกระดาษหรือมาร์กเกอร์พิมพ์)
* กำหนด **Pan 0°** เป็น “North reference” เพื่อใช้อธิบายในห้องเรียนอย่างสอดคล้องกัน

---

## Run from source (developer mode)

### 1) Install Python

* Python 3.10+ (แนะนำ 3.11/3.12)

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

* วาง `ding.mp3` ไว้ข้างไฟล์ Python หลัก
* (แนะนำ) วาง `satcat.csv` ไว้ข้างไฟล์หลักเพื่อการค้นหาที่เร็ว/รองรับออฟไลน์เป็นหลัก

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

* ยืนยันว่าแอปกำลังทำงานอยู่ (หน้าต่างคอนโซลยังไม่ถูกปิด)
* ตรวจสอบว่า Windows Firewall อนุญาตการเข้าถึงภายในเครื่องบน private networks
* พอร์ตชนกัน: หาก `5000` ถูกใช้งาน ให้เปลี่ยนพอร์ตใน configuration

### B) DMX connected but the fixture does not move

* ตรวจสอบ DMX address บนโคม (เริ่มที่ 1)
* ตรวจสอบการติดตั้งไดรเวอร์ให้ถูกต้อง (ดูขั้นตอน Zadig)
* ลองสลับระหว่าง WinUSB และ libusbK ใน Zadig (ขึ้นกับอุปกรณ์) ([zadig.akeo.ie][1])

### C) Satellite search is slow on first use

* ใส่หรือวาง `satcat.csv` ไว้ข้างไฟล์ executable เพื่อค้นหาแบบ local ได้ทันที
* หากคุณใช้ SATCAT endpoint แบบออนไลน์ ให้พิจารณาเปลี่ยนเป็นรูปแบบ/แนวทาง query ของ CelesTrak SATCAT Records API ([celestrak.org][5])

### D) N2YO API errors / rate limits

* N2YO free API มีข้อจำกัดจำนวน transaction แยกตามประเภท endpoint (เช่น `tle`, `positions` เป็นต้น) ให้ตรวจสอบการใช้งานเทียบกับข้อจำกัดทางการ ([N2YO][6])

---

## Safety notes for classroom demonstrations

* แนะนำใช้โหมด **White/Dimmer** สำหรับการสาธิต; ปิด strobe/laser เป็นค่าเริ่มต้น
* หลีกเลี่ยงการฉายลำแสงเข้าดวงตา
* เว้นระยะปลอดภัยรอบอุปกรณ์ที่มีการหมุน
* ใช้ “simulation-only mode” เมื่อต้องสาธิตแนวคิดโดยไม่ใช้อุปกรณ์จริง

---

## Credits and references

* Zadig (เครื่องมือติดตั้งไดรเวอร์ USB ทั่วไป; WinUSB/libusbK) ([zadig.akeo.ie][1])
* ตัวอย่างเวิร์กโฟลว์ uDMX จากชุมชน (VID/PID `16c0:05dc`, libusbK ผ่าน Zadig) ([GitHub][2])
* เอกสาร N2YO REST API และข้อจำกัด transaction ต่อ endpoint ([N2YO][6])
* ตัวอย่างรูปแบบและคำสั่ง query ของ CelesTrak SATCAT Records API (CSV/JSON) ([celestrak.org][5])

---

## Useful links (copy/paste)

เพื่อให้สอดคล้องกับบางสภาพแวดล้อมที่จำกัดลิงก์แบบคลิกได้ในเอกสาร ด้านล่างคือรายการอ้างอิงแบบ plain text:

```text
Zadig official site: https://zadig.akeo.ie/
Zadig wiki:          https://github.com/pbatard/libwdi/wiki/Zadig
N2YO API:            https://www.n2yo.com/api/
CelesTrak SATCAT:    https://celestrak.org/satcat/satcat-format.php
```

---
## About This Project

SatFinder: Satellite Beam Tracker Lab
โครงการส่งเสริมการเรียนรู้ด้านโทรคมนาคมสำหรับโรงเรียนทั่วประเทศ
คณะวิศวกรรมศาสตร์ มหาวิทยาลัยธรรมศาสตร์ และสถาบันวิจัยและให้คำปรึกษาแห่งมหาวิทยาลัยธรรมศาสตร์
ได้รับการสนับสนุนโดยกองทุนวิจัยและพัฒนากิจการกระจายเสียง กิจการโทรทัศน์ และกิจการโทรคมนาคม เพื่อประโยชน์สาธารณะ
