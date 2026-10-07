#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <time.h>

void matmul_cpu(const int32_t *a, const int32_t *b, int32_t *c);

/* Same single-thread kernel; amortize only the Python/C boundary and timer.
 * Caller supplies count contiguous non-overlapping 32x32 int32 matrices.
 */
uint64_t matmul_cpu_batch_ns(const int32_t *a, const int32_t *b,
                             int32_t *c, uint32_t count) {
    struct timespec start, end;
    if (!count || count > 64) return UINT64_MAX;
    if (clock_gettime(CLOCK_MONOTONIC, &start)) return UINT64_MAX;
    for (uint32_t i = 0; i < count; ++i)
        matmul_cpu(a + i * 1024, b + i * 1024, c + i * 1024);
    if (clock_gettime(CLOCK_MONOTONIC, &end)) return UINT64_MAX;
    return (uint64_t)((int64_t)(end.tv_sec - start.tv_sec) * 1000000000
                     + end.tv_nsec - start.tv_nsec);
}
