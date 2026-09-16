#!/usr/bin/env python3
"""检查 WildKernels 上游有哪些本项目尚未引入的功能与补丁。

只读操作 —— 不修改任何文件，只打印一份报告。

用法:
    python scripts/check_upstream.py

用途:
    上游（WildKernels/GKI_KernelSU_SUSFS）与本仓库在 2025-03 就已分叉，
    git merge 已不可行。但本项目的构建会在 CI 时实时 clone 上游的补丁仓库
    （WildKernels/kernel_patches 等），所以「补丁内容」是自动同步的，
    真正需要人工跟进的是「调用补丁的 workflow 代码」。

    这个脚本回答两个问题：
      1. 上游新增了哪些 action / 补丁，而我还没用上？
      2. 上游的补丁清单里，我引用了哪些、漏了哪些？

退出码: 0 正常 / 1 取不到上游数据（网络问题）
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "WildKernels/GKI_KernelSU_SUSFS"
PATCHES_REPO = "WildKernels/kernel_patches"

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
LOCAL_ACTIONS = ROOT / ".github" / "actions"

# 上游 action -> 本项目已用什么方式实现（人工维护，随移植进度更新）
IMPLEMENTED = {
    "bbrv3": "use_bbrv3（内联在 build.yml）",
    "nomount": "use_nomount（内联在 build.yml）",
    "susfs": "enable_susfs（本仓库自研，比上游集成更深）",
    "susfs-setup": "enable_susfs",
    "susfs-patches": "enable_susfs",
    "susfs-config": "enable_susfs",
    "susfs-revert-patches": "enable_susfs",
    "bbg": "use_bbg",
    "droidspaces": "droidspaces / droidspaces_ntsync",
    "ntsync": "droidspaces_ntsync（走 droidspaces 体系）",
    "unicode-fix": "应用 Unicode 绕过修复（自研，走 Numbersf/Action-Build）",
    "apply-device-patches": "修复 6.6 WiFi/蓝牙兼容性（自研）",
    "kernel-fixes": "部分内联（glibc 兼容 / 一加 8E 支持）",
    "remove-protected-exports": "部分内联（CVE 修复链）",
    "fetch-root-managers": "确定 KernelSU 分支（自研，支持 4 种变体）",
    "build-kernel": "编译内核（自研）",
    "download-kernel": "初始化并同步内核源码（自研）",
}

# 与上游架构绑定、对本项目无意义的 action（不参与对比）
NOT_APPLICABLE = {
    "cache-archive",
    "cache-bazel-setup",
    "cache-ccache-setup",
    "cache-ccache-stats",
    "cache-folder-stats",
    "cache-restore",
    "cache-save",
    "cache-setup",
    "cache-stats",
    "cache-upload",
    "clean-kernel-flags",
    "disk-cleanup",
    "extract-sublevel-file-name",
    "misc",
    "retry",
    "root-setup",
    "scan-patch-rejects",
    "set-kernel-config",
    "setup-build-environment",
    "apply-kernel-branding",
    "networking-config",
}

def api(url):
    req = urllib.request.Request(url, headers={"User-Agent": "check-upstream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def list_dir(repo, path):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        data = api(url)
    except urllib.error.HTTPError as e:
        print(f"  [警告] 取不到 {repo}/{path} — HTTP {e.code}", file=sys.stderr)
        raise
    return data if isinstance(data, list) else []


def fetch_upstream_patches():
    """递归拉取 kernel_patches 的 common/ 下所有 .patch，按所在目录分组。

    不能只看 common/ 根目录 —— bbrv3/、ntsync/、bbg/、droidspaces/ 等
    子目录里的补丁同样重要（BBRv3 就在 common/bbrv3/ 下）。
    """
    info = api(f"https://api.github.com/repos/{PATCHES_REPO}")
    branch = info.get("default_branch", "main")
    tree = api(
        f"https://api.github.com/repos/{PATCHES_REPO}/git/trees/{branch}?recursive=1"
    )
    groups = {}
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path.startswith("common/") or not path.endswith(".patch"):
            continue
        rel = path[len("common/") :]
        group = "（common 根目录）" if "/" not in rel else rel.split("/")[0] + "/"
        groups.setdefault(group, []).append(rel)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def collect_local_workflow_text():
    """把所有 workflow 的文本拼起来用于匹配。

    不用「提取 $KERNEL_PATCHES/... 引用」的方式 —— 有些补丁路径是拼在
    变量里的（例如 BBRv3 写成 BBRV3_DIR="$KERNEL_PATCHES/common/bbrv3"，
    补丁文件名单独写在 case 分支里），按前缀提取会漏掉。
    直接全文匹配补丁文件名更可靠。
    """
    parts = []
    if not WORKFLOWS.is_dir():
        return ""
    for f in sorted(WORKFLOWS.glob("*.yml")):
        parts.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def main():
    print("=" * 64)
    print("WildKernels 上游变更检查")
    print("=" * 64)

    try:
        print("\n[1/3] 拉取上游 action 清单 ...")
        up_actions = sorted(
            i["name"] for i in list_dir(UPSTREAM_REPO, ".github/actions") if i["type"] == "dir"
        )

        print("[2/3] 拉取上游补丁清单 ...")
        up_patch_groups = fetch_upstream_patches()
    except urllib.error.URLError as e:
        print(f"\n取不到上游数据（网络问题）: {e}", file=sys.stderr)
        print("如果本机开了代理，GitHub API 应该能通；否则稍后重试。", file=sys.stderr)
        return 1

    print("[3/3] 扫描本地引用 ...")
    local_text = collect_local_workflow_text()
    local_action_dirs = (
        sorted(p.name for p in LOCAL_ACTIONS.iterdir() if p.is_dir())
        if LOCAL_ACTIONS.is_dir()
        else []
    )

    # ---------- action 对比 ----------
    print("\n" + "=" * 64)
    print(f"一、上游 action（{len(up_actions)} 个）")
    print("=" * 64)

    done, missing, na = [], [], []
    for a in up_actions:
        if a in local_action_dirs:
            done.append((a, "已搬到 .github/actions/"))
        elif a in IMPLEMENTED:
            done.append((a, IMPLEMENTED[a]))
        elif a in NOT_APPLICABLE:
            na.append(a)
        else:
            missing.append(a)

    print(f"\n  已覆盖 {len(done)} 个：")
    for a, how in done:
        print(f"    ✓ {a:26s} {how}")

    print(f"\n  与上游架构绑定、本项目不适用 {len(na)} 个：")
    print(f"    - {', '.join(na) if na else '（无）'}")

    print(f"\n  上游有、本项目未引入 {len(missing)} 个：")
    if missing:
        for a in missing:
            print(f"    ✗ {a}")
    else:
        print("    （无）")

    # ---------- 补丁对比 ----------
    total = sum(len(v) for v in up_patch_groups.values())
    print("\n" + "=" * 64)
    print(f"二、上游 common/ 下的补丁（{total} 个，按目录分组）")
    print("=" * 64)
    print("\n  ✓ = 本仓库 workflow 已引用    · = 未引用（不引用 ≠ 该启用）")

    for group, patches in up_patch_groups.items():
        hit = [p for p in patches if p.split("/")[-1] in local_text]
        print(f"\n  {group}  共 {len(patches)} 个，已引用 {len(hit)} 个")
        for p in patches:
            mark = "✓" if p.split("/")[-1] in local_text else "·"
            print(f"    {mark} {p}")

    print("\n" + "=" * 64)
    print("说明")
    print("=" * 64)
    print("""
  本项目的构建会在 CI 时实时 clone WildKernels/kernel_patches，
  所以上面「未被引用」的补丁正文其实每次构建都已下载到工作区，
  只是没有被 patch 命令调用。要启用某个补丁，只需在 build.yml 里
  加一步 patch -p1 < "$KERNEL_PATCHES/common/<name>.patch"。

  补丁正文是自动同步的；需要人工跟进的只有「调用它们的代码」。

  另外提醒：CI 里 clone 这些仓库时没有 pin commit，
  上游改动会直接影响下次构建（构建不可复现）。
  可参考 config/config 对 SUSFS/SukiSU 的做法，把关键仓库也 pin 住。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
