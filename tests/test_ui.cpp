// Renders the real ui.h screen layout to your terminal for every case in
// vectors.csv. Compile-checks ui.h at the same time.
//
//   cd tests && make ui && ./test_ui
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

int main(int argc, char **argv) {
  const char *path = (argc > 1) ? argv[1] : "vectors.csv";
  std::ifstream f(path);
  if (!f) { std::fprintf(stderr, "cannot open %s\n", path); return 2; }

  uiBegin();
  std::printf("\n== boot screen\n");
  uiMessage("gasprices", "connecting...");
  lcd.dump();

  GpConfig cfg = gp_default_config();
  std::string line;
  int clipped = 0;

  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#' || line.rfind("name,", 0) == 0) continue;
    std::vector<std::string> c = split(line, ',');
    if (c.size() < 11) continue;

    GpInput in{};
    in.today = std::atoi(c[1].c_str());
    if (c[2] != "-")
      for (const std::string &p : split(c[2], '|'))
        if (in.pred_len < GP_PRED_MAX) in.pred[in.pred_len++] = std::atoi(p.c_str());
    in.window_lo   = std::atoi(c[3].c_str());
    in.window_hi   = std::atoi(c[4].c_str());
    in.age_minutes = std::atoi(c[5].c_str());
    in.tank = c[6] == "LOW" ? TANK_LOW : c[6] == "FULL" ? TANK_FULL : TANK_HALF;

    GpVerdict v{};
    gp_evaluate(&in, &cfg, &v);

    // A plausible 14-day window to give the sparkline something to draw.
    int32_t hist[14];
    for (int i = 0; i < 14; i++) {
      int32_t lo = in.window_lo, hi = in.window_hi;
      hist[i] = (i % 2) ? lo + (hi - lo) * (i + 2) / 18 : hi - (hi - lo) * i / 20;
    }
    hist[13] = in.today;

    std::printf("\n== %s (tank=%s)\n", c[0].c_str(), c[6].c_str());
    // Alternate between a schema-2 feed (station named, savings shown) and a
    // bare one, so both header paths and the longest plausible label are drawn.
    bool withStation = (in.today % 2) == 0;
    uiRender(&in, &v, hist, 14, in.age_minutes >= 0,
             withStation ? "PETROCAN MAJMAC" : nullptr,
             withStation ? 90 : 0,
             withStation ? (in.today % 4) == 0 : true);
    lcd.dump();
    if (lcd.clipped()) clipped++;
  }

  if (clipped) {
    std::printf("\n%d case(s) drew outside the 320x172 panel\n", clipped);
    return 1;
  }
  std::printf("\nall cases fit the panel\n");
  return 0;
}
