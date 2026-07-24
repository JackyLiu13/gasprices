CXXFLAGS = -std=c++17 -Wall -Wextra -O1
HDRS = ../firmware/gasprices/verdict.h

test_verdict: test_verdict.cpp $(HDRS)
	$(CXX) $(CXXFLAGS) -o $@ test_verdict.cpp

test_ui: test_ui.cpp $(HDRS) ../firmware/gasprices/ui.h stubs/Adafruit_SSD1306.h
	$(CXX) $(CXXFLAGS) -Istubs -o $@ test_ui.cpp

.PHONY: all test ui clean
all: test ui

test: test_verdict
	./test_verdict vectors.csv

ui: test_ui
	./test_ui vectors.csv

clean:
	rm -f test_verdict test_ui
