#!/usr/bin/env python3
"""Build and verify the optional, private Xtensa opt/llc cohort (manual CI only)."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text())
PLATFORMS = {("Linux", "x86_64"): "x86_64-linux", ("Darwin", "arm64"): "arm64-macos",
             ("Darwin", "x86_64"): "x86_64-macos"}


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def capture(*args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, **kwargs)


def digest(path):
    with Path(path).open("rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def tag(revision):
    if not re.fullmatch(r"[1-9][0-9]{0,5}", revision):
        raise ValueError("revision must be a positive decimal integer (at most six digits)")
    return f"abla-xtensa-llvm-{CONFIG['llvmVersion']}-{revision}"


def host_platform():
    return PLATFORMS[(platform.system(), platform.machine())]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def linux_dependencies(path):
    text = capture("ldd", path)
    if "not found" in text:
        raise RuntimeError(text)
    return dict(re.findall(r"^\s*(\S+) => (/\S+)", text, re.M))


def mac_dependencies(path):
    return [line.strip().split(" (", 1)[0] for line in capture("otool", "-L", path).splitlines()[1:]]


def stage(work, root, source):
    """Copy only opt/llc and their private non-system dependency closure."""
    binaries, libraries = root / "bin", root / "lib"
    binaries.mkdir(parents=True)
    libraries.mkdir()
    for name in ("opt", "llc"):
        shutil.copy2(work / "bin" / name, binaries / name)
    if sys.platform == "darwin":
        for binary in binaries.iterdir():
            for dependency in mac_dependencies(binary):
                if dependency.startswith(("/usr/lib/", "/System/Library/")):
                    continue
                name = Path(dependency).name
                if not name.startswith("libLLVM"):
                    raise RuntimeError(f"unexpected non-system Mach-O dependency: {dependency}")
                shutil.copy2(work / "lib" / name, libraries / name)
        for path in [*libraries.iterdir(), *binaries.iterdir()]:
            run("strip", "-S", "-x", path)
            if path.parent == libraries:
                run("install_name_tool", "-id", f"@rpath/{path.name}", path)
            for dependency in mac_dependencies(path):
                if Path(dependency).name.startswith("libLLVM"):
                    run("install_name_tool", "-change", dependency,
                        f"@rpath/{Path(dependency).name}", path)
                elif not dependency.startswith(("/usr/lib/", "/System/Library/")):
                    raise RuntimeError(f"unexpected Mach-O dependency: {dependency}")
            commands = capture("otool", "-l", path)
            rpaths = re.findall(r"cmd LC_RPATH\n.*?\n\s*path (.*?) \(offset", commands)
            for old in dict.fromkeys(rpaths):
                run("install_name_tool", "-delete_rpath", old, path)
            run("install_name_tool", "-add_rpath",
                "@loader_path" if path.parent == libraries else "@loader_path/../lib", path)
            run("codesign", "--force", "--sign", "-", "--timestamp=none", path)
            run("codesign", "--verify", "--strict", path)
    else:
        system = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1",
                  "ld-linux-x86-64.so.2"}
        dependencies = {}
        for binary in binaries.iterdir():
            dependencies.update(linux_dependencies(binary))
        for name, origin in dependencies.items():
            if name in system:
                continue
            if not name.startswith(("libLLVM", "libstdc++", "libgcc_s", "libz.so")):
                raise RuntimeError(f"unexpected ELF dependency: {name}: {origin}")
            shutil.copy2(origin, libraries / name)
        if not any(p.name.startswith("libLLVM") for p in libraries.iterdir()):
            raise RuntimeError("libLLVM was not found in the helper dependency closure")
        for path in [*binaries.iterdir(), *libraries.iterdir()]:
            run("strip", "--strip-unneeded", path)
            # RPATH (not RUNPATH) prevents a parent LLVM's LD_LIBRARY_PATH from
            # substituting another libLLVM or C++ runtime into these helpers.
            run("patchelf", "--force-rpath", "--set-rpath",
                "$ORIGIN" if path.parent == libraries else "$ORIGIN/../lib", path)
        for path in [*binaries.iterdir(), *libraries.iterdir()]:
            for name, resolved in linux_dependencies(path).items():
                if name not in system and Path(resolved).resolve().parent != libraries.resolve():
                    raise RuntimeError(f"dependency escaped the private cohort: {name}: {resolved}")
    licenses = root / "share/licenses"
    licenses.mkdir(parents=True)
    shutil.copy2(source / "LICENSE.TXT", licenses / "LLVM-LICENSE.txt")
    if sys.platform != "darwin":
        for origin, name in [
            ("/usr/share/doc/gcc-12-base/copyright", "GCC-copyright.txt"),
            ("/usr/share/common-licenses/GPL-3", "GPL-3.txt"),
            ("/usr/share/doc/zlib1g/copyright", "zlib-copyright.txt"),
        ]:
            shutil.copy2(origin, licenses / name)
    shutil.copy2(HERE / "README.md", root / "README.md")
    shutil.copy2(HERE / "patches.ll", root / "patches.ll")


def smoke(root, output):
    """Exercise the tools only after moving them away from the build directory."""
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "patches.ll"
    shutil.copy2(root / "patches.ll", fixture)
    opt, llc = root / "bin/opt", root / "bin/llc"
    for tool in (opt, llc):
        version = capture(tool, "--version")
        if f"LLVM version {CONFIG['llvmVersion']}" not in version:
            raise RuntimeError(version)
        (output / f"{tool.name}-version.txt").write_text(version)
    if "xtensa" not in capture(llc, "--version").lower():
        raise RuntimeError("Xtensa is not registered")
    options = ["-mcpu=esp32", "-relocation-model=static", "-O2"]
    run(llc, *options, "-xtensa-direct-calls", "-xtensa-narrow-branches",
        "-xtensa-text-section-literals", fixture, "-o", output / "patches.s")
    assembly = (output / "patches.s").read_text()
    for pattern in (r"call8\s+target", r"extui", r"bbsi", r"beqz\.n"):
        if not re.search(pattern, assembly):
            raise RuntimeError(f"missing patched assembly: {pattern}")
    if digest(output / "patches.s") != CONFIG["assemblySha256"]:
        raise RuntimeError("assembly differs from the frozen Linux/Mac feasibility fixture")
    run(llc, *options, "-filetype=obj", fixture, "-o", output / "patches.o")
    header = (output / "patches.o").read_bytes()[:20]
    if header[:6] != b"\x7fELF\x01\x01" or struct.unpack_from("<H", header, 18)[0] != 94:
        raise RuntimeError("expected a little-endian ELF32 Xtensa object")
    optimized = output / "optimized.ll"
    result = run(opt, "-passes=default<O2>,globaldce", "-S", fixture, "-o", optimized,
                 capture_output=True, text=True)
    if "unable to get target" in result.stderr.lower():
        raise RuntimeError(result.stderr)
    (output / "opt-stderr.txt").write_text(result.stderr)
    run(llc, *options, "-filetype=obj", optimized, "-o", output / "optimized.o")
    if sys.platform != "darwin":
        (output / "glibc.txt").write_text(capture("getconf", "GNU_LIBC_VERSION"))
        for path in (opt, llc):
            text = capture("ldd", path)
            (output / f"{path.name}-loaded.txt").write_text(text)
            for name, resolved in linux_dependencies(path).items():
                if name.startswith(("libLLVM", "libstdc++", "libgcc_s", "libz.so")):
                    if Path(resolved).resolve().parent != (root / "lib").resolve():
                        raise RuntimeError(f"private library not loaded: {name}: {resolved}")
    write_json(output / "smoke.json", {"status": "passed", "assemblySha256": digest(output / "patches.s"),
                                     "objectSha256": digest(output / "patches.o"),
                                     "optimizedObjectSha256": digest(output / "optimized.o")})


def archive(root, destination):
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                for path in [root, *sorted(root.rglob("*"))]:
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise RuntimeError(f"not a regular companion member: {path}")
                    entry = tar.gettarinfo(str(path), arcname=str(path.relative_to(root.parent)))
                    entry.uid = entry.gid = entry.mtime = 0
                    entry.uname = entry.gname = ""
                    entry.mode = 0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644
                    if path.is_file():
                        with path.open("rb") as stream:
                            tar.addfile(entry, stream)
                    else:
                        tar.addfile(entry)


def extract(archive_path, destination):
    """No links, devices, duplicate names, traversal, or unbounded extraction."""
    total, seen = 0, set()
    with tarfile.open(archive_path, "r:gz") as tar:
        for entry in tar:
            parts = Path(entry.name).parts
            if not parts or entry.name.startswith("/") or any(p in (".", "..") for p in parts):
                raise ValueError("unsafe archive path")
            if entry.name in seen or len(seen) >= 1000 or not (entry.isdir() or entry.isfile()):
                raise ValueError("unsafe archive member")
            seen.add(entry.name)
            total += entry.size
            if total > 1024 * 1024 * 1024:
                raise ValueError("archive exceeds 1 GiB unpacked")
            path = destination.joinpath(*parts)
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(entry.mode & 0o755)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("expected one archive root")
    return roots[0]


def verify(archive_path, output):
    expected = archive_path.with_suffix(archive_path.suffix + ".sha256").read_text().split()
    if expected != [digest(archive_path), archive_path.name]:
        raise ValueError("archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="relocated Xtensa companion ") as temp:
        root = extract(archive_path, Path(temp))
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest["platform"] != host_platform():
            raise ValueError("companion host mismatch")
        files = manifest["files"]
        actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
        if actual != set(files) | {"manifest.json"}:
            raise ValueError("manifest file set mismatch")
        for name, expected_hash in files.items():
            if digest(root / name) != expected_hash:
                raise ValueError(f"companion file hash mismatch: {name}")
        smoke(root, output)


def build(revision, output):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("LLVM builds are manual CI only; do not build locally")
    version, host = tag(revision), host_platform()
    output.mkdir(parents=True, exist_ok=True)
    work = output.parent / "llvm-work"
    work.mkdir()
    source_archive = work / "source.tar.gz"
    url = f"https://codeload.github.com/{CONFIG['sourceRepository']}/tar.gz/{CONFIG['sourceCommit']}"
    with urllib.request.urlopen(url, timeout=120) as source, source_archive.open("wb") as target:
        shutil.copyfileobj(source, target)
    if digest(source_archive) != CONFIG["sourceArchiveSha256"]:
        raise RuntimeError("LLVM source archive checksum mismatch")
    source = work / "source"
    source.mkdir()
    # The source archive is checksum-pinned, unlike an arbitrary user archive.
    run("tar", "-xzf", source_archive, "--strip-components=1", "-C", source)
    source_archive.unlink()
    build_dir = work / "build"
    flags = ["-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DLLVM_ENABLE_PROJECTS=",
             "-DLLVM_TARGETS_TO_BUILD=", "-DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=Xtensa",
             "-DLLVM_BUILD_LLVM_DYLIB=ON", "-DLLVM_LINK_LLVM_DYLIB=ON", "-DLLVM_DYLIB_COMPONENTS=all",
             "-DLLVM_ENABLE_ASSERTIONS=OFF", "-DLLVM_INCLUDE_TESTS=OFF", "-DLLVM_INCLUDE_DOCS=OFF",
             "-DLLVM_INCLUDE_EXAMPLES=OFF", "-DLLVM_INCLUDE_BENCHMARKS=OFF",
             "-DLLVM_ENABLE_ZSTD=OFF", "-DLLVM_ENABLE_ZLIB=ON", "-DLLVM_ENABLE_LIBXML2=OFF",
             "-DLLVM_ENABLE_LIBEDIT=OFF", "-DLLVM_ENABLE_FFI=OFF", "-DLLVM_PARALLEL_LINK_JOBS=1"]
    if sys.platform == "darwin":
        sdk = capture("xcrun", "--sdk", "macosx", "--show-sdk-path").strip()
        flags += [f"-DCMAKE_OSX_DEPLOYMENT_TARGET={CONFIG['macosDeploymentTarget']}",
                  f"-DCMAKE_OSX_SYSROOT={sdk}", f"-DZLIB_INCLUDE_DIR={sdk}/usr/include",
                  f"-DZLIB_LIBRARY={sdk}/usr/lib/libz.tbd"]
    jobs = min(4, os.cpu_count() or 1)
    began = time.monotonic()
    run("cmake", "-S", source / "llvm", "-B", build_dir, *flags)
    run("cmake", "--build", build_dir, "--parallel", jobs, "--target", "opt", "llc")
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    root = work / f"{version}-{host}"
    stage(build_dir, root, source)
    write_json(root / "manifest.json", {
        **CONFIG, "version": version, "revision": int(revision), "platform": host,
        "builderCommit": os.environ.get("GITHUB_SHA", ""),
        "buildRun": "https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "") +
                    "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", ""),
        "files": {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()},
    })
    packaged = output / f"{root.name}.tar.gz"
    archive(root, packaged)
    packaged.with_suffix(".gz.sha256").write_text(f"{digest(packaged)}  {packaged.name}\n")
    verify(packaged, output / "relocation-smoke")
    shutil.copy2(root / "manifest.json", output / f"{host}.manifest.json")
    shutil.copy2(build_dir / "CMakeCache.txt", output / f"{host}.cmake.txt")
    write_json(output / f"{host}.build.json", {
        "platform": host, "wallSeconds": time.monotonic() - began, "jobs": jobs,
        "userSeconds": usage.ru_utime, "systemSeconds": usage.ru_stime,
        "largestProcessPeakKiB": usage.ru_maxrss / (1024 if sys.platform == "darwin" else 1),
        "archiveBytes": packaged.stat().st_size,
        "unpackedBytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "sourceCommit": CONFIG["sourceCommit"], "cmakeFlags": flags,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("tag")
    plan.add_argument("revision")
    compile_command = sub.add_parser("build")
    compile_command.add_argument("revision")
    compile_command.add_argument("output", type=Path)
    check = sub.add_parser("verify")
    check.add_argument("archive", type=Path)
    check.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "tag":
        print(tag(args.revision))
    elif args.command == "build":
        build(args.revision, args.output.resolve())
    else:
        verify(args.archive.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
