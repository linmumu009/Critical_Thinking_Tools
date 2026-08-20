from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODE_SCRIPTS = {
    "1": ROOT / "automated_mapping_audit.py",
    "2": ROOT / "automated_mapping_audit_mode_2.py",
}
MODE_LABELS = {
    "1": "双条件盲评审 + 分歧自动仲裁（已冻结）",
    "2": "问题契约提取 + 覆盖证明 + 反例验证（独立）",
}


def choose_mode() -> str:
    print("请选择 API 审计模式：")
    for mode in ("1", "2"):
        print(f"{mode}. {MODE_LABELS[mode]}")
    while True:
        choice = input("输入 1 或 2：").strip()
        if choice in MODE_SCRIPTS:
            return choice
        print("无效选择，请输入 1 或 2。")


def dispatch_command(mode: str, command: str, extra_args: list[str]) -> list[str]:
    if mode not in MODE_SCRIPTS:
        raise ValueError("mode must be 1 or 2")
    return [sys.executable, "-B", str(MODE_SCRIPTS[mode]), command, *extra_args]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Choose one of two isolated API audit modes"
    )
    parser.add_argument("command", choices=("run", "finalize"))
    parser.add_argument("--mode", choices=("1", "2"))
    args, extra_args = parser.parse_known_args()
    mode = args.mode or choose_mode()
    print(f"API_MODE_SELECTED {mode}: {MODE_LABELS[mode]}", flush=True)
    completed = subprocess.run(
        dispatch_command(mode, args.command, extra_args), cwd=ROOT, check=False
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
