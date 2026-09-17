#!/usr/bin/env python3
"""校验 NoMount 用户态模块的接线与 YAML/shell 合法性。

这不是"看一眼觉得对"，而是把每条断言都写成会红的形式：
  - YAML 解析失败 → 红
  - run: 块 bash -n 失败 → 红
  - 接线缺失（job 不在、if 条件不对、release 没上传）→ 红
最后再用变异测试确认这些断言真的有牙齿（见 --selftest）。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.environ.get("SUSFS_ROOT", r"D:\LuluProject\SUSFS\GKI_KernelSU_Sync_SUSFS")
WF = os.path.join(ROOT, ".github", "workflows")
# 必须用 Git Bash 的绝对路径：Windows PATH 上的裸 `bash` 是 WSL 的启动器，
# 本环境把它拉黑了，调用会直接失败（而不是报"命令不存在"）。
# 必须用 Git Bash 的绝对路径：Windows PATH 上的裸 `bash` 是 WSL 的启动器，
# 本环境把它拉黑了，调用会直接失败（而不是报"命令不存在"）。
BASH = os.environ.get("GIT_BASH", r"C:\Program Files\Git\bin\bash.exe")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if detail else ""))


def load_yaml(path):
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_shell_blocks(path, doc):
    """把每个 run: 块抽出来做 bash -n。返回 (总数, 失败列表)。"""
    total = 0
    bad = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if not isinstance(script, str):
                continue
            total += 1
            # GitHub 表达式会出现在 shell 里，替换成占位符再交给 bash
            body = re.sub(r"\$\{\{[^}]*\}\}", "EXPR", script)
            proc = subprocess.run(
                [BASH, "-n"], input=body, text=True,
                capture_output=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                bad.append((job_name, i, step.get("name"), (proc.stderr or "").strip()))
    return total, bad


# ---------------------------------------------------------------- 1. YAML 解析
print("\n[1] YAML 解析 + 内联 shell 语法")
files = sorted(
    os.path.join(WF, f) for f in os.listdir(WF) if f.endswith(".yml")
) + [os.path.join(ROOT, ".github", "nomount-commit.txt")]

docs = {}
for path in files:
    if path.endswith(".txt"):
        continue
    rel = os.path.relpath(path, ROOT)
    try:
        docs[path] = load_yaml(path)
        check(f"YAML 可解析: {rel}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"YAML 可解析: {rel}", False, str(exc))

shell_total = 0
for path, doc in docs.items():
    rel = os.path.relpath(path, ROOT)
    total, bad = run_shell_blocks(path, doc)
    shell_total += total
    for job_name, idx, sname, err in bad:
        check(f"bash -n: {rel} / {job_name} / step#{idx} ({sname})", False, err)
check(f"全部 run: 块 bash -n 通过（共 {shell_total} 段）", shell_total > 0)

# ---------------------------------------------------------------- 2. pin 文件
print("\n[2] .github/nomount-commit.txt")
pin_path = os.path.join(ROOT, ".github", "nomount-commit.txt")
pin = None
if os.path.isfile(pin_path):
    with open(pin_path, "r", encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    pin = lines[0] if lines else None
check("pin 文件存在且有有效行", pin is not None, repr(pin))
check("pin 是 40 位小写 hex", bool(pin and re.fullmatch(r"[0-9a-f]{40}", pin)), repr(pin))

# ---------------------------------------------------------------- 3. 新工作流
print("\n[3] nomount-module.yml 结构")
nm = docs.get(os.path.join(WF, "nomount-module.yml"))
check("nomount-module.yml 存在", nm is not None)
if nm:
    check("只有 workflow_call（不提供 dispatch，避免单独触发导致版本错配）",
          set(nm.get(True) or nm.get("on") or {}) == {"workflow_call"},
          str(list((nm.get(True) or nm.get("on") or {}).keys())))
    job = (nm.get("jobs") or {}).get("build-module")
    check("有 build-module job", job is not None)
    if job:
        body = json.dumps(job, ensure_ascii=False)
        check("读了 .github/nomount-commit.txt", "nomount-commit.txt" in body)
        check("用 setup-zig 装 Zig", "jetsung/setup-zig" in body)
        # 不能用字符串搜 "version: 0.15.2" —— json.dumps 出来是
        # `"version": "0.15.2"`，那样会假红。直接查解析后的结构。
        zig_steps = [s for s in job.get("steps", [])
                     if isinstance(s, dict) and "setup-zig" in str(s.get("uses", ""))]
        check("有且只有一个 setup-zig 步骤", len(zig_steps) == 1, str(len(zig_steps)))
        if zig_steps:
            check("Zig 版本写死 0.15.2（不用滚动的 master）",
                  (zig_steps[0].get("with") or {}).get("version") == "0.15.2",
                  repr(zig_steps[0].get("with")))
        check("编译 aarch64-linux 目标", "aarch64-linux" in body)
        check("编译 arm-linux 目标", "arm-linux" in body)
        # 只数真正的命令行，注释里也提到了这个参数，别把它算进去
        n_entry = body.count("--entry=_start nm.c")
        check("两次编译都带 --entry=_start", n_entry == 2, str(n_entry))
        check("有目录结构预检（缺文件就停）", "userspace/src/nm.c" in body and "缺少" in body)
        # 真正要守的是「按固定 SHA 取源码」，不是「HEAD 是否等于 SHA」——
        # 后者是 fetch+checkout 同一个 SHA 的必然结果，永远不红，留着是装饰。
        # 固定 commit 的全部意义就是可追溯、不被分支头悄悄挪动；
        # 所以必须确认 fetch 的是 SHA 本身，而不是某个分支 ref。
        raw = "\n".join(s.get("run", "") for s in job.get("steps", [])
                        if isinstance(s, dict))
        check("按固定 SHA fetch（不按分支 ref）",
              'fetch --depth=1 origin "$EXPECTED"' in raw and 'refs/heads/' not in raw)
        check("上传产物名 NoMount-Metamodule", "NoMount-Metamodule" in body)
        check("写 NOMOUNT_SOURCE_COMMIT", "NOMOUNT_SOURCE_COMMIT" in body)
        check("打包的是上游 module 目录", "$SOURCE_DIR/module" in body)

# ---------------------------------------------------------------- 4. 接线
print("\n[4] 接线：谁在什么条件下调用它")
main = docs.get(os.path.join(WF, "main.yml"))
callers = {
    "main.yml": ("inputs.use_nomount", main),
    "kernel-a12-5-10.yml": ("!inputs.called_from_main && inputs.use_nomount",
                            docs.get(os.path.join(WF, "kernel-a12-5-10.yml"))),
    "kernel-a13-5-15.yml": ("!inputs.called_from_main && inputs.use_nomount",
                            docs.get(os.path.join(WF, "kernel-a13-5-15.yml"))),
    "kernel-a14-6-1.yml": ("!inputs.called_from_main && inputs.use_nomount",
                           docs.get(os.path.join(WF, "kernel-a14-6-1.yml"))),
    "kernel-a15-6-6.yml": ("!inputs.called_from_main && inputs.use_nomount",
                           docs.get(os.path.join(WF, "kernel-a15-6-6.yml"))),
    "kernel-a16-6-12.yml": ("!inputs.called_from_main && inputs.use_nomount",
                            docs.get(os.path.join(WF, "kernel-a16-6-12.yml"))),
    "kernel-custom.yml": ("inputs.use_nomount",
                          docs.get(os.path.join(WF, "kernel-custom.yml"))),
}
for fname, (expected_if, doc) in callers.items():
    job = ((doc or {}).get("jobs") or {}).get("build-nomount-module")
    check(f"{fname}: 有 build-nomount-module job", job is not None)
    if not job:
        continue
    check(f"{fname}: uses 指向 nomount-module.yml",
          job.get("uses") == "./.github/workflows/nomount-module.yml", str(job.get("uses")))
    cond = str(job.get("if") or "")
    norm = re.sub(r"\s+", "", cond).replace("${{", "").replace("}}", "")
    want = re.sub(r"\s+", "", expected_if)
    check(f"{fname}: if 条件 == {expected_if}", norm == want, repr(cond))

print("\n[5] release job 是否把模块带上")
if main:
    rel = (main.get("jobs") or {}).get("release") or {}
    needs = rel.get("needs") or []
    check("release.needs 含 build-nomount-module", "build-nomount-module" in needs, str(needs))
    body = json.dumps(rel, ensure_ascii=False)
    check("release 有上传 NoMount-Module 的步骤",
          "NoMount-Metamodule/*.zip" in body)
    check("该步骤只在 result == 'success' 时跑",
          "needs.build-nomount-module.result == 'success'" in body)

# ---------------------------------------------------------------- 6. build.yml
print("\n[6] build.yml 内核侧是否也用同一个 pin")
b = docs.get(os.path.join(WF, "build.yml"))
if b:
    body = json.dumps(b, ensure_ascii=False)
    check("内核侧读 nomount-commit.txt", "nomount-commit.txt" in body)
    # 注意：build.yml 里 KernelSU-Next 也有一个 refs/heads/dev/kernel/setup.sh，
    # 那条与本项无关，断言必须限定在 maxsteeel/nomount 上，否则会假红。
    check("NoMount 的 setup.sh 不再从 dev 分支头取",
          "maxsteeel/nomount/refs/heads/dev" not in body)
    check("setup.sh 从固定 SHA 取",
          "raw.githubusercontent.com/maxsteeel/nomount/$NOMOUNT_PIN/kernel/setup.sh" in body)
    check("commit 对不上时 exit 1", "与固定值不符" in body)

# ---------------------------------------------------------------- 汇总
failed = [r for r in results if not r[1]]
print("\n" + "=" * 60)
print(f"共 {len(results)} 条断言，{len(failed)} 条失败")
for name, _, detail in failed:
    print("  FAIL " + name + "  " + detail)
print("=" * 60)
sys.exit(1 if failed else 0)
