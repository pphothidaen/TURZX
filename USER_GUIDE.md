# คู่มือการใช้งาน TURZX 3.5" IPS USB Display (User Guide)

คู่มือฉบับสมบูรณ์สำหรับการใช้งานหน้าจอ **TURZX / Turing Smart Screen 3.5" IPS USB-C** บน macOS (Apple Silicon / Intel) และระบบปฏิบัติการอื่นๆ ครอบคลุมทั้งการใช้งานผ่าน Command Line (CLI) และ Python API

---

## 🗺️ แผนผังเอกสารทั้งหมด (Documentation Map)

| เอกสาร | กลุ่มเป้าหมาย | รายละเอียด | ลิงก์ |
|---|---|---|---|
| **User Guide** *(เอกสารนี้)* | ผู้ใช้งานทั่วไป & Developer | วิธีใช้งานเบื้องต้น, คำสั่ง CLI, Python API, และตัวอย่างใช้งานจริง | [`USER_GUIDE.md`](USER_GUIDE.md) |
| **README** | ภาพรวมโครงการ | จุดเด่น (Highlights), สเปกฮาร์ดแวร์, และ Quick Start | [`README.md`](README.md) |
| **CLI Manual** | ผู้ใช้งาน Terminal / Shell | รวมคำสั่ง CLI ทุกคำสั่ง, Flags, ตัวเลือกเสริม และตัวอย่าง Shell Script | [`docs/CLI_USAGE.md`](docs/CLI_USAGE.md) |
| **API Reference** | Python Developers | เอกสารคลาส `TURZXDriver`, Method ทุกตัว, Types, และ Exceptions | [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) |
| **Architecture & Protocol** | Hardware & Protocol Engineers | ไดอะแกรม Binary Packet 6-byte, Bit-packing 10-bit, และการแก้บั๊ก USB macOS | [`docs/ARCHITECTURE_AND_PROTOCOL.md`](docs/ARCHITECTURE_AND_PROTOCOL.md) |
| **Developer Extension Guide** | ผู้สร้าง Dashboard & Widgets | คู่มือสร้างวิตเจ็ตสด, มอนิเตอร์กราฟฟิก, และการ Optimize บางส่วนของจอ | [`docs/DEVELOPER_EXTENSION_GUIDE.md`](docs/DEVELOPER_EXTENSION_GUIDE.md) |

---

## 📌 สารบัญ (Table of Contents)

