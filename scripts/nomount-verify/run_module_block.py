#!/usr/bin/env python3
"""把 nomount-module.yml 里「编译并打包 metamodule」那一段 run: 块抠出来，
在本机真跑一遍（Git Bash），验证每一步的实际行为。

为什么值得跑：这段脚本里有几个"看着没问题"的地方其实很容易挂 ——
  * `git fetch --depth=1 origin <sha>` 对非分支尖端的 SHA 能不能成功
  * `git checkout --detach <sha>`
  * `install -m 0755` 到不存在的 bin/ 会失败（所以必须先 mkdir -p）
  * `sed -i` 改 module.prop 的 versionCode
  * 失败时到底有没有非零退出（不是"看一眼觉得有"）

本机没有 zip，用一个走 python -m zipfile 的 shim 顶上（ubuntu runner 上是
原生 zip）。shim 必须由 bash 创建：Python 在 Windows 上写出来的文件没有
MSYS 的执行位，bash 会报 "command not found"（第一版就栽在这，见日志）。

用法:
  python run_module_block.py <zig 可执行文件路径>   # 有真 Zig 就用真 Zig
  python run_module_block.py SHIM                  # 否则用 zig shim
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.environ.get("SUSFS_ROOT", r"D:\LuluProject\SUSFS\GKI_KernelSU_Sync_SUSFS")
WF = os.path.join(ROOT, ".github", "workflows", "nomount-module.yml")
PIN_FILE = os.path.join(ROOT, ".github", "nomount-commit.txt")
BASH = os.environ.get("GIT_BASH", r"C:\Program Files\Git\bin\bash.exe")
PY = os.environ.get("PYTHON", r"C:\Users\lulu\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe")
PY_POSIX = PY.replace("\\", "/")

ZIG = sys.argv[1] if len(sys.argv) > 1 else "SHIM"


def posix(p):
    """Windows 路径 -> Git Bash 的 POSIX 路径。

    这一步不能省：在这个 Git Bash 里，PATH 里写 `C:/...` 是**不生效**的
    （实测 `PATH="C:/.../zig" zig version` → command not found，
    而 `/c/.../zig` 正常）。Python 通过环境变量传 PATH 时 MSYS 会帮忙转换，
    但在 bash 内部给 PATH 赋值不会转换，所以必须自己转。
    """
    p = os.path.abspath(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p):
        p = "/" + p[0].lower() + p[2:]
    return p


ZIG_POSIX = posix(os.path.dirname(ZIG)) if ZIG != "SHIM" else ""

# 非尖端 SHA（dev 与它分叉，落后 27 个 commit）：用来验证 --depth=1 按 SHA fetch
NON_TIP_SHA = "d0f57d5c37ff02aae2299daded57919153240cb3"
# v0.1.0：有 module/ 但没有 userspace/，用来验证"目录结构检查"真的会拦
OLD_LAYOUT_SHA = "443c03976e535b592a8a1c3986a1077dfa6b684b"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if detail else ""))


def read_pin():
    with open(PIN_FILE, "r", encoding="utf-8") as fh:
        for ln in fh:
            s = ln.strip()
            if s and not s.startswith("#"):
                return s
    raise SystemExit("pin 文件里没有有效行")


def extract_block():
    import yaml
    with open(WF, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    for st in doc["jobs"]["build-module"]["steps"]:
        if st.get("name") == "编译并打包 metamodule":
            return st["run"]
    raise SystemExit("找不到「编译并打包 metamodule」步骤")


WRAPPER = r"""#!/bin/bash
set -euo pipefail
SHIMDIR="$1"
mkdir -p "$SHIMDIR"

# zip shim：本机没有 zip。只支持本脚本用到的 `zip -qr <out> .`
cat > "$SHIMDIR/zip" <<'EOS'
#!/bin/sh
out=""
for a in "$@"; do
  case "$a" in
    -*) ;;
    *) out="$a"; break ;;
  esac
done
[ -n "$out" ] || { echo "zip shim: 没找到输出文件名" >&2; exit 2; }
exec "__PY__" -m zipfile -c "$out" .
EOS

