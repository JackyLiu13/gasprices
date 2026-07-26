# Installing gasprices on the ESP32-C6

Step-by-step, in an order that identifies your hardware before configuring
anything. Each step has a check — if the check fails, stop there, because every
later step assumes it passed.

For what the thing actually does and why, see the [README](README.md).

---

## 0. Where you already are

The backend is done and live. Confirm it before touching the firmware, so you
never end up debugging a device that's faithfully reporting a broken feed:

```bash
curl -s https://jackyliu13.github.io/gasprices/data.json
```

You should get a small JSON blob with `today_cad`, `pred`, and `verdict_hint`.
That URL is already filled into your `config.h`.

Your board has also already been identified — plugged in and interrogated with
`esptool`:

| | |
|---|---|
| Chip | **ESP32-C6FH8 (QFN32) rev v0.2**, 8 MB embedded flash |
| USB | **USB-Serial/JTAG** (native), Espressif VID `0x303A` PID `0x1001` |
| Port | `/dev/cu.usbmodem3101` |
| MAC | `b0:a6:04:8b:4e:c0` |

Two useful consequences: **you do not need a CH340 or CP2102 driver** (the C6
speaks USB directly), and the flash is 8 MB, not the 4 MB the IDE defaults to.

To re-check the port at any time:

```bash
ls /dev/cu.usbmodem*
```

If that comes up empty, the board isn't enumerating — try a different USB-C
cable. A surprising number of cheap cables are power-only, and it's the single
most common cause of "my board is dead".

---

## 1. Install the libraries

Arduino IDE → **Tools → Manage Libraries**, then install:

| Library | Version | Used for |
|---|---|---|
| `ArduinoJson` by Benoit Blanchon | **7.x** | parsing `data.json` |
| `Adafruit SSD1306` | latest | the OLED |
| `Adafruit GFX Library` | latest | text/graphics primitives |

Adafruit SSD1306 will offer to pull in its dependencies — accept.

> ArduinoJson **7** matters. The sketch uses `JsonDocument`, which replaced v6's
> `StaticJsonDocument`/`DynamicJsonDocument`. On v6 it won't compile.

The ESP32 board core is already installed (**3.3.11**, comfortably past the 3.0
that introduced C6 support), so there's nothing to do there.

**Check:** the three libraries appear in Manage Libraries with "INSTALLED".

---

## 2. Board settings

**Tools → Board → esp32 → ESP32C6 Dev Module**, then:

| Setting | Value | Why |
|---|---|---|
| **USB CDC On Boot** | **Enabled** | ← **the one that gets everyone** |
| Port | `/dev/cu.usbmodem3101` | |
| Flash Size | **8MB (64Mb)** | your chip really has 8 MB; the IDE defaults to 4 |
| Partition Scheme | 8M with spiffs / default | needs ≳1.2 MB app space |
| Upload Speed | 921600 | drop to 115200 only if uploads fail |

Leave everything else alone.

### About USB CDC On Boot

The core ships with this **Disabled** by default. On a board like yours, where
the USB port *is* the serial port, that means `Serial.print()` goes nowhere and
the Serial Monitor stays completely blank — while the sketch runs perfectly.

It looks exactly like a dead board. It isn't. If your Serial Monitor is empty,
check this before anything else.

**Check:** board and port selected, USB CDC On Boot = Enabled.

---

## 3. Add your WiFi credentials

`firmware/gasprices/config.h` already exists with your Pages URL filled in. Open
it and replace just the two placeholders:

```c
#define WIFI_SSID  "your-network"      // <- your 2.4 GHz network
#define WIFI_PASS  "your-password"
```

> **2.4 GHz only.** The ESP32-C6 does support WiFi 6, but on the 2.4 GHz band
> only. If your router broadcasts one merged name for both bands, you may need a
> separate 2.4 GHz SSID.

`config.h` is gitignored, so your password stays on this machine — don't commit
it, and don't paste it into `config.h.example`.

**Check:** no `your-network` placeholder left in `config.h`.

---

## 4. Flash it — LED only, before wiring anything

Do this **before** connecting the display. The sketch runs fine without a screen
(it detects the absence and says so), which lets you prove WiFi, HTTPS and JSON
parsing work in isolation. If you wire the OLED first and something fails, you
won't know which half is at fault.

Open `firmware/gasprices/gasprices.ino` and press Upload.

Then **Tools → Serial Monitor at 115200 baud**. Press the RST button. Expect
roughly:

```
gasprices boot #1 (power-on)
oled: not found (LED-only mode)
wifi: 192.168.1.42
time: 2026-07-26 15:04 local
data: today=1799 pred=5 hist=7 lo=1584 hi=1825
verdict: $1.799 | EXPENSIVE | High, no dip coming | level=89% tank=1 age=3m
```

`oled: not found` is expected right now — nothing is wired yet.

**Check:** you get a `verdict:` line, and the onboard RGB LED (GPIO8) lights
**red** for EXPENSIVE. Green = FILL_NOW/GREAT, amber = NEUTRAL, red =
WAIT/EXPENSIVE, faint purple = STALE.

If the LED stays dark but serial looks right, your board may put its LED on a
pin other than GPIO8, or have none. Harmless — the OLED is the real display.

---

## 5. Identify the display

Cheap OLED modules vary, and yours is unknown, so find out what it is rather
than guessing. Wire it up:

| OLED | ESP32-C6 |
|---|---|
| VCC | **3V3** (not 5V) |
| GND | GND |
| SDA | **GPIO6** |
| SCL | **GPIO7** |

