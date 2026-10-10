# Source publication notes

## Public source preview — 2026-10-10

This repository publishes a clean source snapshot of the experimental
**v0.1.0-alpha.1** developer preview. Earlier development history and pull
requests are retained privately. The public Git history starts with this
snapshot and uses a project contributor identity.

The snapshot contains source, patches, tests, license notices, build provenance,
and compact historical benchmark evidence. It excludes VM images, packages,
SSH credentials, builder state, browser profiles, raw local logs, and unfinished
storage benchmark work. Setup still requires the manual steps and external
inputs in [BUILDING.md](BUILDING.md).

Before publication, the source inventory was reviewed for personal account and
host identifiers, private contact details, credential patterns, and unexpected
binaries. The sole image is the synthetic browser benchmark reference; it
contains no text or EXIF metadata. Public upstream copyright and attribution
notices are retained in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The prepared candidate passed **163 fixture tests on macOS ARM64 and Ubuntu
24.04 ARM64** on 2026-10-06. These validate source tooling, not VM runtime or
anonymity. The publication credential cannot upload active Actions workflows, so the
[Linux fixture-test workflow](../ci/fixture-tests.yml) is distributed as a
template. Hosted Actions are not configured in this preview. A maintainer with
workflow permission can install the template as `.github/workflows/ci.yml`.
Do not infer a passing hosted CI run from source availability. Runtime observations remain dated to 2026-09-17
on M5; M1–M4 and post-fix interactive behavior remain unverified.

## Reproduce source assets

Use a clean checkout of the exact selected public commit, Python 3.9+, and Git.
The committed paths must match [release/source-files.txt](../release/source-files.txt).
Packaging rejects pending tracked changes, symlinks, submodules, unsafe names,
and files larger than 2 MiB. Untracked files are never included. Review file
contents before expanding this inventory; the inventory is not a credential
detector.

```sh
python3 scripts/prepare-source-release.py \
  --version v0.1.0-alpha.1 --ref HEAD \
  --output dist/v0.1.0-alpha.1
cd dist/v0.1.0-alpha.1
shasum -a 256 -c SHA256SUMS
tar -xzf whonix-apple-silicon-v0.1.0-alpha.1-source.tar.gz
cd whonix-apple-silicon-v0.1.0-alpha.1
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
```

Only the source archive, JSON manifest, and `SHA256SUMS` are intended as source
release assets. Archive timestamps and ownership are normalized; no Git
history is bundled. Checksums detect changes but do not authenticate the
publisher. This preview is unsigned.

## Future publication changes

1. Review all new source, Git metadata, asset contents, licenses, and public
   claims before publishing. Keep credentials, personal host paths, guest
   state, and browser state out of the repository and release assets.
2. Run the exact source archive's fixture tests on Linux and macOS and check
   Python/shell syntax, documentation links, inventory, and checksums. Obtain
   hosted CI evidence when GitHub Actions becomes available.
3. Keep announcements explicit: this is an independent, experimental,
   source-only developer preview with no ready-to-run VM downloads or
   one-command image build. Keep security reporting aligned with
   [SECURITY.md](../SECURITY.md).
4. If creating a release, tag the validated public commit, attach only the
   three reviewed source assets, use the reviewed
   [release notes](../release/NOTES-v0.1.0-alpha.1.md), and mark it as an alpha
   prerelease rather than the latest stable release. Expand relative release
   body links to the corresponding tagged files.

Publishing VM images or packages requires a separate review of guest state,
source availability and license obligations for bundled components, image
authentication, update behavior, and runtime acceptance. Existing local images
and browser profiles are not publication artifacts.
