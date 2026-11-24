# Python Android abi3 wheels (Rust / PyO3)

Android `arm64-v8a` wheels for small Rust-backed Python packages. These are
**separate from** the [`opencv-python-android-abi3`](https://github.com/Kash-KLM-001/opencv-python-android-abi3)
repo — different packages, different release tags, one place per project.

## Why these exist

PyPI does not publish bionic/Android wheels for most Rust/PyO3 packages. A
`manylinux` wheel cannot load on Android (bionic vs glibc). Because PyO3 can
emit a **stable-ABI** extension (`abi3`), one build works on Termux, Pydroid 3
and python-for-android/Kivy, with no `libpython` dependency.

The wheel tags are `cp3x-abi3-android_24_arm64_v8a` (API 24+, arm64-v8a).

## Packages

| Package | Wheel tag | Status | Notes |
|---|---|---|---|
| `bcrypt` | `cp38-abi3` | built | pure Rust, no system libs |
| `safetensors` | `cp38-abi3` | built | pure Rust |
| `watchfiles` | `cp38-abi3` | built | vendored Rust notify (needs `anyio`) |
| `tokenizers` | `cp39-abi3` | built | upstream needs `weakref`, unavailable under `abi3-py38` |
| `cryptography` | `cp39-abi3` | blocked | needs an OpenSSL built with the NDK |

`cp38-abi3` / `cp39-abi3` is the *tag*: one binary ABI for Python 3.8+ / 3.9+.
The wheels still contain two copies of the compiled extension because PyO3 on
Android always links a versioned `libpython3.x.so` (unlike our `cv2` wheel).
A tiny import shim selects the 3.13 (Pydroid) or 3.14 (Termux) copy at import
time, so one `.whl` covers both runtimes.

Version-locked (`cp3xx`, not abi3) packages need one wheel per Python version
and are **not** part of this repo: `pydantic-core`, `orjson`, `jiter`,
`rpds-py`, `tiktoken`, `polars`.

## Install

From a release asset, e.g.:

```bash
pip install --no-index /path/to/bcrypt-5.0.0-cp38-abi3-android_24_arm64_v8a.whl
```

On Termux use a `--target` directory or a venv if you do not want to replace
the distro package:

```bash
pip install --no-index --target ~/wheels-test bcrypt-*.whl
PYTHONPATH=~/wheels-test python3 -c 'import bcrypt; print(bcrypt.__version__)'
```

## Build

Toolchain: Termux Rust (`rustc`/`cargo`), NDK r29 clang, Python headers from
Termux. Everything is built as bionic arm64; no host build is usable.

```bash
bash build_rust_wheel.sh bcrypt      # one package
bash build_rust_wheel.sh --all       # all abi3 packages
```

The builder downloads the sdist from PyPI, applies the crate's own
`[tool.maturin]` settings, compiles a `cdylib` with `cargo`, then packs the
result into a wheel with an abi3 tag and an Android platform tag. See
`make_rust_wheel.py`.

Verify a built wheel:

```bash
python3 - <<'EOF'
import zipfile, glob
for w in glob.glob('wheels/*.whl'):
    z = zipfile.ZipFile(w)
    print(w, z.read([n for n in z.namelist() if n.endswith('.dist-info/WHEEL')][0]).decode())
EOF
readelf -d unpacked/<pkg>/*.so | grep NEEDED   # no libpython*
```

## License

Each wheel carries its upstream license from the sdist. This repo contains
only build tooling and documentation.
