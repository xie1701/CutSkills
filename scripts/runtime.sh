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
#
# 平台：本脚本是 POSIX（macOS / Linux / WSL 里的 bash）。Windows 原生走
# install.ps1（-CheckOnly 体检 / -Install 安装），**未在 Windows 真机验证**；
# 在 Git Bash / MSYS 下跑到 install / adopt 会直接拒跑并指向 install.ps1（软链、otool、
# pip --target 的语义在那边不一样，硬跑只会装出半截环境）。
set -uo pipefail

# 平台判定就这一处：后面所有分支都读 $OS_KIND，不再各自摸 uname
os_kind() {
  case "$(uname -s 2>/dev/null)" in
    Darwin)               echo darwin ;;
    Linux)                echo linux ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *)                    echo other ;;
  esac
}
OS_KIND="$(os_kind)"

ROOT="${CUTSKILLS_HOME:-$HOME/.cutskills}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPS="$HERE/../deps.json"

export CUTSKILLS_HOME="$ROOT"
export npm_config_prefix="$ROOT/npm"
export npm_config_cache="$ROOT/npm-cache"
export NODE_PATH="$ROOT/npm/node_modules${NODE_PATH:+:$NODE_PATH}"
# 自编的带 zscale 的 ffmpeg 排在系统前面：素材里的 HLG / 杜比视界要靠它转 SDR，
# 系统 brew 的 ffmpeg 没有 libzimg，压出来偏亮偏灰。
if [ -x "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" ]; then
  export CUTSKILLS_FFMPEG="$ROOT/opt/ffmpeg-zimg/bin/ffmpeg"
fi
# $ROOT/opt/bin 排最前：自建/抽出的 CLI（coli）只在这里，不走系统 PATH。
# 2026-11-13 T-023：Cola 升级后 `coli` 从系统 PATH 上消失，整条转写链哑；
# 现在 coli 是 $ROOT/opt/coli-app 里抽出的 @marswave/coli，靠 $ROOT/opt/bin 软链暴露。
export PATH="$ROOT/opt/bin:${CUTSKILLS_FFMPEG:+$ROOT/opt/ffmpeg-zimg/bin:}$ROOT/npm/node_modules/.bin:$ROOT/bin:$PATH"
export PYTHONPATH="$ROOT/py:$HERE/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PIP_CACHE_DIR="$ROOT/pip-cache"
export XDG_CACHE_HOME="$ROOT/cache"
export HF_HOME="$ROOT/models/hf"
export TRANSFORMERS_CACHE="$ROOT/models/hf"
export TORCH_HOME="$ROOT/models/torch"
export PUPPETEER_CACHE_DIR="$ROOT/cache/puppeteer"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/cache/playwright"

