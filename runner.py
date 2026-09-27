#!/usr/bin/env python3

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


# ============================================================
# 設定
# ============================================================

# runner.py が置かれているフォルダ
BASE_DIR = Path(__file__).resolve().parent

# タスクファイルを格納するフォルダ
TASKS_DIR = BASE_DIR / "tasks"

# タスクファイル名:
#   001_title.yaml
#   002_title.yml
#   003_任意のタイトル.yaml
TASK_FILE_PATTERN = re.compile(r"^(\d+)_.*\.ya?ml$", re.IGNORECASE)


# ============================================================
# Claude Codeの検索
# ============================================================

def find_claude():
    """
    PATHからclaudeコマンドを検索する。
    Node.jsのバージョンやインストール先は決め打ちしない。
    """
    claude = shutil.which("claude")

    if claude is None:
        raise RuntimeError(
            "PATHからClaude Codeの 'claude' コマンドが見つかりません。\n"
            "WSL2のターミナルで 'which claude' を実行して確認してください。"
        )

    return claude


# ============================================================
# タスクファイル取得
# ============================================================

def get_task_files():
    """
    tasksフォルダからタスクファイルを取得し、
    ファイル名先頭の番号順に並べる。
    """

    if not TASKS_DIR.is_dir():
        raise RuntimeError(
            f"tasksフォルダが存在しません: {TASKS_DIR}"
        )

    tasks = []

    for path in TASKS_DIR.iterdir():

        if not path.is_file():
            continue

        match = TASK_FILE_PATTERN.match(path.name)

        if not match:
            continue

        number = int(match.group(1))

        tasks.append((number, path))

    # 番号順
    tasks.sort(key=lambda item: item[0])

    return [path for _, path in tasks]


# ============================================================
# タスクファイル読み込み
# ============================================================

def load_task(task_file):
    """
    YAML形式のタスクファイルを読み込む。
    """

    try:
        with task_file.open("r", encoding="utf-8") as f:
            task = yaml.safe_load(f)

    except yaml.YAMLError as e:
        raise RuntimeError(
            f"{task_file.name}: YAMLの解析に失敗しました: {e}"
        ) from e

    if not isinstance(task, dict):
        raise RuntimeError(
            f"{task_file.name}: YAMLの内容がオブジェクト形式ではありません。"
        )

    # 必須項目
    required_fields = (
        "directory",
        "model",
        "prompt",
    )

    for field in required_fields:
        if field not in task:
            raise RuntimeError(
                f"{task_file.name}: 必須項目 '{field}' がありません。"
            )

    # 空値チェック
    for field in required_fields:
        if task[field] is None or str(task[field]).strip() == "":
            raise RuntimeError(
                f"{task_file.name}: '{field}' が空です。"
            )

    return task


# ============================================================
# Claude Code実行
# ============================================================

def run_claude(claude, task_file, task):
    """
    1つのタスクをClaude Codeで実行する。

    Claude Codeの標準出力・標準エラーは、
    そのままPythonを実行しているターミナルに表示する。
    """

    directory = Path(
        str(task["directory"])
    ).expanduser()

    model = str(task["model"])
    prompt = str(task["prompt"])

    print()
    print("=" * 70)
    print(f"タスクファイル       : {task_file.name}")
    print(f"モデル               : {model}")
    print(f"作業ディレクトリ     : {directory}")
    print("=" * 70)
    print()

    # 作業ディレクトリ確認
    if not directory.is_dir():
        raise RuntimeError(
            f"作業ディレクトリが存在しません: {directory}"
        )

    # Claude Codeコマンド
    command = [
        claude,
        "--model",
        model,
        "-p",
        prompt,
    ]

    print(f"$ {' '.join(command[:3])} ...")
    print()

    # Claude Codeの出力をそのままターミナルへ流す
    result = subprocess.run(
        command,
        cwd=directory,
    )

    print()

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude Codeが終了コード "
            f"{result.returncode} で終了しました。"
        )

    print(
        f"タスク完了: {task_file.name}"
    )


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 70)
    print("Claude Code Workflow Runner")
    print("=" * 70)

    print(f"Runner : {BASE_DIR}")
    print(f"Tasks  : {TASKS_DIR}")
    print()

    # Claude Code確認
    try:
        claude = find_claude()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Claude : {claude}")

    # タスク取得
    try:
        task_files = get_task_files()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not task_files:
        print()
        print("実行するタスクファイルがありません。")
        return 0

    print(f"Tasks  : {len(task_files)}")

    print()
    print("実行順:")
    for index, task_file in enumerate(task_files, start=1):
        print(f"  {index}. {task_file.name}")

    # ========================================================
    # タスクを順番に実行
    # ========================================================

    for index, task_file in enumerate(task_files, start=1):

        print()
        print("#" * 70)
        print(
            f"# TASK {index}/{len(task_files)}: "
            f"{task_file.name}"
        )
        print("#" * 70)

        try:
            task = load_task(task_file)

            run_claude(
                claude,
                task_file,
                task,
            )

        except Exception as e:
            print()
            print("=" * 70)
            print("ERROR")
            print("=" * 70)
            print(f"タスク: {task_file.name}")
            print(f"内容  : {e}")
            print()
            print("ワークフローを停止します。")

            return 1

    # ========================================================
    # 全工程完了
    # ========================================================

    print()
    print("=" * 70)
    print("すべてのタスクが正常に完了しました。")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
