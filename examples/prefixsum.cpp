#include <stdint.h>

#define SIZE 256

void prefixsum(const int16_t input[SIZE], int32_t output[SIZE]) {
    int32_t acc = 0;
SCAN:
    for (int i = 0; i < SIZE; ++i) {
        acc += input[i];
        output[i] = acc;
    }
}
