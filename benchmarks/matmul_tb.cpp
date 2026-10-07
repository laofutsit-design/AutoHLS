#include <stdint.h>
#include <stdio.h>

void matmul(const int16_t a[32][32], const int16_t b[32][32], int32_t c[32][32]);

int main() {
    int16_t a[32][32], b[32][32];
    int32_t actual[32][32];
    uint32_t rng = 20260916u;
    int checked = 0;
    for (int test = 0; test < 20; ++test) {
        for (int i = 0; i < 32; ++i) {
            for (int j = 0; j < 32; ++j) {
                rng = rng * 1664525u + 1013904223u;
                a[i][j] = test == 0 ? 0 : test == 1 ? 1000 : test == 2 ? ((i+j)%2 ? -1000 : 1000) : int(rng % 2001) - 1000;
                rng = rng * 1664525u + 1013904223u;
                b[i][j] = test == 0 ? 0 : test == 1 ? -1000 : test == 2 ? (i == j ? 1 : 0) : int(rng % 2001) - 1000;
                actual[i][j] = 0x12345678;
            }
        }
        matmul(a, b, actual);
        // Independent reference uses 64-bit arithmetic and a different traversal.
        for (int j = 0; j < 32; ++j) {
            for (int i = 0; i < 32; ++i) {
                int64_t expected = 0;
                for (int k = 31; k >= 0; --k) expected += int64_t(a[i][k]) * b[k][j];
                if (actual[i][j] != expected) {
                    printf("FAIL test=%d row=%d col=%d\n", test, i, j);
                    return 1;
                }
                ++checked;
            }
        }
    }
    printf("PASS cases=20 checks=%d seed=20260916\n", checked);
    return 0;
}
