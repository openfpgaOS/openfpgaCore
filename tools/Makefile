CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -std=c99
LDFLAGS =

all: sf2_to_ofsf

sf2_to_ofsf: sf2_to_ofsf.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS) -lm

clean:
	rm -f sf2_to_ofsf

.PHONY: all clean