mkdir -p "$ROOT"/{npm,npm-cache,py,cache,models,bin,opt/bin,pip-cache}

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
export PATH="$ROOT/opt/bin:\${CUTSKILLS_FFMPEG:+$ROOT/opt/ffmpeg-zimg/bin:}$ROOT/npm/node_modules/.bin:$ROOT/bin:\$PATH"
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
  echo "依赖根目录：$ROOT（平台 $OS_KIND）"
  echo "系统依赖："
  # cap: check:ffmpeg check:ffprobe check:node check:npm check:python check:sips
  for b in ffmpeg ffprobe node npm python3 sips; do
    printf '  %-8s ' "$b"
    # sips 是 macOS 的 ImageIO CLI，别的平台没有——报「不适用」，不是报「缺失」
    if [ "$b" = sips ] && [ "$OS_KIND" != darwin ]; then
      echo "不适用（macOS 专属）"
      continue
    fi
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
    # cap: check:ffmpeg-zimg
    echo "自编 ffmpeg（HDR→SDR 的 zscale）："
    if out=$("$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" -version 2>&1); then
      printf '  ok   %s\n' "$(echo "$out" | head -1 | cut -c1-64)"
      miss=""
      # 动态库检查按平台分叉：macOS 用 otool，Linux 用 ldd，其它平台没有等价工具就跳过并说明
      case "$OS_KIND" in
        darwin)
          for lib in $(otool -L "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" 2>/dev/null \
                       | awk 'NR>1{print $1}' | grep -E '^/(opt/homebrew|usr/local)'); do
            [ -e "$lib" ] || miss="$miss $(basename "$lib")"
          done ;;
        linux)
          miss=$(ldd "$ROOT/opt/ffmpeg-zimg/bin/ffmpeg" 2>/dev/null \
                 | awk '/not found/{print $1}' | tr '\n' ' ' | sed 's/^ *//; s/ *$//')
          miss=${miss:+ $miss} ;;
        *)
          echo "  · 本平台没有 otool/ldd 的等价检查，只跑了 -version" ;;
      esac
      if [ -n "$miss" ]; then
        echo "  ！动态库找不到：$miss"
        echo "    brew 把 formula 换掉了。重跑 bash scripts/runtime.sh install 重装缺的那几个。"
      fi
    else
      echo "  ！跑不起来：$(echo "$out" | head -2 | tr '\n' ' ')"
      echo "    修法：重跑 bash scripts/runtime.sh install。没有它，HDR 素材压出来偏亮偏灰。"
    fi
  fi
  # 前端运行时 gsap：2026-11-13 复核撞上——render_hf 里写死的 vendor 路径
  # （某个已删除的 skill 目录里的 vendor 副本）本机已不存在，新建项目走到这一步
  # 直接 SystemExit("缺 gsap")，而有 hf/assets/ 缓存的老项目完全看不出问题。
  echo "前端运行时（gsap）："
  # cap: check:gsap
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  gsap_hit=""
  for g in "$REPO/assets/gsap.min.js" "$REPO/demo/pack-micro/hf/assets/gsap.min.js" \
           "$HOME/.cola/skills/srt-motion-cards/assets/gsap.min.js" \
           "$HOME/.claude/skills/talking-head-recut/assets/vendor/gsap.min.js"; do
    [ -s "$g" ] && { gsap_hit="$g"; break; }
  done
  if [ -n "$gsap_hit" ]; then
    printf '  ok   %s\n' "$gsap_hit"
  else
    echo "  ！找不到 gsap.min.js——新建项目渲染会在拷资产这一步直接退出"
    echo "    修法：把任意一份 gsap.min.js 放到 $REPO/assets/gsap.min.js"
  fi
  # 转写引擎 coli：2026-11-13 T-023 实测过一次「整个 PATH 上都没有它」——
  # Cola 升级后不再把 @marswave/coli 暴露成系统命令，而报错只说「coli 不在 PATH」，
  # 没人知道受害面有多大。所以 check 里单独一段，明说在哪、能不能跑、模型在不在。
  echo "转写引擎（coli）："
  # cap: check:coli check:asr-model
  if command -v coli >/dev/null 2>&1; then
    coli_bin=$(command -v coli)
    printf '  ok   %s\n' "$coli_bin"
    # coli 自己没有 --version；版本从它旁边的 package.json 读
    # （软链指向 .../@marswave/coli/distribution/source/cli.js）
    coli_ver=$(python3 - "$coli_bin" <<'PY'
import json, os, pathlib, sys
p = pathlib.Path(os.path.realpath(sys.argv[1]))
for parent in [p.parent] + list(p.parents):
    pj = parent / "package.json"
    if not pj.is_file():
        continue
    try:
        d = json.loads(pj.read_text())
    except Exception:
        continue
    if d.get("name"):
        print(f"{d['name']} {d.get('version', '?')}")
        break
PY
)
    echo "       版本 ${coli_ver:-未知（不是 npm 包布局）}"
    # 与 ffmpeg 同理：清单里写着有 ≠ 跑得起来。真跑一次 --help。
    if out=$(coli --help 2>&1); then
      printf '  ok   真跑一次 --help：%s\n' "$(echo "$out" | head -1 | cut -c1-56)"
    else
      echo "  ！跑不起来：$(echo "$out" | head -2 | tr '\n' ' ')"
      echo "    受害面：selftest ②–⑤、test_hygiene_fixtures.py、新素材首次 ASR。"
    fi
    # 模型：coli 首跑会自己下 155.5 MB 到 ~/.coli/models（2026-11-13 实测断流过一次）
    sv="$HOME/.coli/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
    if [ -f "$sv/model.int8.onnx" ] && [ -f "$sv/tokens.txt" ]; then
      printf '  ok   模型 sensevoice 在位：%s\n' "$(du -sh "$sv" 2>/dev/null | cut -f1)"
    else
      echo "  · 模型 sensevoice 未装：首次 ASR 要下 155.5 MB（files.colaos.ai），断流会退 1"
    fi
  else
    echo "  缺失 —— 整条转写链路全靠它，缺了会连带："
    echo "    · selftest ②–⑤（预检直接退 2，不再往下假红）"
    echo "    · scripts/test_hygiene_fixtures.py（FileNotFoundError: 'coli'）"
    echo "    · 任何新素材库的首次 ASR（已有 ASR 缓存的素材不受影响）"
    echo "    恢复：`python3 scripts/fetch_coli.py`（从 Cola 安装包里重抽，不联网）"
  fi
  echo "node 包："
  # cap: check:npm-packages
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
  # cap: check:python-packages
  py=$(ls "$ROOT/py" 2>/dev/null | grep -v dist-info | head -20)
  [ -n "$py" ] && echo "$py" | sed 's/^/  /' || echo "  （空；随包 vendor 兜底：$HERE/vendor）"
  echo "占用："
  du -sh "$ROOT"/* 2>/dev/null | sed 's/^/  /'
  echo "总计：$(du -sh "$ROOT" 2>/dev/null | cut -f1)"
}

cmd_install() {
  ensure_manifest
  local changed=0
  # cap: install:npm-hyperframes
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
  # cap: install:pip-deps
  for name in $(dep_names pip); do
    spec=$(dep_spec pip "$name")
    [ -z "$spec" ] && continue
    if [ -d "$ROOT/py/$name" ] || ls "$ROOT/py"/${name}-*.dist-info >/dev/null 2>&1; then
      echo "  ✓ pip $name 已在 $ROOT/py"
      continue
    fi
    echo "  → 安装 pip $name"
    pip_ok=0
    pip3 install --quiet --target "$ROOT/py" "$name$spec" 2>/tmp/cutskills-pip.err && pip_ok=1
    if [ $pip_ok -eq 0 ]; then
      # SOCKS 代理环境缺 PySocks 会让 pip 直接 OSError——剥掉代理再试一次（直连能到 PyPI 就成）
      env -u all_proxy -u ALL_PROXY -u http_proxy -u HTTP_PROXY -u https_proxy -u HTTPS_PROXY \
           pip3 install --quiet --target "$ROOT/py" "$name$spec" 2>>/tmp/cutskills-pip.err && pip_ok=1
    fi
    if [ $pip_ok -eq 0 ]; then
      # pip 走不通。随包 vendor 里有就直接搬进来，
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

# Windows 原生（Git Bash / MSYS）上只放行 env / path / check / run：
# install / adopt 依赖 ln -s、otool、pip --target 的 POSIX 语义，在那边硬跑会装出半截环境。
if [ "$OS_KIND" = windows ]; then
  case "${1:-env}" in
    install|adopt)
      echo "✗ runtime.sh $1 只跑在 macOS / Linux / WSL 上（当前 uname: $(uname -s 2>/dev/null)）" >&2
      echo "  Windows 请改用 PowerShell：" >&2
      echo "    体检：powershell -ExecutionPolicy Bypass -File install.ps1 -CheckOnly" >&2
      echo "    安装：powershell -ExecutionPolicy Bypass -File install.ps1 -Install" >&2
      echo "  自检清单：%USERPROFILE%\\.cutskills\\windows-selfcheck.md（install.ps1 生成）" >&2
      exit 3 ;;
  esac
fi

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
