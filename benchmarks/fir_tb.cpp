#include <stdint.h>
#include <stdio.h>

void fir(const int16_t input[256], const int16_t coeff[32], int32_t output[256]);

int main() {
    int16_t input[256], coeff[32];
    int32_t actual[256];
    uint32_t rng = 20260916u;
    int checked = 0;
    for (int test = 0; test < 20; ++test) {
        for (int i = 0; i < 256; ++i) {
            rng = rng * 1664525u + 1013904223u;
            input[i] = test == 0 ? 0 : test == 1 ? 1000 : test == 2 ? (i == 0 ? 1000 : 0) : int(rng % 2001) - 1000;
            actual[i] = 0x12345678;
        }
        for (int k = 0; k < 32; ++k) {
            rng = rng * 1664525u + 1013904223u;
            coeff[k] = test == 0 ? 0 : test == 1 ? -1000 : int(rng % 2001) - 1000;
        }
        fir(input, coeff, actual);
        // Scatter convolution avoids copying the kernel's gather loop.
        int64_t expected[256] = {};
        for (int i = 0; i < 256; ++i)
            for (int k = 0; k < 32 && i + k < 256; ++k)
                expected[i + k] += int64_t(input[i]) * coeff[k];
        for (int i = 0; i < 256; ++i) {
            if (actual[i] != expected[i]) {
                printf("FAIL test=%d sample=%d\n", test, i);
                return 1;
            }
            ++checked;
        }
    }
    printf("PASS cases=20 checks=%d seed=20260916\n", checked);
    return 0;
}
