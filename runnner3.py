#!/usr/bin/env python3

"""
Claude Code Workflow Runner
===========================

■ 概要
------------------------------------------------------------
指定されたtasksフォルダ内のタスク定義ファイルを連番順に読み込み、
Claude Codeを順番に実行する。

各タスクでは、

    1. 作業ディレクトリ
    2. 使用するClaudeモデル
    3. タスク固有のプロンプト
    4. permission設定（任意）

を指定できる。

tasksフォルダにはsettings.yamlを置くことができ、
全タスク共通の設定を定義できる。


■ 基本構成
------------------------------------------------------------

デフォルトではrunner.py直下のtasksフォルダを使用する。

    runner.py
    tasks/
    ├── settings.yaml
    ├── 001_analyze.yaml
    ├── 002_implement.yaml
    ├── 003_test.yaml
    └── 004_review.yaml

コマンドライン引数 --tasks を指定すると、
任意のtasksフォルダを使用できる。

    python runner.py --tasks ~/workspace/project-a/tasks


■ タスクファイルの仕様
------------------------------------------------------------

各タスクには以下を指定する。

    directory:
        Claude Codeを実行する作業ディレクトリ。

    model:
        Claude Codeで使用するモデル。

    prompt:
        そのタスク固有のプロンプト。

    permission:
        Claude Codeのpermission mode。
        省略した場合はsettings.yamlの共通設定を使用する。

例:

    directory: ~/workspace/my-project
    model: sonnet
    permission: bypassPermissions

    prompt: |
      issue #123を調査して実装してください。
      必要なテストも実行してください。


■ permission
------------------------------------------------------------

permissionはsettings.yamlで全タスク共通設定を指定できる。

    permission: bypassPermissions

各task YAMLにも指定できる。

    permission: default

task側にpermissionが指定されている場合、
settings.yamlよりtask側を優先する。

使用可能なpermission mode:

    default
    acceptEdits
    plan
    dontAsk
    bypassPermissions

Claude Codeには、

    --permission-mode <permission>

として渡す。


■ settings.yaml
------------------------------------------------------------

利用できる設定:

    prompt_prefix:
        全タスクのプロンプトの先頭に追加する文章。

    prompt_suffix:
        全タスクのプロンプトの末尾に追加する文章。

    variables:
        全タスクから利用できるプロンプト変数。

    continue_on_error:
        タスク失敗時に後続タスクを実行するか。

    confirm_each_task:
        タスク実行前に確認するか。

    permission:
        全タスク共通のClaude Code permission mode。


■ settings.yamlの例
------------------------------------------------------------

    prompt_prefix: |
      あなたはこのリポジトリの開発担当者です。
      不明点があっても質問せず、
      合理的に判断して作業を進めてください。

    prompt_suffix: |
      作業が完了したら、
      実施した内容と結果を簡潔に報告してください。

    variables:
      project_name: "Markdown Editor"
      language: "日本語"
      test_command: "npm run test"
      e2e_command: "npm run test:e2e"

    continue_on_error: false
    confirm_each_task: false

    permission: bypassPermissions


■ permissionの優先順位
------------------------------------------------------------

    task.permission
        ↓
    settings.permission
        ↓
    permission指定なし

taskにpermissionがあればtask側を使用する。

taskにもsettingsにもpermissionがない場合は、
Claude Code自身のデフォルト設定を使用する。


■ プロンプト変数
------------------------------------------------------------

settings.yamlのvariablesに定義した値を、
タスクのpromptから以下の形式で参照できる。

    {{変数名}}

環境変数:

    {{ENV:環境変数名}}


■ Claude Codeの実行方式
------------------------------------------------------------

Claude Codeは非対話モードで実行する。

概念的には以下の形式。

    claude \
      --model <model> \
      --permission-mode <permission> \
      -p "<prompt>" \
      --output-format stream-json \
      --verbose


■ リアルタイム進捗表示
------------------------------------------------------------

Claude Codeの出力形式にはstream-jsonを使用する。

Python側でClaude Codeの標準出力をPopenで受け取り、
JSONイベントを1行ずつリアルタイム処理する。

表示するもの:

    - Claudeの通常テキスト
    - ツール使用
    - Read
    - Write
    - Edit
    - Bash
    - Glob
    - Grep
    - ツール完了
    - Claudeの完了
    - エラー

表示しないもの:

    - thinking_tokens
    - thinking_delta
    - その他の内部thinkingイベント

thinking中は、

    [Claude] thinking...

とだけ表示する。


■ Claude終了時の安全処理
------------------------------------------------------------

runner.pyが終了した場合、
実行中のClaude Codeも終了させる。

WSL2/LinuxではClaude Codeをプロセスグループとして起動し、
runner終了時にプロセスグループへSIGTERMを送る。

さらに子プロセス側ではLinuxの
PR_SET_PDEATHSIGを使用する。

これにより、

    Python runner
        ↓
    Claude Code
        ↓
    Claudeの子プロセス

という構造でrunnerが異常終了した場合にも、
Claude Codeが残り続けることを防ぐ。


■ 使用量表示
------------------------------------------------------------

Claude Code公式のstatusLineでは、
5時間枠・7日間枠の使用率を取得できる。

runner.pyから非公式な使用量APIを直接呼び出すことはしない。

使用量の表示はClaude CodeのstatusLine側で行う。

runnerはClaude Codeの実行進捗を担当し、
使用量情報はClaude Code自身の公式statusLineに任せる。


■ 設計方針
------------------------------------------------------------

このrunnerはワークフローエンジンとして過度に複雑化させない。

責務は以下に限定する。

    1. タスクファイルを読む
    2. 連番順に並べる
    3. 共通設定を適用する
    4. プロンプト変数を展開する
    5. Claude Codeを指定モデル・permission・ディレクトリで実行する
    6. Claude Codeの進捗をリアルタイム表示する
    7. runner終了時にClaude Codeを終了する
    8. 成功・失敗に応じて次のタスクへ進む

複雑な状態管理、リトライ制御、
タスク間のデータ受け渡し、並列実行などは扱わない。


■ エントリーポイント
------------------------------------------------------------

直接実行:

    python runner.py

uv:

    uv run python runner.py

別tasksフォルダ:

    uv run python runner.py --tasks ~/workspace/foo/tasks
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path


import yaml


# ============================================================
# 定数
# ============================================================

TASK_FILE_PATTERN = re.compile(
    r"^(\d+)_.*\.ya?ml$",
    re.IGNORECASE,
)

VALID_PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "dontAsk",
    "bypassPermissions",
}


# ============================================================
# グローバル状態
# ============================================================

CURRENT_PROCESS = None


# ============================================================
# コマンドライン引数
# ============================================================

def parse_args():
    """
    コマンドライン引数を解析する。
    """

    parser = argparse.ArgumentParser(
        description="Claude Code Workflow Runner"
    )

    parser.add_argument(
        "--tasks",
        type=Path,
        default=None,
        help=(
            "タスクフォルダを指定します。"
            "指定しない場合はrunner.py直下のtasksフォルダを使用します。"
        ),
    )

    return parser.parse_args()


# ============================================================
# パス設定
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


def get_tasks_dir(args):
    """
    tasksフォルダのパスを決定する。

    --tasksが指定されていなければ、
    runner.py直下のtasksフォルダを使用する。
    """

    if args.tasks is None:
        return BASE_DIR / "tasks"

    return args.tasks.expanduser().resolve()


# ============================================================
# Claude Code
# ============================================================

def find_claude():
    """
    PATHからclaudeコマンドを検索する。
    """

    claude = shutil.which("claude")

    if claude is None:
        raise RuntimeError(
            "PATHから 'claude' コマンドが見つかりません。\n"
            "WSL2のターミナルで 'which claude' を実行してください。"
        )

    return claude


# ============================================================
# YAML
# ============================================================

def load_yaml(path):
    """
    YAMLファイルを読み込む。
    """

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

    except yaml.YAMLError as e:
        raise RuntimeError(
            f"{path.name}: YAMLの解析に失敗しました: {e}"
        ) from e

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{path.name}: YAMLのルートはオブジェクト形式にしてください。"
        )

    return data


# ============================================================
# settings.yaml
# ============================================================

def load_settings(tasks_dir):
    """
    共通設定を読み込む。
    """

    settings_file = tasks_dir / "settings.yaml"

    if not settings_file.is_file():
        raise RuntimeError(
            f"settings.yamlが存在しません: {settings_file}"
        )

    settings = load_yaml(settings_file)

    settings.setdefault("prompt_prefix", "")
    settings.setdefault("prompt_suffix", "")
    settings.setdefault("variables", {})
    settings.setdefault("continue_on_error", False)
    settings.setdefault("confirm_each_task", False)
    settings.setdefault("permission", None)

    if not isinstance(settings["variables"], dict):
        raise RuntimeError(
            "settings.yaml: 'variables' は"
            "オブジェクト形式にしてください。"
        )

    if not isinstance(settings["continue_on_error"], bool):
        raise RuntimeError(
            "settings.yaml: 'continue_on_error' は"
            "true / falseにしてください。"
        )

    if not isinstance(settings["confirm_each_task"], bool):
        raise RuntimeError(
            "settings.yaml: 'confirm_each_task' は"
            "true / falseにしてください。"
        )

    validate_permission(
        settings["permission"],
        "settings.yaml",
    )

    return settings


# ============================================================
# permission
# ============================================================

def validate_permission(permission, source_name):
    """
    permission modeを検証する。

    Noneは未指定として許可する。
    """

    if permission is None:
        return

    permission = str(permission).strip()

    if not permission:
        return

    if permission not in VALID_PERMISSION_MODES:
        valid = ", ".join(sorted(VALID_PERMISSION_MODES))

        raise RuntimeError(
            f"{source_name}: permission '{permission}' は無効です。\n"
            f"使用可能: {valid}"
        )


def get_permission(task, settings, task_file):
    """
    permissionの実効値を決定する。

    優先順位:

        task.permission
        ↓
        settings.permission
        ↓
        None

    Noneの場合はClaude Code自身のデフォルトを使用する。
    """

    if "permission" in task:
        permission = task["permission"]

        validate_permission(
            permission,
            task_file.name,
        )

        if permission is None:
            return None

        permission = str(permission).strip()

        if permission:
            return permission

        return None

    permission = settings.get("permission")

    validate_permission(
        permission,
        "settings.yaml",
    )

    if permission is None:
        return None

    permission = str(permission).strip()

    if permission:
        return permission

    return None


# ============================================================
# タスクファイル取得
# ============================================================

def get_task_files(tasks_dir):
    """
    tasksフォルダから番号付きタスクファイルを取得し、
    番号順に並べる。
    """

    if not tasks_dir.is_dir():
        raise RuntimeError(
            f"tasksフォルダが存在しません: {tasks_dir}"
        )

    tasks = []

    for path in tasks_dir.iterdir():

        if not path.is_file():
            continue

        if path.name == "settings.yaml":
            continue

        match = TASK_FILE_PATTERN.match(path.name)

        if not match:
            continue

        number = int(match.group(1))

        tasks.append((number, path))

    tasks.sort(key=lambda item: item[0])

    return [path for _, path in tasks]


# ============================================================
# プロンプト変数
# ============================================================

VARIABLE_PATTERN = re.compile(
    r"\{\{([^{}]+)\}\}"
)


def expand_variables(text, variables, source_name):
    """
    プロンプト変数を展開する。

    通常:

        {{project_name}}

    環境変数:

        {{ENV:HOME}}
    """

    def replace(match):

        key = match.group(1).strip()

        if key.startswith("ENV:"):

            env_name = key[4:].strip()

            if not env_name:
                raise RuntimeError(
                    f"{source_name}: "
                    "環境変数名が空です。"
                )

            value = os.environ.get(env_name)

            if value is None:
                raise RuntimeError(
                    f"{source_name}: 環境変数 "
                    f"'{env_name}' が設定されていません。"
                )

            return value

        if key not in variables:
            raise RuntimeError(
                f"{source_name}: 未定義のプロンプト変数 "
                f"'{{{{{key}}}}}' が使用されています。"
            )

        value = variables[key]

        if value is None:
            return ""

        return str(value)

    return VARIABLE_PATTERN.sub(
        replace,
        text,
    )


# ============================================================
# プロンプト作成
# ============================================================

def build_prompt(task, settings, task_file):

    prefix = str(
        settings.get("prompt_prefix", "")
    )

    suffix = str(
        settings.get("prompt_suffix", "")
    )

    prompt = str(
        task["prompt"]
    )

    variables = settings["variables"]

    prefix = expand_variables(
        prefix,
        variables,
        f"{task_file.name} / prompt_prefix",
    )

    prompt = expand_variables(
        prompt,
        variables,
        task_file.name,
    )

    suffix = expand_variables(
        suffix,
        variables,
        f"{task_file.name} / prompt_suffix",
    )

    parts = []

    if prefix.strip():
        parts.append(prefix.strip())

    if prompt.strip():
        parts.append(prompt.strip())

    if suffix.strip():
        parts.append(suffix.strip())

    return "\n\n".join(parts)


# ============================================================
# タスク検証
# ============================================================

def validate_task(task, task_file):

    required = (
        "directory",
        "model",
        "prompt",
    )

    for field in required:

        if field not in task:
            raise RuntimeError(
                f"{task_file.name}: "
                f"必須項目 '{field}' がありません。"
            )

        if task[field] is None:
            raise RuntimeError(
                f"{task_file.name}: "
                f"'{field}' が空です。"
            )

    if not str(task["directory"]).strip():
        raise RuntimeError(
            f"{task_file.name}: directoryが空です。"
        )

    if not str(task["model"]).strip():
        raise RuntimeError(
            f"{task_file.name}: modelが空です。"
        )

    if not str(task["prompt"]).strip():
        raise RuntimeError(
            f"{task_file.name}: promptが空です。"
        )

    if "permission" in task:
        validate_permission(
            task["permission"],
            task_file.name,
        )


# ============================================================
# Claudeイベント表示
# ============================================================

def print_claude_event(event):
    """
    stream-jsonのイベントを人間が読みやすい形で表示する。

    thinking_tokens等の内部イベントは表示しない。
    """

    event_type = event.get("type")

    # --------------------------------------------------------
    # assistant
    # --------------------------------------------------------

    if event_type == "assistant":

        message = event.get("message", {})
        content = message.get("content", [])

        has_thinking = False

        for item in content:

            item_type = item.get("type")

            # thinking / thinking_delta
            # --------------------------------------------

            if item_type in (
                "thinking",
                "thinking_delta",
            ):
                has_thinking = True
                continue

            # text
            # --------------------------------------------

            if item_type == "text":

                text = item.get("text", "")

                if text:
                    print(
                        f"\n[Claude] {text}",
                        flush=True,
                    )

                continue

            # tool_use
            # --------------------------------------------

            if item_type == "tool_use":

                tool_name = item.get(
                    "name",
                    "unknown",
                )

                tool_input = item.get(
                    "input",
                    {},
                )

                print(
                    f"\n[Tool] {tool_name}",
                    flush=True,
                )

                if tool_name in (
                    "Read",
                    "Write",
                    "Edit",
                ):

                    file_path = tool_input.get(
                        "file_path"
                    )

                    if file_path:
                        print(
                            f"       {file_path}",
                            flush=True,
                        )

                elif tool_name == "Bash":

                    command = tool_input.get(
                        "command",
                        "",
                    )

                    if command:
                        print(
                            f"       $ {command}",
                            flush=True,
                        )

                elif tool_name == "Glob":

                    pattern = tool_input.get(
                        "pattern",
                        "",
                    )

                    if pattern:
                        print(
                            f"       {pattern}",
                            flush=True,
                        )

                elif tool_name == "Grep":

                    pattern = tool_input.get(
                        "pattern",
                        "",
                    )

                    if pattern:
                        print(
                            f"       {pattern}",
                            flush=True,
                        )

                continue

        if has_thinking:
            print(
                "[Claude] thinking...",
                flush=True,
            )

        return

    # --------------------------------------------------------
    # user / tool_result
    # --------------------------------------------------------

    if event_type == "user":

        message = event.get("message", {})
        content = message.get("content", [])

        for item in content:

            if item.get("type") == "tool_result":

                print(
                    "[Tool] 完了",
                    flush=True,
                )

        return

    # --------------------------------------------------------
    # result
    # --------------------------------------------------------

    if event_type == "result":

        subtype = event.get("subtype")

        if subtype == "success":

            print(
                "\n[Claude] 完了",
                flush=True,
            )

        else:

            print(
                f"\n[Claude] 終了: {subtype}",
                flush=True,
            )

        return

    # --------------------------------------------------------
    # system
    # --------------------------------------------------------

    if event_type == "system":

        subtype = event.get("subtype")

        # thinking_tokens等とは異なり、
        # systemイベントはClaude Codeの状態として有用なので表示する。

        if subtype:

            print(
                f"[Claude] {subtype}",
                flush=True,
            )

        return


# ============================================================
# Claudeプロセス終了
# ============================================================

def terminate_process_tree(process):
    """
    Claude Codeとその子プロセスを終了する。

    Linux/WSL2ではClaudeをプロセスグループとして起動しているため、
    PGID単位で終了させる。

    Windowsでは通常のterminate/killにフォールバックする。
    """

    if process is None:
        return

    if process.poll() is not None:
        return

    try:

        if os.name != "nt":

            try:
                os.killpg(
                    os.getpgid(process.pid),
                    signal.SIGTERM,
                )

            except ProcessLookupError:
                return

            try:
                process.wait(timeout=5)

            except subprocess.TimeoutExpired:

                try:
                    os.killpg(
                        os.getpgid(process.pid),
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass

                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        else:

            process.terminate()

            try:
                process.wait(timeout=5)

            except subprocess.TimeoutExpired:

                process.kill()

                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    except Exception as e:

        print(
            f"[Runner] Claude Code終了処理でエラー: {e}",
            file=sys.stderr,
            flush=True,
        )


# ============================================================
# 子プロセス側の親死亡検知
# ============================================================

def setup_parent_death_signal():
    """
    Linux/WSL2で、親Pythonプロセス終了時に
    Claude Code自身へSIGTERMを送るよう設定する。

    Pythonが異常終了した場合にも、
    Claude Codeが残り続けることを防ぐ。

    Linux以外では何もしない。
    """

    if os.name == "nt":
        return

    try:

        import ctypes

        libc = ctypes.CDLL(
            None,
            use_errno=True,
        )

        PR_SET_PDEATHSIG = 1

        result = libc.prctl(
            PR_SET_PDEATHSIG,
            signal.SIGTERM,
            0,
            0,
            0,
        )

        if result != 0:
            raise OSError(
                ctypes.get_errno()
            )

    except Exception as e:

        print(
            f"[Runner] 親プロセス終了監視を設定できません: {e}",
            file=sys.stderr,
            flush=True,
        )


# ============================================================
# シグナル処理
# ============================================================

def handle_runner_signal(signum, frame):
    """
    runner自身がSIGINT/SIGTERMを受けた場合、
    Claude Codeを終了してからrunnerを終了する。
    """

    global CURRENT_PROCESS

    signal_name = signal.Signals(signum).name

    print(
        f"\n[Runner] {signal_name}を受信しました。",
        flush=True,
    )

    if CURRENT_PROCESS is not None:
        print(
            "[Runner] Claude Codeを終了します。",
            flush=True,
        )

        terminate_process_tree(
            CURRENT_PROCESS
        )

    raise SystemExit(128 + signum)


# ============================================================
# Claude Code実行
# ============================================================

def run_claude(
    claude,
    task_file,
    task,
    settings,
    index,
    total,
):
    """
    Claude Codeを非対話モードで実行する。

    stream-jsonによって進捗をリアルタイム表示する。

    runnerが終了した場合はClaude Codeも終了する。
    """

    global CURRENT_PROCESS

    validate_task(
        task,
        task_file,
    )

    directory = Path(
        str(task["directory"])
    ).expanduser()

    model = str(
        task["model"]
    )

    permission = get_permission(
        task,
        settings,
        task_file,
    )

    prompt = build_prompt(
        task,
        settings,
        task_file,
    )
    
    print()
    print("=" * 70)

    print(
        f"TASK {index}/{total}"
    )

    print(
        f"ファイル               : {task_file.name}"
    )

    print(
        f"モデル                 : {model}"
    )

    print(
        f"作業ディレクトリ       : {directory}"
    )

    print(
        "permission              : "
        f"{permission if permission else '(Claude Code default)'}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Claudeへ実際に渡すプロンプト
    #
    # プロンプトは長くなることが多いため、
    # タスクの基本パラメータとは完全に分離して表示する。
    # --------------------------------------------------------

    print()
    print(
        "------------------------------ PROMPT -------------------------------"
    )
    print()

    print(
        prompt,
        flush=True,
    )

    print()
    print(
        "---------------------------- END PROMPT -----------------------------"
    )

    print()

    if not directory.is_dir():
        raise RuntimeError(
            f"作業ディレクトリが存在しません: {directory}"
        )

    if settings["confirm_each_task"]:

        answer = input(
            f"{task_file.name} を実行しますか？ [y/N]: "
        ).strip().lower()

        if answer not in (
            "y",
            "yes",
        ):

            print(
                "スキップしました。"
            )

            return

    command = [
        claude,
        "--model",
        model,
    ]

    if permission:
        command.extend(
            [
                "--permission-mode",
                permission,
            ]
        )

    command.extend(
        [
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
    )

    print(
        "Claude Codeを実行します。",
        flush=True,
    )

    print(
        flush=True
    )

    popen_kwargs = {
        "cwd": directory,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }

    # Linux/WSL2:
    #
    # Claude Codeを独立したプロセスグループにする。
    #
    # runner終了時にPGID単位で終了できる。
    if os.name != "nt":

        popen_kwargs[
            "start_new_session"
        ] = True

        popen_kwargs[
            "preexec_fn"
        ] = setup_parent_death_signal

    else:

        # WindowsではCREATE_NEW_PROCESS_GROUPを使用。
        popen_kwargs[
            "creationflags"
        ] = subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(
        command,
        **popen_kwargs,
    )

    CURRENT_PROCESS = process

    try:

        if process.stdout is not None:

            for line in process.stdout:

                line = line.strip()

                if not line:
                    continue

                try:

                    event = json.loads(line)

                except json.JSONDecodeError:

                    print(
                        line,
                        flush=True,
                    )

                    continue

                print_claude_event(
                    event
                )

        return_code = process.wait()

    except KeyboardInterrupt:

        print(
            "\n[Runner] Ctrl+Cを受信しました。",
            flush=True,
        )

        terminate_process_tree(
            process
        )

        raise

    finally:

        CURRENT_PROCESS = None

    print()

    if return_code != 0:

        raise RuntimeError(
            f"Claude Codeが終了コード "
            f"{return_code} で終了しました。"
        )

    print(
        f"タスク完了: {task_file.name}"
    )


# ============================================================
# メイン
# ============================================================

def main():

    global CURRENT_PROCESS

    # runner自身の終了時にClaudeを止める。
    signal.signal(
        signal.SIGINT,
        handle_runner_signal,
    )

    if hasattr(signal, "SIGTERM"):
        signal.signal(
            signal.SIGTERM,
            handle_runner_signal,
        )

    args = parse_args()

    tasks_dir = get_tasks_dir(
        args
    )

    print("=" * 70)

    print(
        "Claude Code Workflow Runner"
    )

    print("=" * 70)

    print(
        f"Runner : {BASE_DIR}"
    )

    print(
        f"Tasks  : {tasks_dir}"
    )

    print()

    try:

        claude = find_claude()

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        return 1

    print(
        f"Claude : {claude}"
    )

    try:

        settings = load_settings(
            tasks_dir
        )

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        return 1

    try:

        task_files = get_task_files(
            tasks_dir
        )

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        return 1

    if not task_files:

        print()

        print(
            "実行するタスクファイルがありません。"
        )

        return 0

    print(
        f"Tasks  : {len(task_files)}"
    )

    print()

    print(
        "実行順:"
    )

    for index, task_file in enumerate(
        task_files,
        start=1,
    ):

        print(
            f"  {index}. {task_file.name}"
        )

    for index, task_file in enumerate(
        task_files,
        start=1,
    ):

        print()

        print(
            "#" * 70
        )

        print(
            f"# TASK {index}/{len(task_files)}: "
            f"{task_file.name}"
        )

        print(
            "#" * 70
        )

        try:

            task = load_yaml(
                task_file
            )

            run_claude(
                claude=claude,
                task_file=task_file,
                task=task,
                settings=settings,
                index=index,
                total=len(task_files),
            )

        except Exception as e:

            print()

            print(
                "=" * 70
            )

            print(
                "ERROR"
            )

            print(
                "=" * 70
            )

            print(
                f"タスク: {task_file.name}"
            )

            print(
                f"内容  : {e}"
            )

            print()

            # 念のため、エラー時にもClaudeを残さない。
            if CURRENT_PROCESS is not None:

                terminate_process_tree(
                    CURRENT_PROCESS
                )

                CURRENT_PROCESS = None

            if settings[
                "continue_on_error"
            ]:

                print(
                    "continue_on_error=true "
                    "のため、次のタスクへ進みます。"
                )

                continue

            print(
                "ワークフローを停止します。"
            )

            return 1

    print()

    print(
        "=" * 70
    )

    print(
        "すべてのタスクが完了しました。"
    )

    print(
        "=" * 70
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())