1. [ข้อมูลฮาร์ดแวร์และการเชื่อมต่อ](#1-ข้อมูลฮาร์ดแวร์และการเชื่อมต่อ-hardware-overview)
2. [การติดตั้งและเตรียม Environment](#2-การติดตั้งและเตรียม-environment)
3. [วิธีส่งภาพขึ้นหน้าจอแบบด่วน (Quick Start)](#3-วิธีส่งภาพขึ้นหน้าจอแบบด่วน-quick-start)
4. [คู่มือคำสั่ง Command Line (CLI)](#4-คู่มือคำสั่ง-command-line-cli)
5. [การเรียกใช้งานผ่าน Python API](#5-การเรียกใช้งานผ่าน-python-api)
6. [ตัวอย่างโค้ดนำไปต่อยอด (Extension Examples)](#6-ตัวอย่างโค้ดนำไปต่อยอด-extension-examples)
7. [การแก้ไขปัญหาที่พบบ่อย (Troubleshooting)](#7-การแก้ไขปัญหาที่พบบ่อย-troubleshooting)
8. [เอกสารอ้างอิงและคู่มือเชิงลึก (Related Documentation)](#8-เอกสารอ้างอิงและคู่มือเชิงลึก-related-documentation)

---

## 1. ข้อมูลฮาร์ดแวร์และการเชื่อมต่อ (Hardware Overview)

| คุณสมบัติ | รายละเอียด |
|---|---|
| **รุ่นหน้าจอ** | TURZX 3.5" IPS / UsbMonitor Rev A |
| **ความละเอียดปกติ (Portrait)** | 320 × 480 พิกเซล |
| **ความละเอียดแนวนอน (Landscape)** | 480 × 320 พิกเซล |
| **USB Vendor ID (VID)** | `0x1A86` (QinHeng Electronics / WCH) |
| **USB Product ID (PID)** | `0x5722` |
| **การเชื่อมต่อบน macOS** | สาย USB Type-C เข้ากับพอร์ต Mac โดยตรง |
| **ระบบรับส่งข้อมูลหลัก** | **PyUSB Bulk OUT Endpoint `0x03`** (เร็ว เสถียร ไม่ติดบั๊ก Serial buffer ของ macOS) |

> [!TIP]
> **ทำไมถึงใช้ PyUSB แทน Serial บน macOS?**
> ไดรเวอร์ Serial มาตรฐานของ macOS (`AppleUSBACM`) มีปัญหาเรื่องการแตกและแบ่ง Packet ข้อมูลขนาดใหญ่ ทำให้ส่งรูปแล้วหน้าจอลายหรือติดจอฟ้า ตัวโมดูลนี้จึงสื่อสารผ่าน USB Bulk Endpoint โดยตรง ทำให้ส่งภาพ 307 KB ได้ในเวลาเพียง ~1.8 วินาที

> [!NOTE]
> 📖 **ศึกษาเพิ่มเติมด้านสถาปัตยกรรมและ Binary Protocol:**
> รายละเอียดโครงสร้าง Bit-level Packet Layout (10-bit bit packing), 16-byte Orientation Packet, และกระบวนการ Reverse-engineer ฮาร์ดแวร์ ดูได้ที่ [`docs/ARCHITECTURE_AND_PROTOCOL.md`](docs/ARCHITECTURE_AND_PROTOCOL.md)

---

## 2. การติดตั้งและเตรียม Environment

### 2.1 Virtual Environment
โครงการนี้เตรียม Virtual Environment พร้อม Library จำเป็น (`pyusb`, `pyserial`, `pillow`, `numpy`) ไว้แล้วที่โฟลเดอร์ `.venv/`

```bash
cd /Users/kimlenglim/Project/TURZX

# เข้าสู่ virtual environment
source .venv/bin/activate
```

### 2.2 ติดตั้ง Package เข้าสู่ระบบ (ทางเลือก)
หากต้องการติดตั้งคำสั่ง `turzx` ให้สามารถเรียกใช้ได้จากทุกที่:

```bash
pip install -e .
```

---

## 3. วิธีส่งภาพขึ้นหน้าจอแบบด่วน (Quick Start)

### วิธีที่ 1: ใช้สคริปต์ `send_image_to_display.py`

ส่งภาพใดก็ได้ขึ้นหน้าจอทันที (สคริปต์จะคำนวณและปรับสัดส่วนภาพให้อัตโนมัติ):

```bash
# ส่งภาพที่ระบุ
python3 send_image_to_display.py /path/to/your/image.png

# ส่งภาพพร้อมปรับความสว่าง 80% และหมุนจอแนวนอน
python3 send_image_to_display.py photo.jpg -b 80 -o 1
```

### วิธีที่ 2: ใช้คำสั่ง CLI `turzx send`

```bash
# ส่งภาพแบบอัตโนมัติ
python3 -m turzx.cli send my_wallpaper.png

# หรือหากติดตั้ง package แล้ว
turzx send my_wallpaper.png --brightness 75 --fit cover
```

---

## 4. คู่มือคำสั่ง Command Line (CLI)

คำสั่ง `turzx` รองรับการควบคุมหน้าจออย่างครอบคลุมผ่าน Terminal:

> [!NOTE]
> 📖 **ดูคู่มือ CLI ฉบับละเอียดและตัวอย่าง Automation Scripts:**
> ดู Flags, ตัวเลือกทั้งหมด, และสคริปต์ตัวอย่าง (เช่น Cron job สลับภาพอัตโนมัติ) ได้ที่ [`docs/CLI_USAGE.md`](docs/CLI_USAGE.md)

### 4.1 ตรวจสอบสถานะการเชื่อมต่อ (`status`)
ตรวจสอบว่าเครื่อง Mac ตรวจพบหน้าจอหรือไม่ และเชื่อมต่อผ่าน Transport ใด:

```bash
turzx status
```
*ตัวอย่างผลลัพธ์:*
```text
=======================================================
TURZX DISPLAY HARDWARE STATUS
=======================================================
  Connected:       YES
  Transport:       USB
  Port:            USB:0x1a86:0x5722
  Model Revision:  USBMONITOR_3_5
  Resolution:      320x480
  Orientation:     0
  Baudrate:        115200
=======================================================
```

---

### 4.2 ส่งภาพขึ้นหน้าจอ (`send`)

```bash
turzx send <path_to_image> [options]
```

**ตัวเลือกเสริม:**
- `-b, --brightness <0-100>`: ปรับความสว่างหน้าจอ (ค่าเริ่มต้น: 60%)
- `-o, --orientation <0-3>`: กำหนดทิศทางหน้าจอ
  - `0`: แนวตั้งปกติ (Portrait - 320×480) พอร์ต Type-C อยู่ด้านล่าง
  - `1`: แนวนอน (Landscape - 480×320) พอร์ต Type-C อยู่ด้านขวา
  - `2`: แนวตั้งกลับหัว (Reverse Portrait - 180°)
  - `3`: แนวนอนกลับหัว (Reverse Landscape - 270°)
- `--fit <mode>`: โหมดการปรับขนาดภาพ
  - `cover` (ค่าเริ่มต้น): ขยายภาพเต็มหน้าจอและตัดขอบส่วนเกินออก (Center Crop) ภาพจะไม่เบี้ยว
  - `contain`: ปรับขนาดภาพให้พอดีจอโดยคงสัดส่วนเดิม และใส่ขอบสีดำ (Letterbox)
  - `stretch`: ดึงภาพให้ยืดเต็มจอตามมิติ

*ตัวอย่างการใช้งาน:*
```bash
# แสดงภาพอนิเมะ/วิวเต็มจอแนวตั้งแบบไม่เสียสัดส่วน
turzx send anime_sky.png --fit cover --brightness 80

# แสดงภาพแนวนอนพร้อมขอบสีดำ
turzx send wallpaper_wide.jpg --orientation 1 --fit contain
```

---

### 4.3 ปรับความสว่างหน้าจอ (`brightness`)
ปรับระดับไฟ Backlight ตั้งแต่ 0% ถึง 100%:

```bash
turzx brightness 75
```

---

### 4.4 ล้างหน้าจอ (`clear`)
ล้างหน้าจอให้เป็นสีขาว หรือระบุสีที่ต้องการ:

```bash
# ล้างหน้าจอเป็นสีดำสนิท (ทำงานรวดเร็วในระดับเสี้ยววินาที)
turzx clear --color black

# ล้างหน้าจอเป็นสีขาว
turzx clear

# ล้างหน้าจอเป็นสีน้ำเงินเข้ม (Hex หรือ RGB)
turzx clear --color "#0b192c"
turzx clear --color "10,25,47"
```

---

### 4.5 ควบคุมไฟหน้าจอ (`power`)

```bash
# ปิดไฟหน้าจอ (Standby)
turzx power off

# เปิดไฟหน้าจอ
turzx power on
```

---

### 4.6 ทดสอบหน้าจอด้วย Pattern (`test`)
ทดสอบการแสดงผลและสีของหน้าจอ:

```bash
turzx test --pattern bars      # SMPTE Color Bars
turzx test --pattern gradient  # ไล่เฉดสี RGB
turzx test --pattern grid      # ตารางตารางพิกเซล
```

---

## 5. การเรียกใช้งานผ่าน Python API

หากต้องการนำไปเขียนโปรแกรมควบคุมเองในภาษา Python สามารถเรียกใช้คลาส `TURZXDriver` ได้ง่ายดาย:

> [!NOTE]
> 📖 **ดู API Reference ฉบับเต็ม:**
> ดูรายละเอียด Class Reference, Methods, Parameters, Types, และ Exceptions ทั้งหมดได้ที่ [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)

### 5.1 การเชื่อมต่อและแสดงภาพด้วย Context Manager (`with`)

```python
from turzx import TURZXDriver, Orientation

# เปิดหน้าจอและตัดการเชื่อมต่ออัตโนมัติเมื่อเสร็จสิ้น
with TURZXDriver() as display:
    # 1. ตั้งค่าทิศทางและความสว่าง
    display.set_orientation(Orientation.PORTRAIT)  # หรือ Orientation.LANDSCAPE
    display.set_brightness(80)

    # 2. ส่งภาพจากไฟล์
    display.display_image_file("my_image.png", fit_mode="cover")

print("แสดงผลสำเร็จเรียบร้อย!")
```

### 5.2 การวาดภาพสดด้วย Pillow แล้วส่งขึ้นจอ

```python
from PIL import Image, ImageDraw, ImageFont
from turzx import TURZXDriver, Orientation

with TURZXDriver() as display:
    # สร้าง Canvas ขนาด 320x480
    img = Image.new("RGB", (display.display_width, display.display_height), color=(20, 24, 33))
    draw = ImageDraw.Draw(img)

    # วาดกล่องข้อความ
    draw.rectangle([10, 10, 310, 80], fill=(41, 98, 255))
    draw.text((20, 35), "TURZX MONITOR READY", fill=(255, 255, 255))

    # ส่งขึ้นหน้าจอ
    display.display_pil_image(img)
```

---

## 6. ตัวอย่างโค้ดนำไปต่อยอด (Extension Examples)

> [!NOTE]
> 📖 **ดูคู่มือพัฒนา Dashboard และ Widget ขั้นสูง:**
> คู่มือการสร้างวิตเจ็ตแบบมีแอนิเมชัน, การ Optimize ส่งเฉพาะพิกเซลที่เปลี่ยน (Partial Screen Update ประหยัดแบนด์วิดท์ 32 เท่า), และการสร้าง Daemon ดูได้ที่ [`docs/DEVELOPER_EXTENSION_GUIDE.md`](docs/DEVELOPER_EXTENSION_GUIDE.md)

### ตัวอย่างที่ 1: เปลี่ยนภาพพักหน้าจออัตโนมัติทุกๆ 30 วินาที (Wallpaper Slideshow)

สร้างไฟล์ `slideshow.py`:

```python
import time
import glob
from turzx import TURZXDriver

image_files = glob.glob("35inchENG/3.5/*.png")

with TURZXDriver() as display:
    display.set_brightness(70)
    while True:
        for img_path in image_files:
            print(f"กำลังแสดง: {img_path}")
            display.display_image_file(img_path, fit_mode="cover")
            time.sleep(30)
```

---

### ตัวอย่างที่ 2: มอนิเตอร์ CPU, RAM และอุณหภูมิเครื่อง Mac แบบ Real-time

สร้างไฟล์ `mac_system_monitor.py`:

```python
import time
import psutil
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from turzx import TURZXDriver

def render_stats():
    img = Image.new("RGB", (320, 480), color=(15, 17, 26))
    draw = ImageDraw.Draw(img)

    # หัวเรื่อง
    draw.rectangle([0, 0, 320, 50], fill=(30, 34, 53))
    draw.text((20, 15), "MAC SYSTEM MONITOR", fill=(0, 255, 136))

    # ดึงค่าระบบ
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    now = datetime.now().strftime("%H:%M:%S")

    # แสดงผล
    draw.text((20, 80), f"Time: {now}", fill=(200, 200, 200))
    draw.text((20, 140), f"CPU Usage: {cpu}%", fill=(255, 180, 0))
    draw.rectangle([20, 170, 20 + int(cpu * 2.8), 190], fill=(255, 180, 0))

    draw.text((20, 230), f"RAM Usage: {ram}%", fill=(0, 210, 255))
    draw.rectangle([20, 260, 20 + int(ram * 2.8), 280], fill=(0, 210, 255))

    return img

def main():
    with TURZXDriver() as display:
        display.set_brightness(75)
        print("กำลังเริ่มแสดงผล System Monitor (กด Ctrl+C เพื่อหยุด)...")
        while True:
            frame = render_stats()
            display.display_pil_image(frame)
            time.sleep(2)

if __name__ == "__main__":
    main()
```

---

## 7. การแก้ไขปัญหาที่พบบ่อย (Troubleshooting)

### ❓ ปัญหา: หน้าจอติดจอฟ้า (Blue Screen) หรือไม่แสดงภาพ
- **สาเหตุ**: หน้าจออยู่ในโหมดเริ่มต้น และยังไม่ได้รับ Bitmap ข้อมูลภาพ
- **วิธีแก้**: รันคำสั่งส่งภาพ `python3 send_image_to_display.py <image_path>` 1 ครั้งเพื่อส่ง Framebuffer ใหม่ขึ้นจอ

### ❓ ปัญหา: `libusb` หรือ `PyUSB` แจ้งเตือน Device Not Found
- **วิธีแก้**:
  1. ถอดสาย USB-C แล้วเสียบเข้ากับพอร์ต Mac ใหม่อีกครั้ง
  2. ตรวจสอบว่าพอร์ต Mac จ่ายไฟพอ (แนะนำให้ต่อตรงกับ Mac หรือใช้ Hub ที่มีไฟเลี้ยง)
  3. ตรวจสอบด้วยคำสั่ง:
     ```bash
     ioreg -p IOUSB -l -w 0 | grep -E "1a86|UsbMonitor"
     ```

### ❓ ปัญหา: ภาพสัดส่วนผิดเพี้ยน ยืดหรือบีบ
- **วิธีแก้**: ใช้โหมด `--fit cover` เมื่อต้องการให้ภาพเต็มจอโดยไม่ยืดสัดส่วน หรือใช้ `--fit contain` หากต้องการแสดงเนื้อหาภาพครบทุกส่วน

---

## 8. เอกสารอ้างอิงและคู่มือเชิงลึก (Related Documentation)

ลิงก์ไปยังเอกสารเชิงลึกทั้งหมดในโครงการ:

- 📖 **[Architecture & Binary Protocol Specification](docs/ARCHITECTURE_AND_PROTOCOL.md)**: รายละเอียด Binary Packet, Coordinate Encoding 10-bit, Orientation Packet, และการถอดรหัส USB Descriptors
- 📖 **[Python API Reference](docs/API_REFERENCE.md)**: เอกสารอธิบาย Method ทุกตัว, Types, Attributes, และ Exception Classes ในโมดูล `turzx`
- 📖 **[CLI Manual & Shell Scripting Guide](docs/CLI_USAGE.md)**: คู่มือคำสั่ง CLI และตัวอย่าง Shell Script สำหรับระบบตั้งเวลาอัตโนมัติ
- 📖 **[Developer Extension Guide](docs/DEVELOPER_EXTENSION_GUIDE.md)**: คู่มือสร้าง Extension, Custom Dashboard, และวิตเจ็ตแสดงข้อมูลสด
- 📖 **[Project README](README.md)**: ภาพรวมโครงการ, สเปกฮาร์ดแวร์, และผลการรัน Test Suite (42 tests passed)
- 🐍 **[send_image_to_display.py](send_image_to_display.py)**: สคริปต์ Standalone สำหรับส่งรูปขึ้นจออย่างรวดเร็ว
