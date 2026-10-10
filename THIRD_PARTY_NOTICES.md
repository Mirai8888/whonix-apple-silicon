# License scope and third-party notices

## Original project work

Copyright (C) 2026 whonix-apple-silicon contributors.

Except for the specifically identified files below and the license documents
themselves, this project's original source code, configuration, documentation,
tests, benchmark page, and authored benchmark reference image are licensed under
**GNU GPL version 3 or, at your option, any later version**
(`GPL-3.0-or-later`).

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>. The complete version 3
text is included in [LICENSE](LICENSE).

## Path-to-license mapping

This sidecar mapping records licenses without altering patch context or the
source files whose hashes are preserved in benchmark evidence. More specific
entries take precedence over the original-work default above.

| Path | SPDX license identifier | Origin |
| --- | --- | --- |
| `patches/adduser-uid-defaults.patch` | `AGPL-3.0-or-later` | Changes to derivative-maker, including upstream context |
| `patches/bootstrap-helper-scripts.patch` | `AGPL-3.0-or-later` | Changes to derivative-maker, including upstream context |
| `patches/dependency-error-nounset.patch` | `AGPL-3.0-or-later` | Changes to derivative-maker, including upstream context |
| `review/derivative-maker.patch` | `AGPL-3.0-or-later` | Combined representation of the three derivative-maker patches above |
| `patches/grml-no-reimage.patch` | `GPL-2.0-or-later` | grml-debootstrap backport and adaptation |
| `tests/test_prepare_utm.py` | `AGPL-3.0-or-later` | Adapted libvirt-dist UTM configuration fixtures; this mapping conservatively covers the entire file |
| Other original project files | `GPL-3.0-or-later` | Original project work as described above |

The project's modifications in the specifically identified upstream-derived
files are provided under the respective licenses in this table. Their upstream
copyright notices and license terms remain in effect. The license texts retain
their own Free Software Foundation copyright and verbatim-copy notices.

## derivative-maker patches

