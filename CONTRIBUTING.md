# Contributing

Focused fixes, reproducible bug reports, documentation improvements, and careful
measurements are welcome. This is an experimental ARM64/UTM project. Read
[the build guide](docs/BUILDING.md), [validation limits](VALIDATION.md), and
[security policy](SECURITY.md) before changing guest behavior.

## Tests

Use Python 3.9 or newer, Bash, and GNU coreutils. No Python packages, VM images,
credentials, root access, or running hypervisor are required for the unit suite.

On Linux:

```sh
python3 -m unittest discover -s tests -v
```

On Apple Silicon macOS, install Homebrew `coreutils` and then run:

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

The shell fixtures look for coreutils at
`/opt/homebrew/opt/coreutils/libexec/gnubin`. For other installations, put the GNU
tools on `PATH`. Host runtime helpers are supported on Apple Silicon macOS;
Linux unit-test success does not imply that Linux can run those helpers.

The suite uses temporary files and mocked system commands. The included
[Linux CI template](ci/fixture-tests.yml) additionally
checks Python and shell syntax. It does not build images, boot VMs, verify Tor
routing, or certify anonymity. Runtime testing is a separate, explicitly
prepared procedure; never use production guest disks as test fixtures.

## Proposing a change

1. Create a branch with one concrete purpose. Describe the trigger, expected
   behavior, and effect of the change in the pull request.
2. Run the relevant checks. Add a regression test when it protects meaningful
   behavior; avoid tests that merely repeat the implementation.
3. Update documentation if inputs, platform assumptions, security boundaries,
   or validation limits change. Keep build source revisions and third-party
   notices accurate.
4. Record what was actually tested, including architecture, relevant versions,
   and whether results came from a fixture, headless guest, or UTM interaction.

Keep the Workstation's sole internal NIC, browser verification and sandbox,
display privacy policy, and disabled sharing intact. Changes to those boundaries
need a clear rationale and validation. Do not resolve failures by disabling
signature checks or replacing missing dependencies with dummy packages.

Benchmark changes need a fresh source manifest. Preserve historical results
and their measured source hashes; label new results separately. Report raw
sample counts, host load, and uncertainty, and distinguish component benchmarks
from application or desktop performance.

## What to include in reports

Include the affected commit, macOS/UTM/QEMU or guest version as appropriate,
the command, expected and actual result, and a small sanitized error excerpt.
Explain whether a new build or an existing bundle is involved.

Do not upload VM disks, browser profiles, cookies, SSH keys, seed images, full
serial logs, personal paths, or unrelated screenshots. Review even short logs
before sharing. See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License and conduct

Contributions to original files are accepted under GPL-3.0-or-later. Changes to
upstream-derived files must retain the applicable terms in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Identify copied/adapted material,
its source revision, and its license; do not add code you cannot distribute.

Be respectful, discuss technical claims with evidence, and avoid harassment or
sharing personal information. Maintainers may close abusive or unrelated
threads. No review turnaround or supported-release schedule is promised.
