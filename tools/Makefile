CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -std=c99 -I../src/firmware/api
LDFLAGS =

# Reuse the runtime SF2 unit-conversion helpers so baked OFSF fields are
# bit-identical to whatever the runtime would have computed at note-on.
TABLES_SRC = ../src/firmware/api/of_smp_tables.c

all: sf2_to_ofsf

sf2_to_ofsf: sf2_to_ofsf.c $(TABLES_SRC) ../src/firmware/api/of_smp_tables.h ../src/firmware/api/of_smp_bank.h
	$(CC) $(CFLAGS) -o $@ sf2_to_ofsf.c $(TABLES_SRC) $(LDFLAGS) -lm

clean:
	rm -f sf2_to_ofsf

.PHONY: all clean
