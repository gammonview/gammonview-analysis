#!/bin/bash
# SPDX-License-Identifier: MIT
# Build the browser engine into dist/: bgsage's C++ compiled by emscripten,
# with the weights and bearoff database of the same bgsage version beside it.
#
#   ./build.sh            needs emcc (brew install emscripten), git and uv
#
# The bgsage version is `bgsage.version`, and both halves come from it: the C++
# from that tag on GitHub, the assets from that wheel on PyPI. So the engine
# can never be built from one bgsage and shipped with another's networks.
set -euo pipefail
cd "$(dirname "$0")"
VER=$(tr -d '[:space:]' < bgsage.version)
SRC=.bgsage/$VER
if [ ! -d "$SRC/cpp" ]; then
    rm -rf "$SRC"
    git clone --quiet --depth 1 --branch "v$VER" https://github.com/markbgsage/bgsage.git "$SRC"
fi

# A patched copy, never the checkout itself. One patch: neural_net.cpp picks its
# SIMD path by target architecture, and wasm32 is neither x86 nor ARM, so it
# would fall to plain scalar code. Emscripten translates NEON intrinsics to WASM
# SIMD, and that path reproduces native bgsage's numbers exactly; its AVX2
# translation does not (it drifts by up to 0.003 equity), so NEON it is.
rm -rf build && mkdir -p build dist
cp -R "$SRC/cpp/src" "$SRC/cpp/include" build/
perl -0pi -e 's/#if defined\(__x86_64__\) \|\| defined\(_M_X64\)/#if defined(GV_WASM_NEON)\n  #include <arm_neon.h>\n  #define BGBOT_USE_NEON 1\n#elif defined(__x86_64__) || defined(_M_X64)/' build/src/neural_net.cpp
grep -q GV_WASM_NEON build/src/neural_net.cpp || { echo "neural_net.cpp patch did not apply" >&2; exit 1; }

SOURCES=(board moves strategy pubeval game benchmark encoding neural_net training multipy rollout cube cube_eval match_equity bearoff)
FILES=(src/entry.cpp)
for s in "${SOURCES[@]}"; do FILES+=("build/src/$s.cpp"); done
FLAGS=(-std=c++17 -O3 -fno-math-errno -funroll-loops -msimd128 -mfpu=neon -DGV_WASM_NEON
       -fexceptions -Ibuild/include
       -sALLOW_MEMORY_GROWTH=1 -sMODULARIZE=1 -sEXPORT_ES6=1 -sENVIRONMENT=web,worker,node
       -sFORCE_FILESYSTEM=1 -sSTACK_SIZE=8MB
       -sEXPORTED_FUNCTIONS=_gvengine_create,_gvengine_destroy,_gvengine_analyze,_gvengine_free,_gvengine_last_error,_gvengine_rollout_moves,_malloc,_free
       -sEXPORTED_RUNTIME_METHODS=FS,UTF8ToString,stringToNewUTF8,HEAP32)

# Two builds. Threads need SharedArrayBuffer, which a browser grants only to a
# cross-origin-isolated page; the single-threaded one runs anywhere.
em++ "${FLAGS[@]}" "${FILES[@]}" -o dist/gvengine.mjs &
em++ "${FLAGS[@]}" "${FILES[@]}" -pthread -sDEFAULT_PTHREAD_STACK_SIZE=2MB \
     -sPTHREAD_POOL_SIZE='navigator.hardwareConcurrency' -o dist/gvengine-mt.mjs &
wait %1 && wait %2

uv run --quiet --no-project --with "bgsage==$VER" python -I scripts/assets.py dist "$VER"
cp "$SRC/LICENSE" dist/LICENSE-bgsage.txt
ls -la dist
