#include <stdint.h>

#define TAPS 32
#define SIZE 256

void fir(const int16_t input[SIZE],
         const int16_t coeff[TAPS],
         int32_t output[SIZE]) {
SAMPLE:
    for (int n = 0; n < SIZE; ++n) {
        int32_t acc = 0;
TAP:
        for (int k = 0; k < TAPS; ++k) {
            if (n >= k) {
                acc += input[n - k] * coeff[k];
            }
        }
        output[n] = acc;
    }
}
