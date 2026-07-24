// Host-side stand-in for Adafruit_GFX + Adafruit_SSD1306, enough of the API for
// ui.h to compile and draw into a text grid. Lets you iterate on the screen
// layout in a terminal instead of reflashing and squinting at a 0.96" panel.
//
// Text lands in a 21x8 character grid (6x8 px cells). Size-2 text is drawn as
// doubled characters across two rows, so overflow looks like overflow.
// Graphics primitives go into a real 128x64 pixel buffer underneath.
#ifndef GP_STUB_SSD1306_H
#define GP_STUB_SSD1306_H

#include <cstdio>
#include <cstring>
#include <string>

#define SSD1306_WHITE       1
#define SSD1306_BLACK       0
#define SSD1306_SWITCHCAPVCC 2
#define F(x) x

struct TwoWire {
  void begin(int sda = -1, int scl = -1) { (void)sda; (void)scl; }
};
static TwoWire Wire;

class Adafruit_SSD1306 {
 public:
  Adafruit_SSD1306(int w, int h, TwoWire *, int) : w_(w), h_(h) {}

  bool begin(int, uint8_t) { clearDisplay(); return true; }

  void clearDisplay() {
    std::memset(px_, 0, sizeof px_);
    for (int r = 0; r < ROWS; r++)
      for (int c = 0; c < COLS; c++) txt_[r][c] = 0;
  }
  void display() {}

  void setTextColor(int c) { color_ = c; }
  void setTextSize(int s)  { size_ = s < 1 ? 1 : s; }
  void setTextWrap(bool)   {}
  void setCursor(int x, int y) { cx_ = x; cy_ = y; }

  void drawPixel(int x, int y, int c) {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) { clipped_ = true; return; }
    px_[y][x] = (char)(c ? 1 : 0);
  }
  void fillRect(int x, int y, int w, int h, int c) {
    for (int j = y; j < y + h; j++)
      for (int i = x; i < x + w; i++) drawPixel(i, j, c);
  }
  void fillCircle(int x, int y, int r, int c) {
    for (int j = -r; j <= r; j++)
      for (int i = -r; i <= r; i++)
        if (i * i + j * j <= r * r) drawPixel(x + i, y + j, c);
  }
  void drawLine(int x0, int y0, int x1, int y1, int c) {
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
    std::printf("    +%s+\n", std::string(COLS, '-').c_str());
    for (int r = 0; r < ROWS; r++) {
      std::printf("    |");
      for (int c = 0; c < COLS; c++) {
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
    std::printf("    +%s+\n", std::string(COLS, '-').c_str());
  }

 private:
  static const int COLS = 21, ROWS = 8;

  void put(int row, int col, char ch) {
    if (row < 0 || row >= ROWS || col < 0 || col >= COLS) { clipped_ = true; return; }
    txt_[row][col] = ch;
  }

  void emit(const char *s) {
    int row = cy_ / 8;
    for (const char *p = s; *p; p++) {
      int col = cx_ / 6;
      for (int k = 0; k < size_; k++) {          // doubled glyphs at size 2
        for (int r = 0; r < size_; r++) put(row + r, col + k, *p);
      }
      cx_ += 6 * size_;
    }
  }

  int w_, h_, color_ = 1, size_ = 1, cx_ = 0, cy_ = 0;
  bool clipped_ = false;
  char px_[64][128] = {};
  char txt_[ROWS][COLS] = {};
};

#endif  // GP_STUB_SSD1306_H
