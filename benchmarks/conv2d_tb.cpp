#include <stdint.h>
#include <stdio.h>

void conv2d(const int16_t input[16][16], const int16_t weights[3][3], int32_t output[14][14]);

int main() {
    int16_t input[16][16], weights[3][3];
    int32_t actual[14][14];
    uint32_t rng = 20260916u;
    int checked = 0;
    for (int test = 0; test < 20; ++test) {
        for (int i = 0; i < 16; ++i)
            for (int j = 0; j < 16; ++j) {
                rng = rng * 1664525u + 1013904223u;
                input[i][j] = test == 0 ? 0 : test == 1 ? 1000 : test == 2 ? ((i+j)%2 ? -1000 : 1000) : int(rng % 2001) - 1000;
            }
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j) {
                rng = rng * 1664525u + 1013904223u;
                weights[i][j] = test == 0 ? 0 : test == 1 ? -1000 : test == 2 ? (i == 1 && j == 1 ? 1 : 0) : int(rng % 2001) - 1000;
            }
        for (int y = 0; y < 14; ++y)
            for (int x = 0; x < 14; ++x) actual[y][x] = 0x12345678;
        conv2d(input, weights, actual);
        for (int pos = 0; pos < 196; ++pos) {
            const int y = pos / 14, x = pos % 14;
            int64_t expected = 0;
            for (int tap = 8; tap >= 0; --tap)
                expected += int64_t(input[y + tap/3][x + tap%3]) * weights[tap/3][tap%3];
            if (actual[y][x] != expected) {
                printf("FAIL test=%d pixel=%d\n", test, pos);
                return 1;
            }
            ++checked;
        }
    }
    printf("PASS cases=20 checks=%d seed=20260916\n", checked);
    return 0;
}
