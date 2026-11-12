#!/usr/bin/env bash
# CutSkills · 依赖唯一存放处
#
# 问题：每个剪辑任务各跑一遍 npx / pip / whisper，依赖就各下一份。
# 结果是 ~/.npm 3.5G、~/.cache/puppeteer 1.8G、散落的 node_modules 十几处，
# 而且下次任务还是不知道哪份能用。
#
# 解法：所有「下载来的东西」只放一个地方——
#
#   ${CUTSKILLS_HOME:-~/.cutskills}/
#   ├── npm/            node 依赖（hyperframes 等），装一次用到底
#   ├── npm-cache/      npm 下载缓存（不清，下次装同版本直接命中）
#   ├── py/             python 依赖（pip --target）
#   ├── cache/          XDG 缓存：chrome、whisper 权重、模型
#   ├── models/         HF_HOME / TORCH_HOME
#   ├── bin/            自建命令软链
#   └── manifest.json   装了什么、什么版本、多大、什么时候——防止重复安装
#
# 用法（在任务里第一件事）：
#   source scripts/runtime.sh env      # 把环境变量导进来（幂等）
#   scripts/runtime.sh check           # 看装了什么、缺什么、占多大
#   scripts/runtime.sh install         # 只装缺的
#   scripts/runtime.sh adopt           # 把已有的旧缓存搬进来并留软链（省一次重下）
#   scripts/runtime.sh run <命令...>    # 带环境跑一条命令
set -uo pipefail

ROOT="${CUTSKILLS_HOME:-$HOME/.cutskills}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS="$HERE/../deps.json"

export CUTSKILLS_HOME="$ROOT"
export npm_config_prefix="$ROOT/npm"
export npm_config_cache="$ROOT/npm-cache"
export NODE_PATH="$ROOT/npm/node_modules${NODE_PATH:+:$NODE_PATH}"
# 自编的带 zscale 的 ffmpeg 排在系统前面：素材里的 HLG / 杜比视界要靠它转 SDR，
# 系统 brew 的 ffmpeg 没有 libzimg，压出来偏亮偏灰（原因见 references/ffmpeg-zimg.md）。
if [ -x "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" ]; then
  export CUTSKILLS_FFMPEG="$ROOT/opt/ffmpeg-zimg/bin/ffmpeg"
fi
export PATH="${CUTSKILLS_FFMPEG:+$ROOT/opt/ffmpeg-zimg/bin:}$ROOT/npm/node_modules/.bin:$ROOT/bin:$PATH"
export PYTHONPATH="$ROOT/py:$HERE/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PIP_CACHE_DIR="$ROOT/pip-cache"
export XDG_CACHE_HOME="$ROOT/cache"
export HF_HOME="$ROOT/models/hf"
export TRANSFORMERS_CACHE="$ROOT/models/hf"
export TORCH_HOME="$ROOT/models/torch"
export PUPPETEER_CACHE_DIR="$ROOT/cache/puppeteer"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/cache/playwright"

mkdir -p "$ROOT"/{npm,npm-cache,py,cache,models,bin,pip-cache}

ensure_manifest() {
  [ -f "$ROOT/manifest.json" ] || cat > "$ROOT/manifest.json" <<'JSON'
{"version": 1, "npm": {}, "pip": {}, "adopted": [], "note": "由 scripts/runtime.sh 维护；不要手改，改 deps.json"}
JSON
}

# 读 deps.json 里某类依赖的名字（不依赖 jq）
dep_names() {
  python3 - "$DEPS" "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for k in (d.get(sys.argv[2]) or {}):
    print(k)
PY
}

dep_spec() {
  python3 - "$DEPS" "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print((d.get(sys.argv[2]) or {}).get(sys.argv[3], ""))
PY
}

