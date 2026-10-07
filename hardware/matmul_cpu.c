#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <string.h>
#include <time.h>

/* Same DDR contract as matmul_axi: 32x32 int32, inputs in [-1000,1000].
 * i-k-j order gives contiguous inner-loop accesses for GCC vectorization.
 * Buffers must not overlap. This is a single-thread CPU baseline, not BLAS.
 */
void matmul_cpu(const int32_t *restrict a, const int32_t *restrict b,
                int32_t *restrict c) {
    memset(c, 0, 1024 * sizeof(*c));
    for (int i = 0; i < 32; ++i)
        for (int k = 0; k < 32; ++k) {
            const int32_t value = a[i * 32 + k];
            for (int j = 0; j < 32; ++j)
                c[i * 32 + j] += value * b[k * 32 + j];
        }
}

uint64_t matmul_cpu_ns(const int32_t *a, const int32_t *b, int32_t *c) {
    struct timespec start, end;
    if (clock_gettime(CLOCK_MONOTONIC, &start)) return UINT64_MAX;
    matmul_cpu(a, b, c);
    if (clock_gettime(CLOCK_MONOTONIC, &end)) return UINT64_MAX;
    return (uint64_t)((int64_t)(end.tv_sec - start.tv_sec) * 1000000000
                     + end.tv_nsec - start.tv_nsec);
}
