/*
 * RBF to RBF_R Bit Reversal Tool
 * Converts Quartus RBF files to Analogue Pocket RBF_R format
 *
 * Based on Analogue's documentation:
 * Each byte of the file is bit-reversed (bits[7:0] to bits[0:7])
 */

#include <stdio.h>
#include <stdlib.h>

unsigned char reverse_byte(unsigned char b) {
    b = (b & 0xF0) >> 4 | (b & 0x0F) << 4;
    b = (b & 0xCC) >> 2 | (b & 0x33) << 2;
    b = (b & 0xAA) >> 1 | (b & 0x55) << 1;
    return b;
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <input.rbf> <output.rbf_r>\n", argv[0]);
        return 1;
    }

    FILE *input = fopen(argv[1], "rb");
    if (!input) {
        fprintf(stderr, "Error: Cannot open input file '%s'\n", argv[1]);
        return 1;
    }

    FILE *output = fopen(argv[2], "wb");
    if (!output) {
        fprintf(stderr, "Error: Cannot create output file '%s'\n", argv[2]);
        fclose(input);
        return 1;
    }

    int byte;
    while ((byte = fgetc(input)) != EOF) {
        unsigned char reversed = reverse_byte((unsigned char)byte);
        fputc(reversed, output);
    }

    fclose(input);
    fclose(output);

    printf("Successfully converted %s to %s\n", argv[1], argv[2]);
    return 0;
}
