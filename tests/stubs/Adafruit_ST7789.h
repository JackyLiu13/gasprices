// Host-side stand-in for Adafruit_GFX + Adafruit_ST7789, enough of the API for
// ui.h to compile and draw. Lets you iterate on the screen layout on your laptop
// instead of reflashing and squinting at a 1.47" panel.
//
// It draws each frame twice, from one pass of ui.h:
//
//   px_   a real 320x172 RGB565 framebuffer, glyphs rasterised from the same
//         vendored 5x7 font the device uses. writePPM() dumps it, and
//         preview/tests/compare_render.mjs diffs that against the JS renderer,
//         which is what lets the browser preview be trusted.
//   txt_  a 53x21 character grid. Coarse, but you can read strings out of it in
//         a terminal, and it is what has historically caught overflow bugs.
//
// dump() shades from gfx_, a copy of the framebuffer that glyphs are kept out
// of, so the terminal view still reads as "letters here, graphics there". Let
// real glyph pixels into it and every text row grows a fog of stray dots.
//
// The primitives here are ports of Adafruit_GFX's own — Bresenham line, Bresenham
// circle, the classic-font drawChar — not approximations of them. That matters:
// an approximation only proves the preview matches itself, whereas these make it
// match the device, pixel for pixel, for everything except panel gamma.
//
// What it still does NOT model: ST7789 gamma, backlight, viewing angle. Positions
// and clipping are trustworthy here; colour choices need one look at hardware.
#ifndef GP_STUB_ST7789_H
#define GP_STUB_ST7789_H

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <utility>

#include "glcdfont.h"

#define F(x) x
#define OUTPUT 1
#define HIGH   1
#define LOW    0

static inline void pinMode(int, int) {}
static inline void digitalWrite(int, int) {}

struct SPIClass {
  void begin(int sclk = -1, int miso = -1, int mosi = -1, int cs = -1) {
    (void)sclk; (void)miso; (void)mosi; (void)cs;
  }
};
static SPIClass SPI;

class Adafruit_ST7789 {
 public:
  Adafruit_ST7789(SPIClass *, int cs, int dc, int rst) {
    (void)cs; (void)dc; (void)rst;
  }

  // init() takes native portrait dimensions; setRotation() decides the axes we
  // actually draw in, exactly as on the real device.
  void init(int w, int h) { nativeW_ = w; nativeH_ = h; w_ = w; h_ = h; }
  void setSPISpeed(uint32_t) {}
  void setRotation(int r) {
    if (r & 1) { w_ = nativeH_; h_ = nativeW_; }
    else       { w_ = nativeW_; h_ = nativeH_; }
  }

  int width()  const { return w_; }
  int height() const { return h_; }

  void fillScreen(uint16_t c) {
    for (int r = 0; r < ROWS; r++)
      for (int col = 0; col < COLS; col++) txt_[r][col] = 0;
    fillRect(0, 0, w_, h_, c);
  }

  // One argument sets background == foreground, which Adafruit_GFX treats as
  // "transparent": only set bits get drawn, no background fill. ui.h relies on
  // that everywhere it draws text over the verdict bar.
  void setTextColor(uint16_t c) { color_ = c; bg_ = c; }
  void setTextColor(uint16_t c, uint16_t bg) { color_ = c; bg_ = bg; }
  void setTextSize(int s)  { size_ = s < 1 ? 1 : s; }
  void setTextWrap(bool w) { wrap_ = w; }
  void setCursor(int x, int y) { cx_ = x; cy_ = y; }

