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

### Your board

| | |
|---|---|
| Board | **Waveshare ESP32-C6-LCD-1.47** |
| Chip | **ESP32-C6FH8 (QFN32) rev v0.2**, 8 MB embedded flash |
| Display | **onboard 1.47" ST7789, 172×320**, SPI, soldered — nothing to wire |
| USB | **USB-Serial/JTAG** (native), Espressif VID `0x303A` PID `0x1001` |
| Port | `/dev/cu.usbmodem3101` |

> **Identify the board, not just the chip.** `esptool` reports the *chip*
> (`ESP32-C6FH8`) and is accurate about it — but the chip tells you nothing about
> which GPIOs are wired to what, and that is where all the pain lives. Two boards
> with the same C6 have completely different pinouts. Read the silkscreen: this
> one says `ESP32-C6-LCD-1.47` along the left edge.

Two useful consequences: **you do not need a CH340 or CP2102 driver** (the C6
speaks USB directly), and the flash is 8 MB, not the 4 MB the IDE defaults to.

### The pins that matter

The LCD is hardwired to these, and **none of them are broken out on the
headers**:

| Function | GPIO |
|---|---|
| LCD MOSI | 6 |
| LCD SCLK | 7 |
| LCD CS | 14 |
| LCD DC | 15 |
| LCD RST | 21 |
| LCD backlight | 22 |
| RGB LED | 8 |
| BOOT button | 9 |

The headers expose only **GPIO 0–5, 9, 12, 13, 18, 19, 20, 23**, plus `3V3`,
`GND`, `5V`, `RXD`, `TXD`. Note what is *absent*: **GPIO6 and GPIO7 are not
available.** If you find a guide telling you to wire an I²C display to 6 and 7,
it is describing a different board, and following it here means driving the
LCD's own SPI data and clock lines.

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
| `Adafruit ST7735 and ST7789 Library` | latest | the LCD |
| `Adafruit GFX Library` | latest | text/graphics primitives |

Both Adafruit libraries will offer to pull in dependencies (`Adafruit BusIO`) —
accept.

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
| Partition Scheme | 8M with spiffs / default | the sketch is ~1.17 MB |
| Upload Speed | 921600 | drop to 115200 only if uploads fail |

Leave everything else alone.

### About USB CDC On Boot

The core ships with this **Disabled** by default. On a board like yours, where
the USB port *is* the serial port, that means `Serial.print()` goes nowhere and
the Serial Monitor stays completely blank — while the sketch runs perfectly.

It looks exactly like a dead board. It isn't. If your Serial Monitor is empty,
check this before anything else.

### Partition scheme is not optional

The sketch is ~1,175,000 bytes. The default 4 MB scheme gives the app 1.2 MB, so
it fits at about **89%** — technically, with almost nothing spare. Pick the 8 MB
scheme and you're at 35% instead. Anything you add later will thank you.

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

## 4. Flash it

Nothing to wire — the display is part of the board. Open
`firmware/gasprices/gasprices.ino` and press Upload.

Then **Tools → Serial Monitor at 115200 baud**. Press the RST button. Expect
roughly:

```
gasprices boot #1 (power-on)
lcd: 320x172 ST7789 rot=1
wifi: 10.0.0.133
time: 2026-07-26 18:55 local
data: today=1799 pred=5 hist=7 lo=1584 hi=1825
verdict: $1.799 | EXPENSIVE | High, no dip coming | level=89% tank=1 age=90m
```

And the panel should show:

```
+-----------------------------------------------------+
| RICHMOND HILL                                    1h |
|-----------------------------------------------------|
| $1.799                               LVL 89%        |
|                                   [######----]      |
|##################EXPENSIVE##########################|   <- red
|                                                     |
| High, no dip coming                                 |
| TANK HALF                                     ^0.2c |
|      ~~~~ sparkline of the rolling window ~~~~      |
+-----------------------------------------------------+
```

Price in size-4 text, a verdict bar filled in the verdict colour, one line of
plain English, the tank state, and a sparkline of the rolling window.

**Check:** you get a `verdict:` line, the panel matches, the price agrees with
`curl`ing the feed, and the onboard RGB LED (GPIO8) lights **red** for
EXPENSIVE. Green = FILL_NOW/GREAT, amber = NEUTRAL, red = WAIT/EXPENSIVE, faint
purple = STALE. The bar on screen and the LED always use the same colour.

**Screen upside down?** Set `LCD_ROTATION` to `3` in `config.h` and reflash. `1`
and `3` are the two landscape orientations; `0` and `2` are portrait, which the
layout isn't designed for.

You can preview the layout on your laptop any time, no hardware needed:

```bash
make -C tests ui
```