The three original AGPL patch files and their combined review diff modify
`build-steps.d/3500_install-packages` from
[Whonix/derivative-maker at `945b966e5fbc3b4ca58cc0eece7b91211190c6a4`](https://github.com/Whonix/derivative-maker/tree/945b966e5fbc3b4ca58cc0eece7b91211190c6a4).
The applicable stanza from that revision's
[COPYING](https://github.com/Whonix/derivative-maker/blob/945b966e5fbc3b4ca58cc0eece7b91211190c6a4/COPYING)
is reproduced verbatim:

```text
Files: *
Copyright: 2012 - 2025 ENCRYPTED SUPPORT LLC <adrelanos@whonix.org>
License: AGPL-3+
```

`AGPL-3+` denotes GNU Affero GPL version 3 or any later version. Its full text is
included in [LICENSES/AGPL-3.0-or-later.txt](LICENSES/AGPL-3.0-or-later.txt).

Local changes dated 2026-09-17 install the helper scripts needed during
bootstrapping, supply missing adduser UID defaults, and safely read an unset
dependency-tracking entry. The files are patches, not complete copies of the
upstream build system.

## grml-debootstrap backport

`patches/grml-no-reimage.patch` targets
[Kicksecure/grml-debootstrap at `5c73a609120ac2c77fee7c2e57ea09d774269de6`](https://github.com/Kicksecure/grml-debootstrap/tree/5c73a609120ac2c77fee7c2e57ea09d774269de6).
It backports the `--no-reimage` option from Aaron Rainbolt's 2026 commits
[`b62f8e5c95972d4d77d4576d24753ca0f81ec594`](https://github.com/Kicksecure/grml-debootstrap/commit/b62f8e5c95972d4d77d4576d24753ca0f81ec594)
and
[`f10ef1536ecbaccff4072a8fe5dbbdd98a17cae0`](https://github.com/Kicksecure/grml-debootstrap/commit/f10ef1536ecbaccff4072a8fe5dbbdd98a17cae0),
with a local 2026-09-17 adaptation to the pinned revision's braced shell-variable
style. The upstream author is credited as Aaron Rainbolt
`<arraybolt3@gmail.com>` in those commits.

The applicable copyright and grant from the pinned revision's
[debian/copyright](https://github.com/Kicksecure/grml-debootstrap/blob/5c73a609120ac2c77fee7c2e57ea09d774269de6/debian/copyright)
are reproduced verbatim:

```text
Files: *
Copyright: 2006-2023, Michael Prokop <mika@grml.org>
           2014, 2015, Sebastian Pipping <sebastian@pipping.org>
           2014, Patrick Schleizer <adrelanos@riseup.net>
           2013, 2014, Markus Rekkenbeil <mre@mobi4friends.de>
           2012, 2013, Evgeni Golov <evgeni@grml.org>
           2012, Darshaka Pathirana <dpat@syn-net.org>
           2010, 2011, 2013, 2014, Christian Hofstaedtler <christian@hofstaedtler.name>
           2009, 2010, 2011, Ulrich Dangel <mru@grml.org>
           2009, Michael Gebetsroither <michael@mgeb.org>
           2008, 2010, Tong Sun <suntong@cpan.org>
           2007, Alexander Wirt <formorer@grml.org>
License: GPL-2+
 This package is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.
 .
 This package is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 On Debian systems, the complete text of the GNU General Public License,
 version 2 can be found in `/usr/share/common-licenses/GPL-2'.
```

The complete version 2 text is also included here in
[LICENSES/GPL-2.0-or-later.txt](LICENSES/GPL-2.0-or-later.txt).

## UTM configuration test fixtures

The `upstream_config` fixture in `tests/test_prepare_utm.py` adapts the
[Whonix-Gateway](https://github.com/Kicksecure/libvirt-dist/blob/b5754e099d668447abd743d419b94ff921b35027/usr/share/utm/Whonix-Gateway.plist.template)
and
[Whonix-Workstation](https://github.com/Kicksecure/libvirt-dist/blob/b5754e099d668447abd743d419b94ff921b35027/usr/share/utm/Whonix-Workstation.plist.template)
UTM templates from Kicksecure/libvirt-dist at
`b5754e099d668447abd743d419b94ff921b35027`. The 2026-09-17 adaptation reduces
them to in-memory regression-test fixtures. The entire test file, including the
project's tests around those fixtures, is provided under `AGPL-3.0-or-later`.

The applicable stanza from that revision's
[COPYING](https://github.com/Kicksecure/libvirt-dist/blob/b5754e099d668447abd743d419b94ff921b35027/COPYING)
is reproduced verbatim:

```text
Files: *
Copyright: 2012 - 2025 ENCRYPTED SUPPORT LLC <adrelanos@whonix.org>
License: AGPL-3+
```

The complete license text is in
[LICENSES/AGPL-3.0-or-later.txt](LICENSES/AGPL-3.0-or-later.txt).

## External software and names

Whonix, Kicksecure, derivative-maker, grml-debootstrap, Debian, Tor Browser,
UTM, QEMU, Apple frameworks, and other external dependencies retain their own
licenses, copyrights, and trademarks. This source repository does not distribute
their binaries, VM images, browser profiles, or complete source checkouts, and
does not relicense them. A generated VM image contains many separately licensed
components; distributing one requires retaining the applicable notices and
satisfying those components' source-distribution requirements.

This is an independent, experimental community project. Mentioning an upstream
project, product, or company identifies compatibility or provenance and does not
imply affiliation, sponsorship, or endorsement.

## License-text provenance

- [LICENSE](LICENSE) is the full GNU GPL v3 text, copied verbatim from the local
  live-build `COPYING` and verified byte-for-byte against GNU GCC's
  [COPYING3](https://github.com/gcc-mirror/gcc/blob/master/COPYING3).
- [LICENSES/GPL-2.0-or-later.txt](LICENSES/GPL-2.0-or-later.txt) is the full GNU
  GPL v2 text from GNU GCC's
  [COPYING](https://github.com/gcc-mirror/gcc/blob/master/COPYING).
- [LICENSES/AGPL-3.0-or-later.txt](LICENSES/AGPL-3.0-or-later.txt) is the full GNU
  AGPL v3 text from the pinned libvirt-dist `COPYING` above, with Debian copyright
  paragraph formatting decoded. Every word was checked against the SPDX
  [AGPL-3.0-or-later license text](https://github.com/spdx/license-list-data/blob/main/text/AGPL-3.0-or-later.txt).

The GNU canonical references are [GPL v3](https://www.gnu.org/licenses/gpl-3.0.html),
[GPL v2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html), and
[AGPL v3](https://www.gnu.org/licenses/agpl-3.0.html). The “or later” grants are
specified in the scope and upstream notices above; the unmodified license texts
describe the numbered versions themselves.
