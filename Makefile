# Dev helpers. Screenshots need a display and a book under CHAPTERFIT_LIBRARY.

LIBRARY ?= /srv/media/books
BOOK ?= $(LIBRARY)/Alastair Reynolds/Revelation Space/Revelation Space.mp3
OUT ?= out
PYTHON ?= .venv/bin/python
STEPS := scan match place finish audio librivox
SIZES := 1600x1030 1366x768

.PHONY: screenshots
screenshots:
	@mkdir -p $(OUT)
	@test -e "$(BOOK)" || (echo "BOOK not found: $(BOOK)"; exit 1)
	@for size in $(SIZES); do \
	  for step in $(STEPS); do \
	    echo "$$step $$size"; \
	    CHAPTERFIT_LIBRARY="$(LIBRARY)" $(PYTHON) -m chaptertools.gui \
	      --screenshot "$(OUT)/$${step}-$${size}.png" \
	      --book "$(BOOK)" \
	      --step "$$step" \
	      --size "$$size" \
	      --library "$(LIBRARY)" || exit 1; \
	  done; \
	done
	@echo "wrote $(OUT)/ ($(words $(STEPS)) steps × $(words $(SIZES)) sizes)"