cmd_env() {
  cat <<EOF
export CUTSKILLS_HOME="$ROOT"
export npm_config_prefix="$ROOT/npm"
export npm_config_cache="$ROOT/npm-cache"
export NODE_PATH="$ROOT/npm/node_modules"
export CUTSKILLS_FFMPEG="${CUTSKILLS_FFMPEG:-}"
export PATH="\${CUTSKILLS_FFMPEG:+$ROOT/opt/ffmpeg-zimg/bin:}$ROOT/npm/node_modules/.bin:$ROOT/bin:\$PATH"
export PYTHONPATH="$ROOT/py:$HERE/vendor"
export PIP_CACHE_DIR="$ROOT/pip-cache"
export XDG_CACHE_HOME="$ROOT/cache"
export HF_HOME="$ROOT/models/hf"
export TRANSFORMERS_CACHE="$ROOT/models/hf"
export TORCH_HOME="$ROOT/models/torch"
export PUPPETEER_CACHE_DIR="$ROOT/cache/puppeteer"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/cache/playwright"
EOF
}

cmd_check() {
  echo "依赖根目录：$ROOT"
  echo "系统依赖："
  for b in ffmpeg ffprobe node npm python3 sips; do
    printf '  %-8s ' "$b"
    if command -v "$b" >/dev/null 2>&1; then
      # ffmpeg 的版本信息走 stderr，不是 stdout——只重定向 stdout 会得到空字符串
      v=$("$b" --version 2>&1 | tr '\n' ' ')
      echo "${v:0:60}"
    else
      echo "缺失"
    fi
  done
  # 自编 ffmpeg 的**动态库还能不能解析**——这一条是 2026-11-12 夜里补的：
  # /opt/homebrew/Cellar 与 /opt/homebrew/opt 被一次 brew 操作清空（mtime 全部变成同一秒），
  # 次日渲染报 `dyld: Library not loaded: libvpx.12.dylib`，而前一天一模一样的命令是好的。
  # 依赖清单里写着「ffmpeg 有」不等于「它跑得起来」，所以要真的跑一次 `-version`。
  if [ -x "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" ]; then
    echo "自编 ffmpeg（HDR→SDR 的 zscale）："
    if out=$("$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" -version 2>&1); then
      printf '  ok   %s\n' "$(echo "$out" | head -1 | cut -c1-64)"
      miss=""
      for lib in $(otool -L "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" 2>/dev/null \
                   | awk 'NR>1{print $1}' | grep -E '^/(opt/homebrew|usr/local)'); do
        [ -e "$lib" ] || miss="$miss $(basename "$lib")"
      done
      if [ -n "$miss" ]; then
        echo "  ！动态库找不到：$miss"
        echo "    brew 把 formula 换掉了。按 references/ffmpeg-zimg.md 重装缺的那几个。"
      fi
    else
      echo "  ！跑不起来：$(echo "$out" | head -2 | tr '\n' ' ')"
      echo "    修法见 references/ffmpeg-zimg.md。没有它，HDR 素材压出来偏亮偏灰。"
    fi
  fi
  echo "node 包："
  if [ -d "$ROOT/npm/node_modules" ]; then
    for d in "$ROOT"/npm/node_modules/*/; do
      [ -d "$d" ] || continue
      n=$(basename "$d")
      v=$(python3 -c "import json,sys;print(json.load(open('$d/package.json')).get('version','?'))" 2>/dev/null || echo '?')
      printf '  %-22s %s\n' "$n" "$v"
    done
  else
    echo "  （空）"
  fi
  echo "python 包："
  py=$(ls "$ROOT/py" 2>/dev/null | grep -v dist-info | head -20)
  [ -n "$py" ] && echo "$py" | sed 's/^/  /' || echo "  （空；随包 vendor 兜底：$HERE/vendor）"
  echo "占用："
  du -sh "$ROOT"/* 2>/dev/null | sed 's/^/  /'
  echo "总计：$(du -sh "$ROOT" 2>/dev/null | cut -f1)"
}

cmd_install() {
  ensure_manifest
  local changed=0
  for name in $(dep_names npm); do
    spec=$(dep_spec npm "$name")
    [ -z "$spec" ] && continue
    if [ -d "$ROOT/npm/node_modules/$name" ] && [ -n "$(ls -A "$ROOT/npm/node_modules/$name" 2>/dev/null)" ]; then
      echo "  ✓ npm $name 已在 $ROOT"
      continue
    fi
    echo "  → 安装 npm $name@$spec"
    ( cd "$ROOT/npm" && npm install --silent --no-fund --no-audit "$name@$spec" ) || {
      echo "  ! $name 安装失败（网络或代理问题），稍后重试"; continue; }
    changed=1
  done
  for name in $(dep_names pip); do
    spec=$(dep_spec pip "$name")
    [ -z "$spec" ] && continue
    if [ -d "$ROOT/py/$name" ] || ls "$ROOT/py"/${name}-*.dist-info >/dev/null 2>&1; then
      echo "  ✓ pip $name 已在 $ROOT/py"
      continue
    fi
    echo "  → 安装 pip $name"
    if ! pip3 install --quiet --target "$ROOT/py" "$name$spec" 2>/tmp/cutskills-pip.err; then
      # pip 走 SOCKS 代理常失败（缺 PySocks）。随包 vendor 里有就直接搬进来，
      # 保持「所有依赖都在 $CUTSKILLS_HOME」这一条不破。
      if [ -d "$HERE/vendor/$name" ]; then
        cp -R "$HERE/vendor/$name" "$ROOT/py/" 2>/dev/null && \
          cp -R "$HERE/vendor/$name"-*.dist-info "$ROOT/py/" 2>/dev/null
        echo "  ⇢ pip 不可用，已从随包 vendor 复制 $name 到 $ROOT/py"
      else
        echo "  ! pip 安装失败：$(tail -2 /tmp/cutskills-pip.err | tr '\n' ' ')"
        echo "    可改用随包 vendor：PYTHONPATH=$HERE/vendor"
      fi
      continue
    fi
    changed=1
  done
  python3 - "$ROOT" <<'PY'
import json, subprocess, sys, datetime, pathlib
root = pathlib.Path(sys.argv[1]); m = json.loads((root/"manifest.json").read_text())
m["npm"] = {}; m["pip"] = {}
d = root/"npm/node_modules"
if d.exists():
    for p in sorted(d.glob("*/package.json")):
        try: m["npm"][p.parent.name] = json.loads(p.read_text()).get("version")
        except Exception: pass
for di in (root/"py").glob("*.dist-info"):
    name = di.name.split("-")[0]
    m["pip"][name] = di.name.split("-")[1]
m["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
(root/"manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
print(f"  manifest 已更新：npm {len(m['npm'])} 个 / pip {len(m['pip'])} 个")
PY
  [ $changed -eq 0 ] && echo "  没有需要新装的东西。"
}

# 把已有的散落缓存搬进来，并在原位置留软链——工具仍以为在 ~/.cache，实际只有一份
cmd_adopt() {
  ensure_manifest
  local pairs=(
    "$HOME/.cache/hyperframes|$ROOT/cache/hyperframes"
    "$HOME/.cache/puppeteer|$ROOT/cache/puppeteer"
    "$HOME/.npm/_npx|$ROOT/npm-cache/_npx"
  )
  for pair in "${pairs[@]}"; do
    src="${pair%%|*}"; dst="${pair##*|}"
    if [ -L "$src" ]; then echo "  ✓ 已是软链：$src"; continue; fi
    if [ -d "$src" ]; then
      mkdir -p "$(dirname "$dst")"
      [ -d "$dst" ] || mv "$src" "$dst"
      rm -rf "$src" 2>/dev/null
      ln -s "$dst" "$src"
      echo "  ⇢ 已搬入并软链：$src → $dst"
    else
      echo "  · 没有 $src，跳过"
    fi
  done
  python3 - "$ROOT" <<'PY'
import json, sys, pathlib, datetime
root = pathlib.Path(sys.argv[1]); p = root/"manifest.json"
m = json.loads(p.read_text()); m.setdefault("adopted", []).append(datetime.datetime.now().isoformat(timespec="seconds"))
p.write_text(json.dumps(m, ensure_ascii=False, indent=1))
PY
}

case "${1:-env}" in
  env)     cmd_env ;;
  check)   cmd_check ;;
  install) cmd_install ;;
  adopt)   cmd_adopt ;;
  path)    echo "$ROOT" ;;
  run)     shift; export PATH PYTHONPATH NODE_PATH XDG_CACHE_HOME HF_HOME TORCH_HOME; exec "$@" ;;
  *)
    echo "用法: runtime.sh [env|check|install|adopt|path|run <cmd>]" >&2
    exit 2 ;;
esac
