# Optional Abla Xtensa LLVM companion

This is a private `opt` + `llc` + `libLLVM` cohort for Xtensa. It is **not** an
Abla compiler, Clang distribution, general host LLVM replacement, or firmware
linker. ESP firmware still needs its SDK and GNU linker. No Abla build downloads
this companion automatically.

The manual `abla-xtensa-companion.yml` workflow builds only when a maintainer
chooses to dispatch it, after the LLVM source or the Abla patches change. It is
not called by ordinary CI or Abla release workflows. The checked-in config pins
both the fork commit and its source-archive SHA-256. Each package carries that
identity and SHA-256 hashes of every file in `manifest.json`.

The helpers register only the Xtensa target; the parent LLVM and Clang remain
untouched. Linux builds against Ubuntu 22.04's glibc 2.35 and carries private
libstdc++, libgcc and zlib copies. It does not carry a libc or dynamic loader.
ELF RPATH and Mach-O loader-relative paths keep this cohort together after
relocation. The two macOS architectures are built separately with Apple's
compiler; deployment target 13.0 is configured, but execution is tested on the
workflow's macOS 15 hosts. macOS binaries are ad-hoc signed, not notarized.

Each build verifies the relocated archive, the four patched assembly patterns,
a frozen assembly digest, Xtensa ELF output and an optimization/codegen
round-trip. A separate Linux job extracts and runs the package in a clean
Ubuntu 22.04 container without the source build's GCC installation. Source
builds are CI-only and use at most four compiler jobs and one linker job.

Dispatch with `publish=false` first to retain packages and evidence as Actions
artifacts. `publish=true` requires every platform and the clean-container check
to pass, then creates a versioned fork release, with all three `.tar.gz` files,
per-archive `.sha256` files, `SHA256SUMS` and the combined manifest. Existing
versions are refused; assets are never overwritten. The release tag points to
the pinned LLVM source commit, rather than to the workflow's own commit. It is
not marked as the fork's latest LLVM release.

The installer and compiler target routing are separate Abla changes. Until
those land, do not present downloading this archive as working released Abla
Xtensa support. This workflow proves the LLVM helpers, not an entire firmware
build or board run.

Local maintenance checks (no LLVM compilation):

```
python3 abla-tools/xtensa-companion/unit_tests.py
python3 -m py_compile abla-tools/xtensa-companion/build.py
```