# zig shim：产出假产物，但把 argv 记下来供断言
cat > "$SHIMDIR/zig" <<'EOS'
#!/bin/sh
echo "$@" >> "$ZIG_ARGV_LOG"
out=""
prev=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  prev="$a"
done
[ -n "$out" ] || { echo "zig shim: 命令行里没有 -o" >&2; exit 2; }
printf '\177ELF\002\001\001\000' > "$out"
printf 'FAKE-ZIG-OUTPUT\n' >> "$out"
exit 0
EOS

chmod +x "$SHIMDIR/zip" "$SHIMDIR/zig"

ZIGDIR="$2"
if [ -n "$ZIGDIR" ]; then
  export PATH="$ZIGDIR:$SHIMDIR:$PATH"
else
  export PATH="$SHIMDIR:$PATH"
fi

# ============================ 被测代码块开始 ============================
__BLOCK__
# ============================ 被测代码块结束 ============================
"""


def run_case(expected, tag):
    tmp = tempfile.mkdtemp(prefix=f"susfs-modblock-{tag}-")
    runner_temp = os.path.join(tmp, "runner")
    os.makedirs(runner_temp, exist_ok=True)
    goutput = os.path.join(tmp, "github_output")
    gsummary = os.path.join(tmp, "github_summary")
    open(goutput, "w").close()
    open(gsummary, "w").close()
    zig_log = os.path.join(tmp, "zig_argv.log")
    open(zig_log, "w").close()

    script = extract_block()
    script = re.sub(r"\$\{\{[^}]*\}\}", expected, script)

    wrapper = (
        WRAPPER
        .replace("__PY__", PY_POSIX)
        .replace("__BLOCK__", script)
    )
    wrapper_path = os.path.join(tmp, "wrapper.sh")
    with open(wrapper_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(wrapper)

    env = dict(os.environ)
    env.update({
        "RUNNER_TEMP": runner_temp.replace("\\", "/"),
        "GITHUB_OUTPUT": goutput.replace("\\", "/"),
        "GITHUB_STEP_SUMMARY": gsummary.replace("\\", "/"),
        "ZIG_ARGV_LOG": zig_log.replace("\\", "/"),
        "MSYS_NO_PATHCONV": "1",
    })

    proc = subprocess.run(
        [BASH, posix(wrapper_path), posix(os.path.join(tmp, "shims")), ZIG_POSIX],
        text=True, capture_output=True, encoding="utf-8", errors="replace",
        env=env, cwd=tmp,
    )
    return proc, tmp, runner_temp, goutput, zig_log


def show(proc, only_interesting=False):
    for tag, text in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        for ln in (text or "").splitlines():
            if only_interesting and not any(
                k in ln for k in ("::error", "缺少", "not found", "fatal", "NoMount")
            ):
                continue
            print(f"  | [{tag}] {ln}")


# =====================================================================
pin = read_pin()
print("=" * 74)
print(f"用例 A：pin 正确（Zig = {'shim' if ZIG == 'SHIM' else ZIG}）")
print(f"        pin = {pin}")
print("=" * 74)
proc, tmp, runner_temp, goutput, zig_log = run_case(pin, "ok")
show(proc)
check("退出码为 0", proc.returncode == 0, f"rc={proc.returncode}")

module_dir = os.path.join(runner_temp, "nomount-metamodule")
zip_path = os.path.join(runner_temp, "NoMount-Metamodule.zip")
check("产出 NoMount-Metamodule.zip", os.path.isfile(zip_path))

if os.path.isfile(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n.lstrip("./") for n in zf.namelist())
        print("  zip 内容: " + ", ".join(names))
        for want in ("module.prop", "bin/nm-arm64", "bin/nm-arm",
                     "NOMOUNT_SOURCE_COMMIT", "customize.sh",
                     "webroot/index.js", "metamount.sh"):
            check(f"zip 里有 {want}", any(n.endswith(want) for n in names))
        check("zip 里没有 .ko", not any(n.endswith(".ko") for n in names))
        check("zip 里没有 lkmloader",
              not any("lkmloader" in n or "ko-loader" in n for n in names))
        src = zf.read([n for n in zf.namelist()
                       if n.endswith("NOMOUNT_SOURCE_COMMIT")][0]).decode().strip()
        check("NOMOUNT_SOURCE_COMMIT == pin", src == pin, src)
        prop = zf.read([n for n in zf.namelist() if n.endswith("module.prop")][0]).decode()
        m = re.search(r"^versionCode=(.*)$", prop, re.M)
        check("module.prop 的 versionCode 填成 1",
              bool(m) and m.group(1).strip() == "1", repr(m.group(1) if m else None))

with open(goutput, "r", encoding="utf-8") as fh:
    kv = dict(ln.strip().split("=", 1) for ln in fh if "=" in ln)
check("GITHUB_OUTPUT 有 module_path", "module_path" in kv, str(kv))
check("GITHUB_OUTPUT 的 commit == pin", kv.get("commit") == pin, str(kv))

# ---- 编译产物类型（真 Zig 才验）----
out = (proc.stdout or "") + (proc.stderr or "")
if ZIG == "SHIM":
    with open(zig_log, "r", encoding="utf-8") as fh:
        argv_lines = [ln.strip() for ln in fh if ln.strip()]
    check("zig 被调用两次", len(argv_lines) == 2, str(len(argv_lines)))
    if len(argv_lines) == 2:
        a64, arm = argv_lines
        check("第一次 -target aarch64-linux", "-target aarch64-linux" in a64, a64[:70])
        check("第二次 -target arm-linux", "-target arm-linux" in arm, arm[:70])
    print("  注意：Zig 是 shim，编译是否成功没有验证")
else:
    check("nm-arm64 是 ARM aarch64 静态可执行文件",
          bool(re.search(r"nm-arm64: ELF 64-bit LSB executable, ARM aarch64.*statically linked", out)),
          next((l for l in out.splitlines() if "nm-arm64" in l), "")[:110])
    check("nm-arm 是 ARM 32 位静态可执行文件",
          bool(re.search(r"nm-arm:\s+ELF 32-bit LSB executable, ARM.*statically linked", out)),
          next((l for l in out.splitlines() if "nm-arm:" in l), "")[:110])

# =====================================================================
print()
print("=" * 74)
print("用例 B：pin 是不存在的 SHA —— 必须非零退出，不许带着错版本往下走（反证）")
print("=" * 74)
proc_b, tmp_b, *_ = run_case("0" * 40, "bad")
print(f"  退出码 {proc_b.returncode}")
show(proc_b, only_interesting=True)
check("不存在的 SHA → 非零退出", proc_b.returncode != 0, f"rc={proc_b.returncode}")

# =====================================================================
print()
print("=" * 74)
print(f"用例 C：pin 是非尖端 SHA {NON_TIP_SHA[:12]}…（--depth=1 按 SHA fetch）")
print("=" * 74)
proc_c, tmp_c, rt_c, go_c, _ = run_case(NON_TIP_SHA, "nontip")
print(f"  退出码 {proc_c.returncode}")
show(proc_c, only_interesting=True)
check("非尖端 SHA 也能跑通", proc_c.returncode == 0, f"rc={proc_c.returncode}")
with open(go_c, "r", encoding="utf-8") as fh:
    kv_c = dict(ln.strip().split("=", 1) for ln in fh if "=" in ln)
check("用例 C 记录的 commit 是该 SHA", kv_c.get("commit") == NON_TIP_SHA, str(kv_c))

# =====================================================================
print()
print("=" * 74)
print(f"用例 D：pin 指到 {OLD_LAYOUT_SHA[:12]}…（v0.1.0，没有 userspace/）")
print("=" * 74)
proc_d, tmp_d, *_ = run_case(OLD_LAYOUT_SHA, "oldlayout")
print(f"  退出码 {proc_d.returncode}")
show(proc_d, only_interesting=True)
check("目录结构不兼容 → 非零退出", proc_d.returncode != 0, f"rc={proc_d.returncode}")
check("错误信息点明缺哪个文件",
      "缺少 userspace/src/nm.c" in ((proc_d.stdout or "") + (proc_d.stderr or "")))

# =====================================================================
print()
print("=" * 74)
failed = [r for r in results if not r[1]]
print(f"共 {len(results)} 条断言，{len(failed)} 条失败")
for name, _, detail in failed:
    print("  FAIL " + name + "  " + detail)
print("=" * 74)

for d in (tmp, tmp_b, tmp_c, tmp_d):
    shutil.rmtree(d, ignore_errors=True)
sys.exit(1 if failed else 0)
