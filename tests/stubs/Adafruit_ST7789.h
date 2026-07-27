// Host-side stand-in for Adafruit_GFX + Adafruit_ST7789, enough of the API for
// ui.h to compile and draw into a text grid. Lets you iterate on the screen
// layout in a terminal instead of reflashing and squinting at a 1.47" panel.
//
// Text lands in a 53x21 character grid (6x8 px cells). Size-N text is drawn as
// N-times-doubled characters across N rows, so overflow looks like overflow.
// Graphics primitives go into a real 320x172 pixel buffer underneath.
//
// The panel is colour, the terminal is not: any non-black colour counts as an
// on pixel. That is enough to catch the thing this harness exists to catch —
// geometry that runs off the edge — but it will not tell you that you drew grey
// text on a grey background. Check contrast on the real panel.
#ifndef GP_STUB_ST7789_H
#define GP_STUB_ST7789_H

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>

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
    std::memset(px_, 0, sizeof px_);
    for (int r = 0; r < ROWS; r++)
      for (int col = 0; col < COLS; col++) txt_[r][col] = 0;
    if (c) fillRect(0, 0, w_, h_, c);
  }

  void setTextColor(uint16_t c) { color_ = c; }
  void setTextSize(int s)  { size_ = s < 1 ? 1 : s; }
  void setTextWrap(bool)   {}
  void setCursor(int x, int y) { cx_ = x; cy_ = y; }

  void drawPixel(int x, int y, uint16_t c) {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) { clipped_ = true; return; }
    px_[y][x] = (char)(c ? 1 : 0);
  }
  void fillRect(int x, int y, int w, int h, uint16_t c) {
    for (int j = y; j < y + h; j++)
      for (int i = x; i < x + w; i++) drawPixel(i, j, c);
  }
  void drawRect(int x, int y, int w, int h, uint16_t c) {
    for (int i = x; i < x + w; i++) { drawPixel(i, y, c); drawPixel(i, y + h - 1, c); }
    for (int j = y; j < y + h; j++) { drawPixel(x, j, c); drawPixel(x + w - 1, j, c); }
  }
  void drawFastHLine(int x, int y, int w, uint16_t c) {
    for (int i = x; i < x + w; i++) drawPixel(i, y, c);
  }
  void drawFastVLine(int x, int y, int h, uint16_t c) {
    for (int j = y; j < y + h; j++) drawPixel(x, j, c);
  }
  void fillCircle(int x, int y, int r, uint16_t c) {
    for (int j = -r; j <= r; j++)
      for (int i = -r; i <= r; i++)
        if (i * i + j * j <= r * r) drawPixel(x + i, y + j, c);
  }
  void drawLine(int x0, int y0, int x1, int y1, uint16_t c) {
    int dx = x1 - x0, dy = y1 - y0;
    int steps = std::abs(dx) > std::abs(dy) ? std::abs(dx) : std::abs(dy);
    if (steps == 0) { drawPixel(x0, y0, c); return; }
    for (int i = 0; i <= steps; i++)
      drawPixel(x0 + dx * i / steps, y0 + dy * i / steps, c);
  }

  void print(const char *s)   { emit(s); }
  void print(const std::string &s) { emit(s.c_str()); }
  void println(const char *s) { emit(s); cy_ += 8 * size_; cx_ = 0; }

  // --- host-only helpers ---
  bool clipped() const { return clipped_; }

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
            if (x < w_ && y < h_ && px_[y][x]) on++;
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

  void put(int row, int col, char ch) {
    if (row < 0 || row >= ROWS || col < 0 || col >= COLS) { clipped_ = true; return; }
    txt_[row][col] = ch;
  }

  void emit(const char *s) {
    int row = cy_ / 8;
    for (const char *p = s; *p; p++) {
      int col = cx_ / 6;
      for (int k = 0; k < size_; k++) {          // doubled glyphs above size 1
        for (int r = 0; r < size_; r++) put(row + r, col + k, *p);
      }
      cx_ += 6 * size_;
    }
  }

  int nativeW_ = 172, nativeH_ = 320;
  int w_ = 172, h_ = 320;
  uint16_t color_ = 0xFFFF;
  int size_ = 1, cx_ = 0, cy_ = 0;
  bool clipped_ = false;
  char px_[MAXH][MAXW] = {};
  char txt_[ROWS][COLS] = {};
};

#endif  // GP_STUB_ST7789_H
