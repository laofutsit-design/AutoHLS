#include <stdint.h>
#include <stdio.h>

void matmul_axi(const int32_t *a, const int32_t *b, int32_t *c);

int main() {
    int32_t a[1024], b[1024];
    int32_t actual[1024];
    uint32_t rng = 20260916u;
    int checked = 0;
    for (int test = 0; test < 20; ++test) {
        for (int i = 0; i < 32; ++i) {
            for (int j = 0; j < 32; ++j) {
                int n = i * 32 + j;
                rng = rng * 1664525u + 1013904223u;
                a[n] = test == 0 ? 0 : test == 1 ? 1000 : test == 2 ? ((i+j)%2 ? -1000 : 1000) : int(rng % 2001) - 1000;
                rng = rng * 1664525u + 1013904223u;
                b[n] = test == 0 ? 0 : test == 1 ? -1000 : test == 2 ? (i == j ? 1 : 0) : int(rng % 2001) - 1000;
                actual[n] = 0x12345678;
            }
        }
        matmul_axi(a, b, actual);
        for (int j = 0; j < 32; ++j) {
            for (int i = 0; i < 32; ++i) {
                int64_t expected = 0;
                for (int k = 31; k >= 0; --k) expected += int64_t(a[i * 32 + k]) * b[k * 32 + j];
                if (actual[i * 32 + j] != expected) {
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
