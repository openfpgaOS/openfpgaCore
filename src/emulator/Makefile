# Emulator Test Build
CC = gcc
CFLAGS = -Wall -Wextra -O2 -std=c11

all: test_save

test_save: test_save.c
	$(CC) $(CFLAGS) -o $@ $<

test: test_save
	./test_save

clean:
	rm -f test_save

.PHONY: all test clean