GPIO6/7 are the sketch's defaults and are safe general-purpose pins on the C6.
The C6 routes I²C through a GPIO matrix, so any free pin works — if 6 and 7
aren't broken out on your board, pick others and update `I2C_SDA_PIN` /
`I2C_SCL_PIN` in `config.h`. **Avoid** GPIO4, 5, 8, 9, 15 (strapping pins) and
GPIO24–30 (SPI flash).

Now flash `firmware/i2c_scan/i2c_scan.ino` and watch the Serial Monitor:

```
i2c_scan
SDA=GPIO6  SCL=GPIO7

scanning 0x01..0x7E ...
  device at 0x3C  <- looks like an SSD1306/SH1106 OLED  (set OLED_ADDR 0x3C in config.h)
  1 device(s).
```

**Check:** exactly one device, at `0x3C` or `0x3D`. Write it down.

**Nothing found?** That's wiring, not software:
- VCC on 3V3, not 5V
- SDA and SCL swapped (by far the most common)
- a module needing external 4.7 kΩ pull-ups — most breakouts have them onboard
- a dead/unsoldered module — try the other I²C pins to rule out the GPIO

### Which panel is it?

| Physical size | Almost always |
|---|---|
| 0.96" | SSD1306, 128×64 — **what this project targets** |
| 0.91" | SSD1306, 128×**32** |
| 1.3" | often **SH1106**, 128×64 |

Count the pixel rows if the silkscreen doesn't say. This matters — see step 7.

---

## 6. Set the address and flash for real

In `config.h`, set the address the scanner reported:

```c
#define OLED_ADDR  0x3C   // or 0x3D
```

Re-flash `gasprices.ino`. The `oled: not found` line should be gone, and the
panel should show:

```
+---------------------+
|RICHMOND HILL      0h|
|$1.799        LVL 89%|
|#####EXPENSIVE#######|
|High, no dip coming  |
|      /\_            |
+---------------------+
```

Price in double-height text, an inverted verdict bar, one line of plain English,
and a sparkline of the last 30 days.

You can preview that layout on your laptop any time, no hardware needed:

```bash
make -C tests ui
```

(In the terminal preview, double-height text shows as doubled characters —
`$$11..779999` — so that character-cell overflow is visible. On the panel it's
simply `$1.799` in a larger font.)

**Check:** the panel matches, and the price agrees with `curl`ing the feed.

---

## 7. If your panel isn't a 128×64 SSD1306

Be aware these are real work, not one-line fixes:

**128×32 (0.91").** `ui.h` hardcodes a 64-pixel-tall layout — verdict bar at
y=27, sparkline at y=52–63, both off the bottom of a 32px panel. Change
`OLED_H` to 32 and roughly half the elements have to go; realistically you'd
keep the price and the verdict bar and drop the sparkline and reason line.

**SH1106 (1.3").** Different controller. Needs the `Adafruit_SH110X` library
instead, a different constructor, and a 132→128 column offset or everything
renders shifted by 2px. The layout itself carries over unchanged.

Either way the verdict engine and backend are untouched — this is purely `ui.h`.

---

## 8. Using it

**The button.** `TANK_BUTTON_PIN` is GPIO9, the BOOT button on most C6 boards:

- **Short press** — cycle tank state FULL → HALF → LOW. Re-decides instantly
  using cached data, no refetch. Watch the verdict change: at LOW it collapses
  to `FILL NOW` / "Tank low: fill anyway", because running dry beats saving 3¢.
- **Hold >1 s** — force a refresh from the network.

If your board's button isn't on GPIO9, this silently does nothing (the pin just
reads high) — harmless, and everything else still works.

**Refresh schedule.** Twice daily at 05:30 and 16:30 Toronto time, after the
overnight reset and the afternoon moves. Change `REFRESH_HOUR_AM` /
`REFRESH_HOUR_PM` in `config.h`.

**Logging prices.** This is the input that actually makes the level meaningful —
your own station beats a regional average:

```bash
python3 backend/log_price.py 1.799
```

Or from your phone, no laptop needed: repo → **Actions → "update price data" →
Run workflow** → type the price into `local_price`.

**Battery.** Set `USE_DEEP_SLEEP 1`. The SSD1306 keeps its framebuffer while the
ESP32 sleeps, so the last verdict stays on screen the whole time. Watch for
burn-in if it'll hold one image for hours.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Serial Monitor completely blank | **USB CDC On Boot = Disabled.** Set Enabled, reflash |
| No `/dev/cu.usbmodem*` | Power-only USB cable, or board not in a working state |
| Upload fails / times out | Hold BOOT, tap RST, release BOOT to force download mode. Or lower Upload Speed to 115200 |
| `wifi: FAILED` | Wrong SSID/password, or a 5 GHz-only network — the C6 is 2.4 GHz only |
| `http: 404` | `DATA_URL` typo — test it with `curl` first |
| `http: -1` | TLS/connection failure; usually weak WiFi or DNS |
| `json: ...` | Feed returned HTML (wrong URL) or a truncated response |
| `json: implausible price` | Sanity guard rejected a value outside 0.50–3.50 $/L — the feed is wrong, not the device |
| `oled: not found` | Wrong `OLED_ADDR`, swapped SDA/SCL, or 5V on VCC. Re-run `i2c_scan` |
| `time: NTP failed` | Non-fatal. Staleness detection is disabled; verdicts still work |
| Screen on but garbled | SH1106 controller, or wrong size — see step 7 |
| Verdict looks wrong | Check the feed itself: `curl -s .../data.json`. The device only renders what the backend computed |
| `STALE` on screen | Feed older than 36 h — check the Action ran: `gh run list --workflow=update.yml` |

### Compiling without hardware

The decision logic and screen layout both build and run on your laptop:

```bash
make -C tests test    # verdict engine, 14 cases
make -C tests ui      # renders the OLED layout as ASCII
```

Useful for confirming a change is good before you go find a USB cable.