  void drawPixel(int x, int y, uint16_t c) {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) { clipped_ = true; return; }
    px_[y][x] = c;
    if (!textPass_) gfx_[y][x] = c;
  }
  void fillRect(int x, int y, int w, int h, uint16_t c) {
    for (int j = y; j < y + h; j++)
      for (int i = x; i < x + w; i++) drawPixel(i, j, c);
  }
  void drawFastHLine(int x, int y, int w, uint16_t c) {
    for (int i = x; i < x + w; i++) drawPixel(i, y, c);
  }
  void drawFastVLine(int x, int y, int h, uint16_t c) {
    for (int j = y; j < y + h; j++) drawPixel(x, j, c);
  }
  void drawRect(int x, int y, int w, int h, uint16_t c) {
    drawFastHLine(x, y, w, c);
    drawFastHLine(x, y + h - 1, w, c);
    drawFastVLine(x, y, h, c);
    drawFastVLine(x + w - 1, y, h, c);
  }

  // Adafruit_GFX::writeLine — Bresenham, with the steep/shallow swap. A DDA
  // approximation picks visibly different pixels on shallow slopes, which is
  // exactly the sparkline's case.
  void drawLine(int x0, int y0, int x1, int y1, uint16_t c) {
    int steep = std::abs(y1 - y0) > std::abs(x1 - x0);
    if (steep) { std::swap(x0, y0); std::swap(x1, y1); }
    if (x0 > x1) { std::swap(x0, x1); std::swap(y0, y1); }

    int dx = x1 - x0, dy = std::abs(y1 - y0);
    int err = dx / 2;
    int ystep = (y0 < y1) ? 1 : -1;

    for (; x0 <= x1; x0++) {
      if (steep) drawPixel(y0, x0, c);
      else       drawPixel(x0, y0, c);
      err -= dy;
      if (err < 0) { y0 += ystep; err += dx; }
    }
  }

  // Adafruit_GFX::fillCircle + fillCircleHelper — a Bresenham circle. A radius
  // test disagrees with it at r=2, which is the size of the sparkline's dot.
  void fillCircle(int x0, int y0, int r, uint16_t c) {
    drawFastVLine(x0, y0 - r, 2 * r + 1, c);
    fillCircleHelper(x0, y0, r, 3, 0, c);
  }

  void print(const char *s)   { emit(s); }
  void print(const std::string &s) { emit(s.c_str()); }
  void println(const char *s) { emit(s); cy_ += 8 * size_; cx_ = 0; }

  // --- host-only helpers ---
  bool clipped() const { return clipped_; }
  void clearClipped() { clipped_ = false; }

  uint16_t pixel(int x, int y) const {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) return 0;
    return px_[y][x];
  }

  // Binary PPM (P6). Chosen so the golden diff needs no image library on either
  // side: trivial to emit here, trivial to parse in node.
  //
  // RGB565 -> RGB888 by bit replication. Each 5- or 6-bit channel value maps to
  // a distinct byte, so comparing PPMs is exactly comparing RGB565 — the diff
  // loses nothing.
  bool writePPM(const char *path) const {
    std::FILE *f = std::fopen(path, "wb");
    if (!f) return false;
    std::fprintf(f, "P6\n%d %d\n255\n", w_, h_);
    for (int y = 0; y < h_; y++) {
      for (int x = 0; x < w_; x++) {
        uint16_t p = px_[y][x];
        int r5 = (p >> 11) & 0x1F, g6 = (p >> 5) & 0x3F, b5 = p & 0x1F;
        unsigned char rgb[3] = {
            (unsigned char)((r5 << 3) | (r5 >> 2)),
            (unsigned char)((g6 << 2) | (g6 >> 4)),
            (unsigned char)((b5 << 3) | (b5 >> 2)),
        };
        std::fwrite(rgb, 1, 3, f);
      }
    }
    return std::fclose(f) == 0;
  }

  void dump() const {
    int cols = w_ / 6, rows = h_ / 8;
    if (cols > COLS) cols = COLS;
    if (rows > ROWS) rows = ROWS;
    std::printf("    +%s+\n", std::string(cols, '-').c_str());
    for (int r = 0; r < rows; r++) {
      std::printf("    |");
      for (int c = 0; c < cols; c++) {
        char ch = txt_[r][c];
        if (ch) { std::putchar(ch); continue; }
        int on = 0;
        for (int j = 0; j < 8; j++)
          for (int i = 0; i < 6; i++) {
            int x = c * 6 + i, y = r * 8 + j;
            if (x < w_ && y < h_ && gfx_[y][x]) on++;
          }
        std::putchar(on == 0 ? ' ' : on > 24 ? '#' : on > 8 ? '+' : '.');
      }
      std::printf("|\n");
    }
    std::printf("    +%s+\n", std::string(cols, '-').c_str());
  }

 private:
  static const int COLS = 53, ROWS = 21;
  static const int MAXW = 320, MAXH = 320;

  void fillCircleHelper(int x0, int y0, int r, uint8_t corners, int delta,
                        uint16_t c) {
    int f = 1 - r, ddF_x = 1, ddF_y = -2 * r;
    int x = 0, y = r, px = x, py = y;
    delta++;                                   // avoids some +1's in the loop

    while (x < y) {
      if (f >= 0) { y--; ddF_y += 2; f += ddF_y; }
      x++;
      ddF_x += 2;
      f += ddF_x;
      if (x < (y + 1)) {
        if (corners & 1) drawFastVLine(x0 + x, y0 - y, 2 * y + delta, c);
        if (corners & 2) drawFastVLine(x0 - x, y0 - y, 2 * y + delta, c);
      }
      if (y != py) {
        if (corners & 1) drawFastVLine(x0 + py, y0 - px, 2 * px + delta, c);
        if (corners & 2) drawFastVLine(x0 - py, y0 - px, 2 * px + delta, c);
        py = y;
      }
      px = x;
    }
  }

  // Adafruit_GFX::drawChar, classic-font branch. Note the whole-glyph clip: a
  // glyph starting past the right edge draws nothing at all, which is what the
  // device does too, so a string running off the panel visibly stops rather
  // than wrapping.
  void drawChar(int x, int y, unsigned char c, uint16_t color, uint16_t bg,
                int size) {
    if ((x >= w_) || (y >= h_) || ((x + 6 * size - 1) < 0) ||
        ((y + 8 * size - 1) < 0))
      return;

    if (c >= 176) c++;                         // classic charset behaviour

    textPass_ = true;                          // keep glyphs out of gfx_
    struct Restore { bool *f; ~Restore() { *f = false; } } restore{&textPass_};

    for (int i = 0; i < 5; i++) {
      uint8_t line = gp_glcdfont[c * 5 + i];
      for (int j = 0; j < 8; j++, line >>= 1) {
        if (line & 1) {
          if (size == 1) drawPixel(x + i, y + j, color);
          else fillRect(x + i * size, y + j * size, size, size, color);
        } else if (bg != color) {
          if (size == 1) drawPixel(x + i, y + j, bg);
          else fillRect(x + i * size, y + j * size, size, size, bg);
        }
      }
    }
    if (bg != color) {                         // opaque: fill the 6th column too
      if (size == 1) drawFastVLine(x + 5, y, 8, bg);
      else fillRect(x + 5 * size, y, size, 8 * size, bg);
    }
  }

  void put(int row, int col, char ch) {
    if (row < 0 || row >= ROWS || col < 0 || col >= COLS) { clipped_ = true; return; }
    txt_[row][col] = ch;
  }

  // One pass, both outputs: real glyphs into px_, a readable letter into txt_.
  void emit(const char *s) {
    for (const char *p = s; *p; p++) {
      if (wrap_ && (cx_ + size_ * 6) > w_) { cx_ = 0; cy_ += 8 * size_; }
      int row = cy_ / 8, col = cx_ / 6;
      for (int k = 0; k < size_; k++) {          // doubled glyphs above size 1
        for (int r = 0; r < size_; r++) put(row + r, col + k, *p);
      }
      drawChar(cx_, cy_, (unsigned char)*p, color_, bg_, size_);
      cx_ += 6 * size_;
    }
  }

  int nativeW_ = 172, nativeH_ = 320;
  int w_ = 172, h_ = 320;
  uint16_t color_ = 0xFFFF, bg_ = 0xFFFF;
  int size_ = 1, cx_ = 0, cy_ = 0;
  bool wrap_ = true;
  bool clipped_ = false;
  bool textPass_ = false;
  uint16_t px_[MAXH][MAXW] = {};      // everything: what the panel shows
  uint16_t gfx_[MAXH][MAXW] = {};     // everything except glyphs: what dump() shades
  char txt_[ROWS][COLS] = {};
};

#endif  // GP_STUB_ST7789_H
