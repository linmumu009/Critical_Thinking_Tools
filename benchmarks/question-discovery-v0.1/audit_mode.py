from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODE_LABELS = {
    "1": "外部模型 API 自动审计（已冻结）",
    "2": "当前 Codex 直接处理（不调用外部 API）",
}


def choose_mode() -> str:
    print("请选择运行模式：")
    for mode in ("1", "2"):
        print(f"{mode}. {MODE_LABELS[mode]}")
    while True:
        choice = input("输入 1 或 2：").strip()
        if choice in MODE_LABELS:
            return choice
        print("无效选择，请输入 1 或 2。")


def mode_1_command(command: str, extra_args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(ROOT / "automated_mapping_audit.py"),
        command,
        *extra_args,
    ]


def mode_2_commands(command: str, extra_args: list[str]) -> list[list[str]]:
    script = str(ROOT / "codex_direct_audit.py")
    if command == "run":
        return [
            [sys.executable, "-B", script, "prepare", *extra_args],
            [sys.executable, "-B", script, "next", *extra_args],
        ]
    return [[sys.executable, "-B", script, "finalize", *extra_args]]


def dispatch(mode: str, command: str, extra_args: list[str]) -> list[list[str]]:
    if mode == "1":
        return [mode_1_command(command, extra_args)]
    if mode == "2":
        return mode_2_commands(command, extra_args)
    raise ValueError("mode must be 1 or 2")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Choose API automatic or Codex direct audit mode"
    )
    parser.add_argument("command", choices=("run", "finalize"))
    parser.add_argument("--mode", choices=("1", "2"))
    args, extra_args = parser.parse_known_args()
    mode = args.mode or choose_mode()
    print(f"MODE_SELECTED {mode}: {MODE_LABELS[mode]}", flush=True)
    for command in dispatch(mode, args.command, extra_args):
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode:
            return int(completed.returncode)
    if mode == "2" and args.command == "run":
        print(
            "CODEX_DIRECT_READY: 当前 Codex 应直接完成上方任务并通过 "
            "codex_direct_audit.py submit 锁定结果；不需要用户填写。",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
