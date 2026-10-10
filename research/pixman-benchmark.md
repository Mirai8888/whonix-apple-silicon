# ARM64 Pixman composition benchmark

Completed repeated Workstation measurements appear in the
[performance report](../PERFORMANCE.md). The builder numbers below are smoke
checks only and are not pooled into those results.

The [C benchmark](../scripts/pixman-composite-benchmark.c) exercises the installed
Linux `libpixman-1` through `pixman_image_composite32`: `PIXMAN_OP_OVER`, a
1920×1080 premultiplied ARGB8888 source, and an XRGB8888 destination. Inputs vary
deterministically across pixels, with source alpha from 1 through 254. It takes
no arguments and runs one warm-up, then complete iterations until at least one
second has elapsed on `CLOCK_MONOTONIC`.

Each timed iteration first restores the same destination using `memcpy`. The
reported pixels/second therefore includes reset plus composition and clock
sampling; it is not isolated composition throughput or desktop frame rate.
Initialization, warm-up, and checksum calculation are outside the timed region.
The live external Pixman calls and final checksum prevent the compiler from
discarding the compositing work.

The result is one JSON object on stdout. Pixman's own `PIXMAN_DISABLE`
diagnostic is redirected to stderr. `checksum_rgb_fnv1a64` hashes the final
row-major RGB bytes in red/green/blue order and excludes XRGB's unused byte.
Resetting each iteration makes the checksum independent of iteration count.

Build on the ARM64 Debian maintenance VM with its installed system library:

```sh
cd /home/user/arm64-bench/pixman
nice -n 19 gcc -O2 -std=c11 -Wall -Wextra -Werror \
  pixman-composite-benchmark.c \
  $(pkg-config --cflags --libs pixman-1) -o pixman-composite-benchmark
```

Run separate processes so Pixman's once-per-process implementation selection
sees each setting:

```sh
env -u PIXMAN_DISABLE ./pixman-composite-benchmark > default.json
env PIXMAN_DISABLE=arm-neon ./pixman-composite-benchmark > without-arm-neon.json
```

The second case disables Pixman's dedicated ARM NEON implementation. It is not
a pure-scalar baseline: compiler-generated SIMD and other library code may
still use vector instructions. Neither case changes desktop settings or the
installed library. This small workload can establish whether the existing
optimized path benefits this operation; it cannot establish browser or complete
desktop responsiveness, native performance parity, or hardware GPU rendering.

For a comparison, use repeated alternating runs in the same VM configuration
after idle settles, retain both raw JSON results and stderr, verify identical
checksums, and report medians and the execution context. Maintenance-builder
smoke checks only establish that the binary works; they are not Workstation
performance results.

## Build and smoke verification, 2026-09-17

Built on the Debian 13 ARM64 maintenance builder using GCC
`14.2.0-19` (`gcc` metapackage `4:14.2.0-1`) and `pkgconf 1.8.1-4`.
The builder lacked Pixman, so its only newly installed packages were the signed
Debian trixie `libpixman-1-0:arm64` and `libpixman-1-dev:arm64`, both
`0.44.0-3`. Compilation and smoke runs used `nice -n 19`, sequentially.

The binary (local-only: `artifacts/local-benchmarks/pixman-composite-benchmark`) is an
AArch64 ELF PIE linked to the installed library; no copied or patched Pixman is
embedded. Its direct ELF dependencies are `libpixman-1.so.0` and `libc.so.6`.
`ldd` also resolves Pixman's `libm.so.6` dependency and the
`/lib/ld-linux-aarch64.so.1` loader. The builder's glibc package is
`libc6:arm64 2.41-12+deb13u4`.

| Builder smoke process | Iterations | Elapsed seconds | Pixels/second |
| --- | ---: | ---: | ---: |
| `PIXMAN_DISABLE` unset | 1311 | 1.000814737 | 2,716,276,548.993 |
| `PIXMAN_DISABLE=arm-neon` | 169 | 1.001755457 | 349,824,298.486 |

These are single smoke samples, not a controlled performance comparison.
Both results parse as one JSON object, report Pixman `0.44.0`, and give checksum
`457c95b0871b8af3`. A separate integer reference computed every output channel
as `floor(source_channel * alpha / 255) + round(destination_channel *
(255 - alpha) / 255)` and produced the same checksum. The disabled case emitted
`pixman: Disabled arm-neon implementation` on stderr only. Passing an unexpected
argument returned status 2 with empty stdout, as intended.

SHA-256:

```text
50b87a6595d78b10accf75e42cdd1ed28c52c2bec088279013a78cf9cec96d74  scripts/pixman-composite-benchmark.c
e0cdd0c6a193dafaf070e580ef6bbb0cb4ec5b1ba4a5753287c9d770af13eef2  artifacts/local-benchmarks/pixman-composite-benchmark
```
