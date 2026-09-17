#!/usr/bin/env python3
"""对 verify_nomount_wiring.py 做变异测试。

目的不是"证明断言有效"，而是"证明哪些断言无效"：每一处故意改坏，
都必须让对应的断言变红；如果某处改坏了却还是全绿，那条断言就是装饰。

做法：把 .github/ 复制到临时目录，在副本上动手，再让校验脚本跑副本。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

SUSFS_ROOT = os.environ.get("SUSFS_ROOT", r"D:\LuluProject\SUSFS\GKI_KernelSU_Sync_SUSFS")
SRC = os.path.join(SUSFS_ROOT, ".github")
PY = os.environ.get("PYTHON", r"C:\Users\lulu\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe")
# 校验脚本就放在本目录里，用兄弟文件路径，不写死临时目录
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_nomount_wiring.py")

WF = os.path.join(".github", "workflows")


def read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def write(root, rel, text):
    with open(os.path.join(root, rel), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run_harness(root):
    env = dict(os.environ, SUSFS_ROOT=root)
    proc = subprocess.run(
        [PY, HARNESS], capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    fails = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("FAIL")]
    return proc.returncode, fails, proc.stdout or ""


# 每个变异：(名字, 函数(root) 就地改坏, 期望哪条断言变红的关键词)
def m_drop_guard(root):
    p = os.path.join(WF, "main.yml")
    t = read(root, p)
    t = t.replace("""  build-nomount-module:
    if: inputs.use_nomount
    uses: ./.github/workflows/nomount-module.yml""",
                  """  build-nomount-module:
    uses: ./.github/workflows/nomount-module.yml""", 1)
    write(root, p, t)


def m_drop_kernel_guard(root):
    p = os.path.join(WF, "kernel-a16-6-12.yml")
    t = read(root, p)
    t = t.replace("if: ${{ !inputs.called_from_main && inputs.use_nomount }}\n    uses: ./.github/workflows/nomount-module.yml",
                  "uses: ./.github/workflows/nomount-module.yml", 1)
    write(root, p, t)


def m_bad_pin(root):
    write(root, os.path.join(".github", "nomount-commit.txt"), "# c\n\ndeadbeef\n")


def m_drop_release_needs(root):
    p = os.path.join(WF, "main.yml")
    t = read(root, p)
    t = t.replace("      - build-nomount-module\n", "", 1)
    write(root, p, t)


def m_release_upload_ungated(root):
    p = os.path.join(WF, "main.yml")
    t = read(root, p)
    t = t.replace("if: needs.build-nomount-module.result == 'success'\n        continue-on-error: true\n        run: |\n          uploaded=0\n          for file in ./downloaded-artifacts/NoMount-Metamodule/*.zip",
                  "continue-on-error: true\n        run: |\n          uploaded=0\n          for file in ./downloaded-artifacts/NoMount-Metamodule/*.zip", 1)
    write(root, p, t)


def m_break_shell(root):
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    # 注入一个语法错误（未闭合的 if）
    t = t.replace('echo "NoMount 固定 commit: $SHA"',
                  'if [ -n "$SHA" ]; then\n            echo "NoMount 固定 commit: $SHA"', 1)
    write(root, p, t)


def m_wrong_target(root):
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    t = t.replace("-target arm-linux", "-target riscv64-linux", 1)
    write(root, p, t)


def m_drop_presence_check(root):
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    t = t.replace("""          for REL in userspace/src/nm.c userspace/src/nm.h \\
                     module/customize.sh module/module.prop; do
            if [ ! -f "$SOURCE_DIR/$REL" ]; then
              echo "::error::NoMount $ACTUAL 里缺少 $REL —— pin 可能指到了不兼容的 commit"
              exit 1
            fi
          done
