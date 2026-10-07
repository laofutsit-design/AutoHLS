#include <stdint.h>
#include <stdio.h>

void matmul_axi(const int32_t *a, const int32_t *b, int32_t *c, uint32_t batch_count);

int main() {
    static int32_t a[65536], b[65536], actual[65536];
    const uint32_t counts[] = {0, 1, 2, 3, 4, 16, 63, 64, 1, 65, 0xffffffffu};
    const int32_t sentinel = 0x12345678;
    uint32_t rng = 20260926u;
    int checked = 0;
    for (int test = 0; test < 11; ++test) {
        uint32_t count = counts[test];
        for (int n = 0; n < 65536; ++n) {
            int row = (n / 32) % 32, col = n % 32, matrix = n / 1024;
            rng = rng * 1664525u + 1013904223u;
            a[n] = test == 1 ? 0 : test == 2 ? (matrix % 2 ? -1000 : 1000) : int(rng % 2001) - 1000;
            rng = rng * 1664525u + 1013904223u;
            b[n] = test == 2 ? 1000 : test == 3 ? (row == col ? 1 : 0) : int(rng % 2001) - 1000;
            actual[n] = sentinel;
        }
        matmul_axi(a, b, actual, count);
        for (int n = 0; n < 65536; ++n) {
            int64_t expected = sentinel;
            if (count >= 1 && count <= 64 && uint32_t(n / 1024) < count) {
                expected = 0;
                int offset = (n / 1024) * 1024, row = (n / 32) % 32, col = n % 32;
                for (int k = 31; k >= 0; --k)
                    expected += int64_t(a[offset + row * 32 + k]) * b[offset + k * 32 + col];
            }
            if (actual[n] != expected) {
                printf("FAIL case=%d count=%u index=%d expected=%lld actual=%d\n",
                       test, count, n, (long long)expected, actual[n]);
                return 1;
            }
            ++checked;
        }
    }
#ifdef AUTOHLS_NATIVE_NULL_CHECKS
    // Only native sanitizers can check that invalid counts never dereference.
    matmul_axi(0, 0, 0, 0);
    matmul_axi(0, 0, 0, 65);
    matmul_axi(0, 0, 0, 0xffffffffu);
#endif
    printf("PASS cases=11 checks=%d seed=20260926\n", checked);
    return 0;
}