(In the terminal preview, large text shows as doubled characters —
`$$$$1111....777799999999` — so that character-cell overflow is visible. On the
panel it's simply `$1.799` in a large font.)

---

## 5. If your board is different

This project now targets the onboard ST7789. If you have a plain C6 devkit and a
separate display, the work is in `ui.h` only — the verdict engine and backend are
untouched either way.

**Different ST7789 size.** `ui.h` hardcodes a 320×172 landscape layout. Change
`LCD_W`/`LCD_H` and the y-coordinates in `uiRender()`. Always call
`init()` with the panel's **native portrait** dimensions and then `setRotation()`
— the controller's centring offset is computed from the native size, so
initializing in landscape puts the offset on the wrong axis and shifts
everything sideways.

**An I²C SSD1306 instead.** Needs `Adafruit_SSD1306`, a `Wire.begin(sda, scl)`
call, an address (`0x3C` or `0x3D`), and the whole layout rescaled to 128×64.
Wire it to header pins — **GPIO2/3 are free and known-good**; do not use 6/7.
`firmware/i2c_scan` will find the address.

**A monochrome panel.** The verdict bar relies on colour to carry meaning. On a
mono panel, go back to an inverted fill (white bar, black text) as the original
128×64 layout did.

---

## 6. Using it

**The button.** `TANK_BUTTON_PIN` is GPIO9, the BOOT button:

- **Short press** — cycle tank state FULL → HALF → LOW. Re-decides instantly
  using cached data, no refetch. Watch the verdict change: at LOW it collapses
  to `FILL NOW` / "Tank low: fill anyway", because running dry beats saving 3¢.
  The tank state is shown on screen, so you get direct feedback.
- **Hold >1 s** — force a refresh from the network.

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

**Battery.** Set `USE_DEEP_SLEEP 1`. Note this behaves differently from the
SSD1306 this project originally targeted: the ST7789 keeps its own GRAM through
sleep, but the backlight is a plain GPIO and **GPIO22 is not an RTC pin on the
C6**, so it drops on sleep entry and the screen goes dark until the next wake.
You get long battery life, not a persistent display.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Serial Monitor completely blank | **USB CDC On Boot = Disabled.** Set Enabled, reflash |
| No `/dev/cu.usbmodem*` | Power-only USB cable, or board not in a working state |
| Upload fails / times out | Hold BOOT, tap RST, release BOOT to force download mode. Or lower Upload Speed to 115200 |
| `wifi: FAILED` | Wrong SSID/password, or a 5 GHz-only network — the C6 is 2.4 GHz only. A single failure at boot can also just be a slow association against the 20 s `WIFI_TIMEOUT_MS`; try a reset before changing anything |
| `http: 404` | `DATA_URL` typo — test it with `curl` first |
| `http: -1` | TLS/connection failure; usually weak WiFi or DNS |
| `json: ...` | Feed returned HTML (wrong URL) or a truncated response |
| `json: implausible price` | Sanity guard rejected a value outside 0.50–3.50 $/L — the feed is wrong, not the device |
| Screen completely black | Backlight pin. `LCD_BL_PIN` (GPIO22) must be driven HIGH — `uiBegin()` does this. Verify serial shows the `lcd:` line |
| Screen upside down | `LCD_ROTATION` 1 ↔ 3 in `config.h` |
| Everything shifted sideways ~34px | `init()` was called with landscape dimensions. It must take native portrait (172, 320), with `setRotation()` after |
| `time: NTP failed` | Non-fatal. Staleness detection is disabled; verdicts still work |
| Verdict looks wrong | Check the feed itself: `curl -s .../data.json`. The device only renders what the backend computed |
| `STALE` on screen | Feed older than 36 h — check the Action ran: `gh run list --workflow=update.yml` |

### If you ever run an I²C scan on this board

`firmware/i2c_scan` defaults to free header pins. If you point it at GPIO6/7 it
will report **~120 devices, and a different set each run**. That is not a bus
full of chips — it's the scanner clocking an I²C protocol into the LCD's SPI
data and clock lines. "Everything ACKs" means *wrong pins*, exactly as
"nothing found" means *nothing connected*.

### Compiling without hardware

The decision logic and screen layout both build and run on your laptop:

```bash
make -C tests test    # verdict engine, 14 cases
make -C tests ui      # renders the LCD layout as ASCII
```

Useful for confirming a change is good before you go find a USB cable.

### Building from the command line

Everything above works headlessly too, which is handy for scripted reflashes:

```bash
brew install arduino-cli
arduino-cli config set directories.user ~/Documents/Arduino
arduino-cli lib install ArduinoJson "Adafruit ST7735 and ST7789 Library" "Adafruit GFX Library"

arduino-cli compile --upload -p /dev/cu.usbmodem3101 \
  -b "esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,UploadSpeed=921600" \
  firmware/gasprices
```

The FQBN option string encodes exactly the same choices as the Tools menu in
step 2 — `CDCOnBoot=cdc` is the "USB CDC On Boot → Enabled" setting.
