/*
 * Bounded, single-threaded Pixman composition microbenchmark.
 *
 * Each timed iteration restores a deterministic XRGB destination and composites
 * premultiplied ARGB over it. The rate includes that memcpy, so this measures a
 * repeatable reset-and-composite workload, not isolated compositor frame rate.
 * The final RGB checksum is independent of the number of timed iterations.
 *
 * Build on the ARM64 Linux guest with its installed libpixman:
 *   gcc -O2 -std=c11 -Wall -Wextra -Werror pixman-composite-benchmark.c \
 *     $(pkg-config --cflags --libs pixman-1) -o pixman-composite-benchmark
 * Compare separate processes with and without PIXMAN_DISABLE=arm-neon.
 */
#define _POSIX_C_SOURCE 200809L

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <pixman.h>

enum { WIDTH = 1920, HEIGHT = 1080, STRIDE = WIDTH * 4 };
static const uint64_t target_nanoseconds = UINT64_C(1000000000);

static int monotonic_nanoseconds(uint64_t *result)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        perror("clock_gettime");
        return 0;
    }
    *result = (uint64_t)now.tv_sec * UINT64_C(1000000000)
            + (uint64_t)now.tv_nsec;
    return 1;
}

/* FNV-1a over the red, green, then blue bytes of each row-major pixel. */
static uint64_t checksum_rgb(const uint32_t *pixels, size_t count)
{
    uint64_t hash = UINT64_C(14695981039346656037);
    for (size_t i = 0; i < count; ++i) {
        for (int shift = 16; shift >= 0; shift -= 8) {
            hash ^= (pixels[i] >> shift) & UINT32_C(255);
            hash *= UINT64_C(1099511628211);
        }
    }
    return hash;
}

static void initialize_pixels(uint32_t *source, uint32_t *initial)
{
    for (uint32_t y = 0; y < HEIGHT; ++y) {
        for (uint32_t x = 0; x < WIDTH; ++x) {
            size_t i = (size_t)y * WIDTH + x;
            uint32_t alpha = 1 + (x * 13 + y * 17) % 254;
            uint32_t red = ((x * 3 + y * 5) & 255) * alpha / 255;
            uint32_t green = ((x * 7 + y * 11) & 255) * alpha / 255;
            uint32_t blue = ((x * 19 + y * 23) & 255) * alpha / 255;
            source[i] = (alpha << 24) | (red << 16) | (green << 8) | blue;
            initial[i] = UINT32_C(0xff000000)
                       | (((x * 29 + y * 31) & 255) << 16)
                       | (((x * 37 + y * 41) & 255) << 8)
                       | ((x * 43 + y * 47) & 255);
        }
    }
}

static void composite(pixman_image_t *source_image,
                      pixman_image_t *destination_image,
                      uint32_t *destination,
                      const uint32_t *initial,
                      size_t bytes)
{
    memcpy(destination, initial, bytes);
    pixman_image_composite32(PIXMAN_OP_OVER, source_image, NULL,
                            destination_image, 0, 0, 0, 0, 0, 0,
                            WIDTH, HEIGHT);
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1) {
        fputs("This benchmark takes no arguments.\n", stderr);
        return 2;
    }

    /* PIXMAN_DISABLE diagnostics use stdout. Preserve stdout for JSON only. */
    int output_fd = dup(STDOUT_FILENO);
    if (output_fd < 0) {
        perror("dup stdout");
        return 1;
    }
    FILE *json_output = fdopen(output_fd, "w");
    if (!json_output) {
        perror("fdopen stdout");
        close(output_fd);
        return 1;
    }
    if (dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
        perror("redirect Pixman diagnostics");
        fclose(json_output);
        return 1;
    }

    const size_t count = (size_t)WIDTH * HEIGHT;
    const size_t bytes = count * sizeof(uint32_t);
    uint32_t *source = malloc(bytes);
    uint32_t *initial = malloc(bytes);
    uint32_t *destination = malloc(bytes);
    pixman_image_t *source_image = NULL;
    pixman_image_t *destination_image = NULL;
    int status = 1;

    if (!source || !initial || !destination) {
        fputs("Unable to allocate benchmark buffers.\n", stderr);
        goto cleanup;
    }
    initialize_pixels(source, initial);
    source_image = pixman_image_create_bits(PIXMAN_a8r8g8b8, WIDTH, HEIGHT,
                                            source, STRIDE);
    destination_image = pixman_image_create_bits(PIXMAN_x8r8g8b8, WIDTH, HEIGHT,
                                                 destination, STRIDE);
    if (!source_image || !destination_image) {
        fputs("Unable to create Pixman images.\n", stderr);
        goto cleanup;
    }

    /* Resolve lazy dispatch and fault in pages before the timed interval. */
    composite(source_image, destination_image, destination, initial, bytes);

    uint64_t start, finish, iterations = 0;
    if (!monotonic_nanoseconds(&start))
        goto cleanup;
    do {
        composite(source_image, destination_image, destination, initial, bytes);
        ++iterations;
        if (!monotonic_nanoseconds(&finish))
            goto cleanup;
    } while (finish - start < target_nanoseconds);

    const double elapsed = (double)(finish - start) / 1000000000.0;
    const double pixels_per_second = (double)count * (double)iterations / elapsed;
    const uint64_t checksum = checksum_rgb(destination, count);
    fprintf(json_output, "{\"benchmark\":\"pixman-reset-over\","
           "\"width\":%d,\"height\":%d,"
           "\"iterations\":%" PRIu64 ",\"elapsed_seconds\":%.9f,"
           "\"pixels_per_second\":%.3f,"
           "\"checksum_rgb_fnv1a64\":\"%016" PRIx64 "\","
           "\"pixman_version\":\"%s\"}\n",
           WIDTH, HEIGHT, iterations, elapsed, pixels_per_second, checksum,
           pixman_version_string());
    status = ferror(json_output) ? 1 : 0;

cleanup:
    if (destination_image)
        pixman_image_unref(destination_image);
    if (source_image)
        pixman_image_unref(source_image);
    free(destination);
    free(initial);
    free(source);
    fflush(stdout);
    if (fclose(json_output) != 0)
        status = 1;
    return status;
}
