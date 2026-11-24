#!/usr/bin/env python3
"""Build an Android abi3 wheel from a Rust/PyO3 sdist.

Unlike maturin, this needs no maturin binary: it compiles the crate's cdylib
with cargo and assembles the wheel itself.  The resulting extension is a
limited-API `.so` with no libpython dependency, so one artifact works on
Termux, Pydroid 3 and python-for-android.

Example:
    python3 make_rust_wheel.py --sdist bcrypt-5.0.0.tar.gz \
        --version 5.0.0 --abi cp38 --features pyo3/abi3-py38
"""

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import re
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "wheels"


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def parse_sdist(sdist: Path, work: Path, abi_min: str) -> tuple[Path, dict]:
    with tarfile.open(sdist) as tf:
        tf.extractall(work, filter="data")
    roots = [p for p in work.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"expected one top-level dir in {sdist}, got {roots}")
    root = roots[0]
    # PyO3's abi3 version is chosen by the lowest abi3-py3XX feature present.
    # Normalise all of them to 3.8 for the widest possible wheel tag.
    for mf in list(root.rglob("Cargo.toml")) + list(root.rglob("pyproject.toml")):
        text = mf.read_text()
        patched = re.sub(r"abi3-py3\d\d", f"abi3-{abi_min}", text)
        if patched != text:
            mf.write_text(patched)
    with (root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    return root, pyproject


def crate_manifest(root: Path, pyproject: dict) -> Path:
    maturin = pyproject.get("tool", {}).get("maturin", {})
    if maturin.get("manifest-path"):
        return root / maturin["manifest-path"]
    ext = pyproject.get("tool", {}).get("setuptools-rust", {}).get("ext-modules", [])
    if ext and ext[0].get("path"):
        return root / ext[0]["path"]
    return root / "Cargo.toml"


def find_cdylib(root: Path, target_dir: Path, libname: str) -> Path:
    names = {f"lib{libname}.so", f"lib{libname.replace('-', '_')}.so"}
    candidates = [target_dir / n for n in names]
    candidates += [p for n in names for p in root.glob(f"**/target/**/{n}")]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError(f"cannot find cdylib {names} under {root} / {target_dir}")


def record_line(path: Path, arcname: str) -> str:
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arcname},sha256={digest},{len(data)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdist", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--abi", required=True, help="python tag, e.g. cp38 (implies abi3)")
    ap.add_argument("--platform-tags", default="android_24_arm64_v8a,linux_aarch64")
    ap.add_argument("--features", default="", help="extra cargo features, comma separated")
    ap.add_argument("--abi-min", default="py38",
                    help="PyO3 abi3 baseline feature, e.g. py38 or py39")
    ap.add_argument("--python-versions", default="3.13,3.14",
                    help="Python versions to ship DT_NEEDED variants for")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep-build", action="store_true")
    args = ap.parse_args()

    tags = [t.strip() for t in args.platform_tags.replace(" ", "").split(",") if t.strip()]
    if not tags:
        raise SystemExit("no platform tags")

    work = Path(tempfile.mkdtemp(prefix="rustwheel-", dir="/tmp/opencode"))
    if args.keep_build:
        print("build dir:", work)
    try:
        root, pyproject = parse_sdist(args.sdist, work, args.abi_min)
        project = pyproject.get("project", {})
        name = project.get("name")
        if not name:
            raise RuntimeError("no [project].name")
        dist_name = name
        norm = name.replace("-", "_").replace(".", "_")

        maturin = pyproject.get("tool", {}).get("maturin", {})
        ext = pyproject.get("tool", {}).get("setuptools-rust", {}).get("ext-modules", [])
        if maturin.get("module-name"):
            module_name = maturin["module-name"]
        elif ext:
            module_name = ext[0]["target"]
        else:
            raise RuntimeError("no module name in pyproject.toml")

        python_source = maturin.get("python-source")
        if not python_source:
            setuptools = pyproject.get("tool", {}).get("setuptools", {})
            python_source = (setuptools.get("package-dir") or {}).get("")

        manifest = crate_manifest(root, pyproject)
        with manifest.open("rb") as f:
            cargo = tomllib.load(f)
        libname = (cargo.get("lib") or {}).get("name") or cargo["package"]["name"].replace("-", "_")

        # Build with the local interpreter for PyO3's build config.  The crate's
        # abi3 + extension-module features keep libpython out of the link line.
        env = os.environ.copy()
        env.pop("PYO3_NO_PYTHON", None)
        env["PYO3_PYTHON"] = shutil.which("python3") or sys.executable
        # Reuse the compiled artifacts across retries (sdists are re-extracted).
        env["CARGO_TARGET_DIR"] = f"/tmp/opencode/rust/target/{norm}"
        cmd = ["cargo", "build", "--release", "--manifest-path", manifest,
               "--target", "aarch64-linux-android"]
        features = [f for f in args.features.split(",") if f]
        if features:
            cmd += ["--features", ",".join(features)]
        run(cmd, cwd=root, env=env)

        cargo_target = Path(env["CARGO_TARGET_DIR"]) / "aarch64-linux-android" / "release"
        lib = find_cdylib(root, cargo_target, libname)

        stage = work / "stage"
        stage.mkdir()
        parts = module_name.split(".")
        ext_name = parts[-1]
        pkg_parts = parts[:-1]

        # Copy the Python package that ships alongside the extension.
        base = root / python_source if python_source else root
        src_pkg = base.joinpath(*pkg_parts) if pkg_parts else base
        if src_pkg.is_dir():
            dst_pkg = stage.joinpath(*pkg_parts) if pkg_parts else stage
            dst_pkg.mkdir(parents=True, exist_ok=True)
            for item in src_pkg.iterdir():
                if item.name in ("__pycache__", ".pytest_cache"):
                    continue
                target = dst_pkg / item.name
                if item.is_dir():
                    shutil.copytree(item, target,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(item, target)

        dst = stage.joinpath(*pkg_parts) if pkg_parts else stage
        dst.mkdir(parents=True, exist_ok=True)

        # PyO3 always adds a versioned libpython DT_NEEDED on Android, even
        # with the extension-module feature.  Ship one copy per supported
        # Python and pick the right one at import time; the compiled code is
        # abi3, so the only difference between copies is the DT_NEEDED name.
        needed = subprocess.run(["patchelf", "--print-needed", str(lib)],
                                capture_output=True, text=True, check=True).stdout.split()
        py_soname = next((n for n in needed if n.startswith("libpython")), None)
        if not py_soname:
            raise RuntimeError(f"PyO3 did not add a libpython DT_NEEDED to {lib}")
        versions = sorted({v.strip() for v in args.python_versions.split(",") if v.strip()})
        variants = []
        for version in versions:
            tag = version.replace(".", "")
            sub = dst / f"_py{tag}"
            sub.mkdir(exist_ok=True)
            (sub / "__init__.py").write_text("")
            # Keep the original filename: the init symbol is PyInit_<name>.
            target = sub / f"{ext_name}.abi3.so"
            shutil.copy2(lib, target)
            want = f"libpython{version}.so"
            if py_soname != want:
                run(["patchelf", "--replace-needed", py_soname, want, str(target)])
            after = subprocess.run(["patchelf", "--print-needed", str(target)],
                                   capture_output=True, text=True, check=True).stdout.split()
            if want not in after:
                raise RuntimeError(f"patchelf failed to set {want} on {target}")
            print(f"variant: _py{tag}/{ext_name}.abi3.so -> {want}", flush=True)
            variants.append((version, tag))

        shim = ["import sys", "_v = sys.version_info[:2]"]
        for i, (version, tag) in enumerate(reversed(variants)):
            minor = int(version.split(".")[1])
            keyword = "if" if i == 0 else "elif"
            shim += [f"{keyword} _v >= (3, {minor}):",
                     f"    from ._py{tag} import {ext_name} as _ext"]
        shim += ["else:",
                 "    raise ImportError('this wheel needs Python 3.8 or newer')",
                 "_skip = {'__name__', '__doc__', '__package__', '__loader__',",
                 "         '__spec__', '__file__', '__builtins__'}",
                 "globals().update({k: v for k, v in vars(_ext).items()",
                 "                  if k not in _skip})"]
        (dst / f"{ext_name}.py").write_text("\n".join(shim) + "\n")

        dist_info = stage / f"{norm}-{args.version}.dist-info"
        dist_info.mkdir()
        summary = project.get("description", "")
        lines = [
            "Metadata-Version: 2.1",
            f"Name: {dist_name}",
            f"Version: {args.version}",
            f"Summary: {summary}",
        ]
        if project.get("requires-python"):
            lines.append(f"Requires-Python: {project['requires-python']}")
        license_expr = project.get("license")
        if isinstance(license_expr, dict):
            license_expr = license_expr.get("text") or license_expr.get("file")
        if isinstance(license_expr, str) and len(license_expr) < 100:
            lines.append(f"License: {license_expr}")
        for dep in project.get("dependencies", []):
            lines.append(f"Requires-Dist: {dep}")
        for classifier in project.get("classifiers", []):
            lines.append(f"Classifier: {classifier}")
        (dist_info / "METADATA").write_text("\n".join(lines) + "\n")

        wheel_lines = ["Wheel-Version: 1.0", "Generator: android-abi3-rust-wheel-builder",
                       "Root-Is-Purelib: false"]
        wheel_lines += [f"Tag: {args.abi}-abi3-{t}" for t in tags]
        (dist_info / "WHEEL").write_text("\n".join(wheel_lines) + "\n")

        scripts = project.get("scripts") or project.get("entry-points", {}).get("console-scripts")
        if scripts:
            ep = ["[console_scripts]"]
            ep += [f"{k} = {v}" for k, v in scripts.items()]
            (dist_info / "entry_points.txt").write_text("\n".join(ep) + "\n")

        license_files = dist_info / "licenses"
        for lic in root.glob("LICENSE*"):
            if lic.is_file():
                license_files.mkdir(exist_ok=True)
                shutil.copy2(lic, license_files / lic.name)

        record = []
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                arc = path.relative_to(stage).as_posix()
                if arc.startswith(dist_info.name + "/") and path.name == "RECORD":
                    continue
                record.append(record_line(path, arc))
        record.append(f"{dist_info.name}/RECORD,,")
        (dist_info / "RECORD").write_text("\n".join(record) + "\n")

        platform_tag = ".".join(tags)
        wheel_name = f"{norm}-{args.version}-{args.abi}-abi3-{platform_tag}.whl"
        args.out.mkdir(parents=True, exist_ok=True)
        wheel_path = args.out / wheel_name
        with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(stage).as_posix())
        print("built:", wheel_path, wheel_path.stat().st_size, "bytes")
    finally:
        if not args.keep_build:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
