#include <stdint.h>

void matmul(const int16_t a[32][32], const int16_t b[32][32], int32_t c[32][32]);

// Fixed benchmark contract: row-major 32x32, signed inputs in [-1000, 1000].
// DDR buffers use uniform 32-bit words so a shared AXI master can burst.
void matmul_axi(const int32_t *a, const int32_t *b, int32_t *c) {
#pragma HLS INTERFACE m_axi port=a offset=slave bundle=gmem depth=1024
#pragma HLS INTERFACE m_axi port=b offset=slave bundle=gmem depth=1024
#pragma HLS INTERFACE m_axi port=c offset=slave bundle=gmem depth=1024
#pragma HLS INTERFACE s_axilite port=a bundle=control
#pragma HLS INTERFACE s_axilite port=b bundle=control
#pragma HLS INTERFACE s_axilite port=c bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    int16_t local_a[32][32], local_b[32][32];
    int32_t local_c[32][32];
    for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
        local_a[i / 32][i % 32] = a[i];
    }
    for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
        local_b[i / 32][i % 32] = b[i];
    }
    matmul(local_a, local_b, local_c);
    for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
        c[i] = local_c[i / 32][i % 32];
    }
}
