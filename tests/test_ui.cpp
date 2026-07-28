// Renders the real ui.h screen layout for every case in vectors.csv.
// Compile-checks ui.h at the same time.
//
//   cd tests && make ui                 # ASCII dump to your terminal
//   cd tests && make golden             # also write out/<case>.ppm
//
// The PPM files are the golden images the browser preview is diffed against;
// see preview/tests/compare_render.mjs.
#include <Adafruit_ST7789.h>   // stub, from tests/stubs

// ui.h normally picks these up from config.h, which is gitignored and carries
// WiFi credentials. Define them here so the host build never needs it.
#define LCD_MOSI_PIN  6
#define LCD_SCLK_PIN  7
#define LCD_CS_PIN   14
#define LCD_DC_PIN   15
#define LCD_RST_PIN  21
#define LCD_BL_PIN   22
#define LCD_ROTATION  1

#include "../firmware/gasprices/ui.h"
#include "../firmware/gasprices/verdict.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

static std::vector<std::string> split(const std::string &s, char sep) {
  std::vector<std::string> out;
  std::stringstream ss(s);
  std::string item;
  while (std::getline(ss, item, sep)) out.push_back(item);
  return out;
}

// A plausible 14-day window to give the sparkline something to draw. Derived
// from the case's own window and today's price so it needs no extra columns —
// preview/states.js reproduces this exactly, integer division included.
static void synth_history(const GpInput *in, int32_t *hist, int n) {
  for (int i = 0; i < n; i++) {
    int32_t lo = in->window_lo, hi = in->window_hi;
    hist[i] = (i % 2) ? lo + (hi - lo) * (i + 2) / 18 : hi - (hi - lo) * i / 20;
  }
  hist[n - 1] = in->today;
}

int main(int argc, char **argv) {
  const char *path = nullptr;
  const char *ppmDir = nullptr;

  for (int i = 1; i < argc; i++) {
    if (std::strcmp(argv[i], "--ppm") == 0 && i + 1 < argc) ppmDir = argv[++i];
    else if (!path) path = argv[i];
  }
  if (!path) path = "vectors.csv";

  std::ifstream f(path);
  if (!f) { std::fprintf(stderr, "cannot open %s\n", path); return 2; }

  char ppmPath[512];

  uiBegin();
  std::printf("\n== boot screen\n");
  uiMessage("gasprices", "connecting...");
  lcd.dump();
  if (ppmDir) {
    std::snprintf(ppmPath, sizeof ppmPath, "%s/boot.ppm", ppmDir);
    if (!lcd.writePPM(ppmPath)) {
      std::fprintf(stderr, "cannot write %s\n", ppmPath);
      return 2;
    }
  }

  GpConfig cfg = gp_default_config();
  std::string line;
  int clipped = 0, cases = 0;

  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#' || line.rfind("name,", 0) == 0) continue;
    std::vector<std::string> c = split(line, ',');
    if (c.size() < 18) continue;

    GpInput in{};
    in.today = std::atoi(c[1].c_str());
    if (c[2] != "-")
      for (const std::string &p : split(c[2], '|'))
        if (in.pred_len < GP_PRED_MAX) in.pred[in.pred_len++] = std::atoi(p.c_str());
    in.window_lo   = std::atoi(c[3].c_str());
    in.window_hi   = std::atoi(c[4].c_str());
    in.age_minutes = std::atoi(c[5].c_str());
    in.tank = c[6] == "LOW" ? TANK_LOW : c[6] == "FULL" ? TANK_FULL : TANK_HALF;

    // Display-only arguments. Columns, not derived from `today`, so the
    // C++/JS pixel diff exercises the renderer and not a shared guess.
    const std::string &label = c[11];
    uint8_t stationIdx   = (uint8_t)std::atoi(c[12].c_str());
    uint8_t stationCount = (uint8_t)std::atoi(c[13].c_str());
    bool    isCheapest   = std::atoi(c[14].c_str()) != 0;
    int32_t vsBest       = std::atoi(c[15].c_str());
    int32_t bestSave     = std::atoi(c[16].c_str());
    bool    bestConfident = std::atoi(c[17].c_str()) != 0;

    GpVerdict v{};
    gp_evaluate(&in, &cfg, &v);

    int32_t hist[14];
    synth_history(&in, hist, 14);

    std::printf("\n== %s (tank=%s)\n", c[0].c_str(), c[6].c_str());
    lcd.clearClipped();
    uiRender(&in, &v, hist, 14, in.age_minutes >= 0,
             label == "-" ? nullptr : label.c_str(),
             bestSave, bestConfident,
             stationIdx, stationCount, isCheapest, vsBest);
    lcd.dump();
    cases++;

    if (ppmDir) {
      std::snprintf(ppmPath, sizeof ppmPath, "%s/%s.ppm", ppmDir, c[0].c_str());
      if (!lcd.writePPM(ppmPath)) {
        std::fprintf(stderr, "cannot write %s\n", ppmPath);
        return 2;
      }
    }
    if (lcd.clipped()) {
      std::printf("  ^ drew outside the %dx%d panel\n", LCD_W, LCD_H);
      clipped++;
    }
  }

  if (ppmDir) std::printf("\nwrote %d golden image(s) to %s/\n", cases + 1, ppmDir);
  if (clipped) {
    std::printf("\n%d case(s) drew outside the %dx%d panel\n", clipped, LCD_W, LCD_H);
    return 1;
  }
  std::printf("\nall cases fit the panel\n");
  return 0;
}