""", "", 1)
    write(root, p, t)


def m_zig_master(root):
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    t = t.replace("version: 0.15.2", "version: master", 1)
    write(root, p, t)


def m_fetch_by_branch(root):
    # 把「按固定 SHA fetch」改成「按分支头 fetch」——固定 commit 的意义就是
    # 不被分支头移动影响，这个回归必须被抓到。
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    t = t.replace('git -C "$SOURCE_DIR" fetch --depth=1 origin "$EXPECTED"',
                  'git -C "$SOURCE_DIR" fetch --depth=1 origin "refs/heads/main"', 1)
    write(root, p, t)


def m_kernel_revert_to_dev(root):
    p = os.path.join(WF, "build.yml")
    t = read(root, p)
    t = t.replace("https://raw.githubusercontent.com/maxsteeel/nomount/$NOMOUNT_PIN/kernel/setup.sh",
                  "https://raw.githubusercontent.com/maxsteeel/nomount/refs/heads/dev/kernel/setup.sh", 1)
    write(root, p, t)


def m_drop_dispatch_guard(root):
    p = os.path.join(WF, "nomount-module.yml")
    t = read(root, p)
    t = t.replace("on:\n  workflow_call:\n", "on:\n  workflow_dispatch:\n  workflow_call:\n", 1)
    write(root, p, t)


MUTATIONS = [
    ("main.yml 去掉 use_nomount 开关", m_drop_guard, "main.yml: if 条件"),
    ("kernel-a16 去掉 called_from_main 防重复", m_drop_kernel_guard, "kernel-a16-6-12.yml: if 条件"),
    ("pin 文件写成非法值", m_bad_pin, "pin 是 40 位小写 hex"),
    ("release.needs 漏掉 build-nomount-module", m_drop_release_needs, "release.needs 含"),
    ("release 上传步骤去掉 result 门禁", m_release_upload_ungated, "该步骤只在 result"),
    ("nomount-module.yml 注入 shell 语法错误", m_break_shell, "bash -n"),
    ("arm 目标改成 riscv64", m_wrong_target, "编译 arm-linux 目标"),
    ("去掉目录结构预检", m_drop_presence_check, "目录结构预检"),
    ("Zig 版本退回滚动的 master", m_zig_master, "Zig 版本写死"),
    ("fetch 改成按分支头而非固定 SHA", m_fetch_by_branch, "按固定 SHA fetch"),
    ("内核侧 setup.sh 退回 dev 分支头", m_kernel_revert_to_dev, "setup.sh 从固定 SHA 取"),
    ("nomount-module.yml 多开一个 dispatch 入口", m_drop_dispatch_guard, "只有 workflow_call"),
]

print("=" * 72)
print("基线（未变异）")
print("=" * 72)
base = tempfile.mkdtemp(prefix="susfs-base-")
shutil.copytree(SRC, os.path.join(base, ".github"))
rc, fails, out = run_harness(base)
print(f"  退出码 {rc}，失败 {len(fails)} 条")
if rc != 0:
    print("基线就不是绿的，变异测试没有意义：")
    print(out)
    sys.exit(2)

print()
print("=" * 72)
print("变异测试")
print("=" * 72)
bad = []
for name, fn, expect in MUTATIONS:
    d = tempfile.mkdtemp(prefix="susfs-mut-")
    shutil.copytree(SRC, os.path.join(d, ".github"))
    fn(d)
    rc, fails, out = run_harness(d)
    hit = any(expect in f for f in fails)
    caught = rc != 0 and hit
    print(f"  {'抓到' if caught else '漏了'}  {name}")
    print(f"          期望红: {expect}")
    if not caught:
        print(f"          实际失败 {len(fails)} 条: {fails[:5]}")
        bad.append(name)
    shutil.rmtree(d, ignore_errors=True)

shutil.rmtree(base, ignore_errors=True)

print()
print("=" * 72)
if bad:
    print(f"{len(bad)} 处变异没被抓到（这些断言没有牙齿）：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
print(f"全部 {len(MUTATIONS)} 处变异都被对应断言抓到")
sys.exit(0)
