CXXFLAGS = -std=c++17 -Wall -Wextra -O1
HDRS = ../firmware/gasprices/verdict.h

test_verdict: test_verdict.cpp $(HDRS)
	$(CXX) $(CXXFLAGS) -o $@ test_verdict.cpp

# layout.h matters here: without it, editing the layout leaves a stale binary
# that writes golden images of the old geometry, and the pixel diff then blames
# the JS renderer for a change it got right.
test_ui: test_ui.cpp $(HDRS) ../firmware/gasprices/ui.h ../firmware/gasprices/layout.h \
         stubs/Adafruit_ST7789.h stubs/glcdfont.h
	$(CXX) $(CXXFLAGS) -Istubs -o $@ test_ui.cpp

.PHONY: all test ui golden clean
all: test ui

test: test_verdict
	./test_verdict vectors.csv

ui: test_ui
	./test_ui vectors.csv

# Golden images for the browser preview's pixel diff. out/ is gitignored: these
# are regenerated, never reviewed, and only ever compared byte-for-byte.
golden: test_ui
	@mkdir -p out
	./test_ui vectors.csv --ppm out > /dev/null

clean:
	rm -f test_verdict test_ui
	rm -rf out
