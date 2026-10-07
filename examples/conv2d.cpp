#include <stdint.h>

#define WIDTH 16
#define KERNEL 3

// Valid 3x3 convolution: output dimensions are 14x14, no implicit padding.
void conv2d(const int16_t input[WIDTH][WIDTH],
            const int16_t weights[KERNEL][KERNEL],
            int32_t output[WIDTH - KERNEL + 1][WIDTH - KERNEL + 1]) {
OUTPUT_Y:
    for (int y = 0; y < WIDTH - KERNEL + 1; ++y) {
OUTPUT_X:
        for (int x = 0; x < WIDTH - KERNEL + 1; ++x) {
            int32_t sum = 0;
KERNEL_Y:
            for (int ky = 0; ky < KERNEL; ++ky) {
KERNEL_X:
                for (int kx = 0; kx < KERNEL; ++kx) {
                    sum += input[y + ky][x + kx] * weights[ky][kx];
                }
            }
            output[y][x] = sum;
        }
    }
}
