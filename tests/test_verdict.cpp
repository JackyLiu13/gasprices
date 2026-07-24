// Host-side test for the firmware's decision engine.
//   cd tests && make && ./test_verdict
// No Arduino, no hardware. Tune thresholds here, not on the bench.
#include "../firmware/gasprices/verdict.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
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

static TankState parse_tank(const std::string &s) {
  if (s == "LOW")  return TANK_LOW;
  if (s == "FULL") return TANK_FULL;
  return TANK_HALF;
}

static const char *canonical(Verdict v) {
  switch (v) {
    case V_FILL_NOW:  return "FILL_NOW";
    case V_WAIT:      return "WAIT";
    case V_GREAT:     return "GREAT";
    case V_EXPENSIVE: return "EXPENSIVE";
    case V_NEUTRAL:   return "NEUTRAL";
    default:          return "STALE";
  }
}

int main(int argc, char **argv) {
  const char *path = (argc > 1) ? argv[1] : "vectors.csv";
  std::ifstream f(path);
  if (!f) {
    std::fprintf(stderr, "cannot open %s\n", path);
    return 2;
  }

  GpConfig cfg = gp_default_config();
  int pass = 0, fail = 0;
  std::string line;

  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#' || line.rfind("name,", 0) == 0) continue;
    std::vector<std::string> c = split(line, ',');
    if (c.size() < 11) continue;

    GpInput in{};
    in.today       = std::atoi(c[1].c_str());
    in.pred_len    = 0;
    if (c[2] != "-") {
      for (const std::string &p : split(c[2], '|')) {
        if (in.pred_len < GP_PRED_MAX) in.pred[in.pred_len++] = std::atoi(p.c_str());
      }
    }
    in.window_lo   = std::atoi(c[3].c_str());
    in.window_hi   = std::atoi(c[4].c_str());
    in.age_minutes = std::atoi(c[5].c_str());
    in.tank        = parse_tank(c[6]);

    const std::string &want_v = c[7];
    int want_level = std::atoi(c[8].c_str());
    int want_days  = std::atoi(c[9].c_str());
    int want_save  = std::atoi(c[10].c_str());

    GpVerdict v{};
    gp_evaluate(&in, &cfg, &v);

    char reason[48];
    gp_reason(&in, &v, reason, sizeof reason);

    bool fits = std::strlen(reason) <= GP_REASON_MAX_CHARS;
    if (!fits) {
      std::printf("  FAIL %-28s reason is %zu chars, panel fits %d: \"%s\"\n",
                  c[0].c_str(), std::strlen(reason), GP_REASON_MAX_CHARS, reason);
    }

    bool ok = fits &&
              want_v == canonical(v.verdict) &&
              want_level == v.level_pct &&
              want_days == v.days_to_wait &&
              want_save == v.save;

    if (ok) {
      pass++;
      std::printf("  ok   %-28s %-9s level=%-4d days=%d  \"%s\"\n",
                  c[0].c_str(), canonical(v.verdict), v.level_pct, v.days_to_wait, reason);
    } else {
      fail++;
      std::printf("  FAIL %-28s got %s/level=%d/days=%d/save=%d "
                  "want %s/level=%d/days=%d/save=%d\n",
                  c[0].c_str(), canonical(v.verdict), v.level_pct, v.days_to_wait, v.save,
                  want_v.c_str(), want_level, want_days, want_save);
    }
  }

  std::printf("\n%d passed, %d failed\n", pass, fail);
  return fail == 0 ? 0 : 1;
}
