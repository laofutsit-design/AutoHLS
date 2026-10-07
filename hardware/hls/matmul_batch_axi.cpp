#include <stdint.h>

void matmul(const int16_t a[32][32], const int16_t b[32][32], int32_t c[32][32]);

// Separate IP build: one start processes 1..64 contiguous 32x32 matrices.
// Same signed [-1000,1000] inputs, 32-bit DDR words and compute kernel as before.
void matmul_axi(const int32_t *a, const int32_t *b, int32_t *c, uint32_t batch_count) {
#pragma HLS INTERFACE m_axi port=a offset=slave bundle=gmem depth=65536
#pragma HLS INTERFACE m_axi port=b offset=slave bundle=gmem depth=65536
#pragma HLS INTERFACE m_axi port=c offset=slave bundle=gmem depth=65536
#pragma HLS INTERFACE s_axilite port=a bundle=control
#pragma HLS INTERFACE s_axilite port=b bundle=control
#pragma HLS INTERFACE s_axilite port=c bundle=control
#pragma HLS INTERFACE s_axilite port=batch_count bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if (batch_count == 0 || batch_count > 64) return;
    int16_t local_a[32][32], local_b[32][32];
    int32_t local_c[32][32];
BATCH:
    for (uint32_t matrix = 0; matrix < batch_count; ++matrix) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=64
        const uint32_t offset = matrix * 1024;
        for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
            local_a[i / 32][i % 32] = a[offset + i];
        }
        for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
            local_b[i / 32][i % 32] = b[offset + i];
        }
        matmul(local_a, local_b, local_c);
        for (int i = 0; i < 1024; ++i) {
#pragma HLS PIPELINE II=1
            c[offset + i] = local_c[i / 32][i % 32];
        }
    }
}
