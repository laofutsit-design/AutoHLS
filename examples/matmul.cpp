#include <stdint.h>

#define N 32

void matmul(const int16_t a[N][N],
            const int16_t b[N][N],
            int32_t c[N][N]) {
ROW:
    for (int i = 0; i < N; ++i) {
COL:
        for (int j = 0; j < N; ++j) {
            int32_t sum = 0;
DOT:
            for (int k = 0; k < N; ++k) {
                sum += a[i][k] * b[k][j];
            }
            c[i][j] = sum;
        }
    }
}
