# NoMount metamodule 验证 harness

这三套脚本用来证明 `nomount-module.yml`（用户态模块构建）+ 各内核 workflow 的
NoMount 接线**真的正确**，而不是"看一眼觉得对"。

设计原则：**每条"不许发生"的断言都配反证**——`mutate_*.py` 故意把工作流改坏，
对应的断言必须变红；某处改坏了却还全绿，那条断言就是装饰（没有牙齿）。

## 前置

- Windows + Git Bash（`C:\Program Files\Git\bin\bash.exe`）
- Python 3，且装了 `pyyaml`（`python -m pip install pyyaml`）
- 端到端打包验证可选：本机有 `zig` 就传路径，没有也能用 shim 跑通流程
  （只是不验"编译是否真成功"）

## 三个脚本

### 1. `verify_nomount_wiring.py` — 接线 / YAML / shell 合法性（快，~10s）

校验所有 workflow 的 YAML 可解析、每个 `run:` 块 `bash -n` 通过、NoMount 接线
（谁在什么条件下调用、release 是否带上模块、固定 commit 是否同源、Zig 版本写死）。

```bash
python scripts/nomount-verify/verify_nomount_wiring.py
```

### 2. `run_module_block.py` — 端到端打包（中，需要联网 clone NoMount）

把 `nomount-module.yml` 里"编译并打包 metamodule"那一段 `run:` 抠出来，在本机真跑：
正常打包、不存在的 SHA 必须非零退出、非尖端 SHA 能跑通、老 commit 缺目录结构必须
非零退出。四个反证用例。

```bash
# 有真 Zig：传可执行文件路径（会验产物是 ARM 静态可执行文件）
python scripts/nomount-verify/run_module_block.py "C:/path/to/zig.exe"
# 没 Zig：用 shim 顶上（只验流程，不验编译成败）
python scripts/nomount-verify/run_module_block.py SHIM
```

### 3. `mutate_nomount_wiring.py` — 变异测试（慢，必须后台跑，~5 分钟）

把 `.github/` 复制到临时目录，做 12 处故意改坏（去掉开关、改成分支头、退滚动
master、注入语法错误……），每处都必须让 `verify_nomount_wiring.py` 的红。

```bash
# 前台 2 分钟会被 SIGTERM 杀掉，务必放后台：
python scripts/nomount-verify/mutate_nomount_wiring.py
```

## 环境变量（覆盖默认值，方便换机器 / CI）

| 变量 | 含义 | 默认 |
|---|---|---|
| `SUSFS_ROOT` | 仓库根目录 | `D:\LuluProject\SUSFS\GKI_KernelSU_Sync_SUSFS` |
| `GIT_BASH` | Git Bash 可执行文件 | `C:\Program Files\Git\bin\bash.exe` |
| `PYTHON` | Python 解释器 | 当前用户的 managed venv |

> 注意：`mutate_*.py` 里的校验脚本路径取**兄弟文件**（`verify_nomount_wiring.py`
> 同目录），不用环境变量，所以这三个文件要放在一起。
