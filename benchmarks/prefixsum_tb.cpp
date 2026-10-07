#include <stdint.h>
#include <stdio.h>

void prefixsum(const int16_t input[256], int32_t output[256]);

int main() {
    struct { int16_t before; int16_t data[256]; int16_t after; } input;
    struct { int32_t before; int32_t data[256]; int32_t after; } output;
    int16_t original[256];
    uint32_t rng = 20260927u;
    int checked = 0;
    for (int test = 0; test < 32; ++test) {
        input.before = 12345;
        input.after = -12345;
        output.before = 0x12345678;
        output.after = -0x12345678;
        for (int i = 0; i < 256; ++i) {
            rng = rng * 1664525u + 1013904223u;
            int value = int(rng % 2001) - 1000;
            if (test == 0) value = 0;
            if (test == 1) value = 1000;
            if (test == 2) value = -1000;
            if (test == 3) value = i % 2 ? -1000 : 1000;
            if (test == 4) value = i == 0 ? 1000 : 0;
            if (test == 5) value = i == 255 ? -1000 : 0;
            if (test == 6) value = i - 128;
            if (test == 7) value = i < 128 ? 1000 : -1000;
            input.data[i] = original[i] = int16_t(value);
            output.data[i] = 0x12345678;
        }
        prefixsum(input.data, output.data);
        if (input.before != 12345 || input.after != -12345 ||
            output.before != 0x12345678 || output.after != -0x12345678) {
            printf("FAIL test=%d guard\n", test);
            return 1;
        }
        checked += 4;
        for (int i = 0; i < 256; ++i) {
            // Recompute each prefix backwards in int64, independently of the scan.
            int64_t expected = 0;
            for (int j = i; j >= 0; --j) expected += original[j];
            if (output.data[i] != expected) {
                printf("FAIL test=%d output=%d\n", test, i);
                return 1;
            }
            if (input.data[i] != original[i]) {
                printf("FAIL test=%d input=%d\n", test, i);
                return 1;
            }
            checked += 2;
        }
    }
    printf("PASS cases=32 checks=%d seed=20260927\n", checked);
    return 0;
}
