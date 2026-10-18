# Dev helpers. Screenshots need a display and a book under CHAPTERFIT_LIBRARY.

LIBRARY ?= /srv/media/books
BOOK ?= $(LIBRARY)/Alastair Reynolds/Revelation Space/Revelation Space.mp3
OUT ?= out
PYTHON ?= .venv/bin/python
# light, dark, or both. `make screenshots MODE=dark`
MODE ?= light dark
STEPS := scan match place finish audio librivox
SIZES := 1600x1030 1366x768

.PHONY: screenshots
screenshots:
	@command -v xvfb-run >/dev/null || { echo "xvfb-run is missing. Run: sudo apt install xvfb"; exit 1; }
	@test -e "$(BOOK)" || (echo "BOOK not found: $(BOOK)"; exit 1)
	@for mode in $(MODE); do \
	  mkdir -p "$(OUT)/$$mode"; \
	  for size in $(SIZES); do \
	    for step in $(STEPS); do \
	      echo "$$mode $$step $$size"; \
	      xvfb-run -a -s "-screen 0 1920x1200x24" env CHAPTERFIT_LIBRARY="$(LIBRARY)" $(PYTHON) -m chaptertools.gui \
	        --screenshot "$(OUT)/$$mode/$${step}-$${size}.png" \
	        --appearance "$$mode" \
	        --book "$(BOOK)" \
	        --step "$$step" \
	        --size "$$size" \
	        --library "$(LIBRARY)" || exit 1; \
	    done; \
	  done; \
	done
	@echo "wrote $(OUT)/{$(MODE)} ($(words $(STEPS)) steps × $(words $(SIZES)) sizes)"
