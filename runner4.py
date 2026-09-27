#!/usr/bin/env python3

"""
Claude Code Workflow Runner
===========================

■ 概要
------------------------------------------------------------
指定されたtasksフォルダ内のタスク定義ファイルを連番順に読み込み、
Claude Codeを順番に実行する。

タスクが失敗した場合はtasksフォルダ内に実行レポートを保存する。

例:

    runner.py
    tasks/
    ├── settings.yaml
    ├── 001_analyze.yaml
    ├── 002_implement.yaml
    ├── 003_test.yaml
    └── report_20260927185000.log


■ コマンドライン引数
------------------------------------------------------------

--tasks
    使用するtasksフォルダを指定する。

    指定しない場合:

        runner.py直下のtasks/

    例:

        python runner.py --tasks ~/workspace/project/tasks


--start-task
    指定した番号のタスクから実行する。

    例:

        python runner.py --start-task 3

    001, 002を飛ばして003から開始する。


--resume
    指定した開始タスクについて、
    過去のreportからClaude Codeのsession_idを取得し、
    そのセッションをresumeして再開する。

    例:

        python runner.py --start-task 3 --resume


■ 再開の基本的な使い方
------------------------------------------------------------

例えば002タスクでClaudeがクレジット不足により停止した場合、

    tasks/report_20260927185000.log

に以下のような情報が記録される。

    TASK: 002_implement.yaml
    RESULT: FAILED
    SESSION_ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    CAUSE: Claude Codeの使用量制限またはクレジット不足
    RESUME_COMMAND:
      python runner.py --start-task 2 --resume

使用量制限が解除された後、

    python runner.py --start-task 2 --resume

を実行すると、
reportからsession_idを取得して同じClaude Codeセッションを再開する。


■ resumeを使用しない場合
------------------------------------------------------------

    python runner.py --start-task 2

の場合は、新しいClaude Codeセッションを開始する。

そのため、途中までの会話コンテキストは引き継がない。

ただし、Claudeがそれまでに保存したファイル変更は残る。


■ permission
------------------------------------------------------------

settings.yaml:

    permission: bypassPermissions

各task:

    permission: plan

taskにpermissionが指定されている場合、
settings.yamlよりtask側を優先する。


■ プロンプト
------------------------------------------------------------

Claudeに実際に渡す最終プロンプトを、
Claude Code起動前にコンソールへ表示する。

以下の3つを結合したものを実際のプロンプトとする。

    prompt_prefix
        ↓
    task.prompt
        ↓
    prompt_suffix

変数展開後の最終結果を表示する。


■ Claude終了時の安全処理
------------------------------------------------------------

runner.pyが終了した場合、
実行中のClaude Codeも終了させる。

Linux/WSL2ではプロセスグループ単位で終了させる。

また、PR_SET_PDEATHSIGを利用して、
runnerの親プロセスが終了した場合にも
Claude Codeが残り続けることを防ぐ。


■ 実行レポート
------------------------------------------------------------

各runner実行ごとに、

    tasks/report_yyyymmddHHMMSS.log

を作成する。

1つのレポートに、

    - runner開始時刻
    - 使用tasksフォルダ
    - コマンドライン引数
    - 全体結果
    - 各taskの結果
    - Claudeモデル
    - permission
    - session_id
    - 終了コード
    - 失敗原因
    - resume方法

を記録する。


■ Claude Code stream-json
------------------------------------------------------------

Claude Codeのstream-jsonを利用する。

表示するもの:

    - Claude text
    - tool_use
    - tool_result
    - result
    - system

表示しないもの:

    - thinking
    - thinking_delta
    - thinking_tokens

thinking中は、

    [Claude] thinking...

とだけ表示する。


■ クレジット不足判定
------------------------------------------------------------

Claude Codeの出力に、

    usage limit
    rate limit
    credit
    quota
    5-hour
    weekly limit
    exceeded

などの使用量制限を示す文字列が含まれていた場合、
クレジット/使用量制限による失敗としてレポートする。

Claude Code側の終了メッセージを取得できなかった場合は、
一般的なClaude Code実行エラーとして扱う。


■ 設計方針
------------------------------------------------------------

タスクの状態管理を複雑化させず、
reportログを再開情報の保存場所として利用する。

session_idはClaude Codeのstream-jsonイベントから取得する。

再開時には最新のreportから、
指定タスクのsession_idを検索する。
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime
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

CREDIT_LIMIT_KEYWORDS = (
    "usage limit",
    "rate limit",
    "credit",
    "credits",
    "quota",
    "5-hour",
    "5 hour",
    "weekly limit",
    "weekly usage",
    "exceeded",
    "limit reached",
    "usage exceeded",
    "out of credits",
    "insufficient credits",
)

CURRENT_PROCESS = None
CURRENT_SESSION_ID = None
CURRENT_TASK_NAME = None

CURRENT_REPORT = None


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

    parser.add_argument(
        "--start-task",
        type=int,
        default=None,
        metavar="NUMBER",
        help=(
            "指定した番号のタスクから実行します。"
            "例: --start-task 3"
        ),
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "指定した開始タスクの過去のClaude Codeセッションを"
            "resumeして再開します。"
        ),
    )

    return parser.parse_args()


# ============================================================
# パス
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


def get_tasks_dir(args):
    """
    tasksフォルダのパスを決定する。
    """

    if args.tasks is None:
        return BASE_DIR / "tasks"

    return args.tasks.expanduser().resolve()


# ============================================================
# report
# ============================================================

def create_report(tasks_dir):
    """
    実行開始時にreportファイルを作成する。
    """

    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    report_path = (
        tasks_dir /
        f"report_{timestamp}.log"
    )

    # 同一秒に複数起動された場合に備える。
    counter = 1

    while report_path.exists():

        report_path = (
            tasks_dir /
            f"report_{timestamp}_{counter}.log"
        )

        counter += 1

    return report_path


def write_report(report_path, text=""):
    """
    reportへ追記する。
    """

    with report_path.open(
        "a",
        encoding="utf-8",
    ) as f:

        f.write(text)

        if text and not text.endswith("\n"):
            f.write("\n")


def report_section(report_path, title):
    """
    reportにセクション区切りを書く。
    """

    write_report(
        report_path,
        "\n"
        + "=" * 70
        + "\n"
        + title
        + "\n"
        + "=" * 70
        + "\n",
    )


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

        with path.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = yaml.safe_load(f)

    except yaml.YAMLError as e:

        raise RuntimeError(
            f"{path.name}: YAMLの解析に失敗しました: {e}"
        ) from e

    if data is None:
        return {}

    if not isinstance(data, dict):

        raise RuntimeError(
            f"{path.name}: YAMLのルートは"
            "オブジェクト形式にしてください。"
        )

    return data


# ============================================================
# permission
# ============================================================

def validate_permission(
    permission,
    source_name,
):
    """
    permission modeを検証する。
    """

    if permission is None:
        return

    permission = str(
        permission
    ).strip()

    if not permission:
        return

    if permission not in VALID_PERMISSION_MODES:

        valid = ", ".join(
            sorted(
                VALID_PERMISSION_MODES
            )
        )

        raise RuntimeError(
            f"{source_name}: permission "
            f"'{permission}' は無効です。\n"
            f"使用可能: {valid}"
        )


def get_permission(
    task,
    settings,
    task_file,
):
    """
    permissionの実効値を決定する。

    task.permission
        ↓
    settings.permission
        ↓
    None
    """

    if "permission" in task:

        permission = task["permission"]

        validate_permission(
            permission,
            task_file.name,
        )

        if permission is None:
            return None

        permission = str(
            permission
        ).strip()

        return permission or None

    permission = settings.get(
        "permission"
    )

    validate_permission(
        permission,
        "settings.yaml",
    )

    if permission is None:
        return None

    permission = str(
        permission
    ).strip()

    return permission or None


# ============================================================
# settings.yaml
# ============================================================

def load_settings(tasks_dir):
    """
    共通設定を読み込む。
    """

    settings_file = (
        tasks_dir /
        "settings.yaml"
    )

    if not settings_file.is_file():

        raise RuntimeError(
            f"settings.yamlが存在しません: "
            f"{settings_file}"
        )

    settings = load_yaml(
        settings_file
    )

    settings.setdefault(
        "prompt_prefix",
        "",
    )

    settings.setdefault(
        "prompt_suffix",
        "",
    )

    settings.setdefault(
        "variables",
        {},
    )

    settings.setdefault(
        "continue_on_error",
        False,
    )

    settings.setdefault(
        "confirm_each_task",
        False,
    )

    settings.setdefault(
        "permission",
        None,
    )

    if not isinstance(
        settings["variables"],
        dict,
    ):

        raise RuntimeError(
            "settings.yaml: 'variables' は"
            "オブジェクト形式にしてください。"
        )

    if not isinstance(
        settings["continue_on_error"],
        bool,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'continue_on_error' は"
            "true / falseにしてください。"
        )

    if not isinstance(
        settings["confirm_each_task"],
        bool,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'confirm_each_task' は"
            "true / falseにしてください。"
        )

    validate_permission(
        settings["permission"],
        "settings.yaml",
    )

    return settings


# ============================================================
# タスク取得
# ============================================================

def get_task_files(tasks_dir):
    """
    tasksフォルダから番号付きタスクファイルを取得し、
    番号順に並べる。
    """

    if not tasks_dir.is_dir():

        raise RuntimeError(
            f"tasksフォルダが存在しません: "
            f"{tasks_dir}"
        )

    tasks = []

    for path in tasks_dir.iterdir():

        if not path.is_file():
            continue

        if path.name == "settings.yaml":
            continue

        if path.name.startswith("report_"):
            continue

        match = TASK_FILE_PATTERN.match(
            path.name
        )

        if not match:
            continue

        number = int(
            match.group(1)
        )

        tasks.append(
            (
                number,
                path,
            )
        )

    tasks.sort(
        key=lambda item: item[0]
    )

    return tasks


# ============================================================
# プロンプト変数
# ============================================================

VARIABLE_PATTERN = re.compile(
    r"\{\{([^{}]+)\}\}"
)


def expand_variables(
    text,
    variables,
    source_name,
):
    """
    プロンプト変数を展開する。
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

            value = os.environ.get(
                env_name
            )

            if value is None:

                raise RuntimeError(
                    f"{source_name}: "
                    f"環境変数 '{env_name}' "
                    "が設定されていません。"
                )

            return value

        if key not in variables:

            raise RuntimeError(
                f"{source_name}: "
                "未定義のプロンプト変数 "
                f"'{{{{{key}}}}}' "
                "が使用されています。"
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

def build_prompt(
    task,
    settings,
    task_file,
):
    """
    Claude Codeへ実際に渡す最終プロンプトを作る。
    """

    prefix = str(
        settings.get(
            "prompt_prefix",
            "",
        )
    )

    suffix = str(
        settings.get(
            "prompt_suffix",
            "",
        )
    )

    prompt = str(
        task["prompt"]
    )

    variables = settings[
        "variables"
    ]

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
        parts.append(
            prefix.strip()
        )

    if prompt.strip():
        parts.append(
            prompt.strip()
        )

    if suffix.strip():
        parts.append(
            suffix.strip()
        )

    return "\n\n".join(parts)


# ============================================================
# タスク検証
# ============================================================

def validate_task(
    task,
    task_file,
):
    """
    タスク定義を検証する。
    """

    required = (
        "directory",
        "model",
        "prompt",
    )

    for field in required:

        if field not in task:

            raise RuntimeError(
                f"{task_file.name}: "
                f"必須項目 '{field}' "
                "がありません。"
            )

        if task[field] is None:

            raise RuntimeError(
                f"{task_file.name}: "
                f"'{field}' が空です。"
            )

    if not str(
        task["directory"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: "
            "directoryが空です。"
        )

    if not str(
        task["model"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: "
            "modelが空です。"
        )

    if not str(
        task["prompt"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: "
            "promptが空です。"
        )

    if "permission" in task:

        validate_permission(
            task["permission"],
            task_file.name,
        )


# ============================================================
# Claudeイベント
# ============================================================

def print_claude_event(
    event,
):
    """
    Claude Codeのstream-jsonイベントを表示する。

    戻り値:
        session_id
        またはNone
    """

    session_id = event.get(
        "session_id"
    )

    event_type = event.get(
        "type"
    )

    # --------------------------------------------------------
    # assistant
    # --------------------------------------------------------

    if event_type == "assistant":

        message = event.get(
            "message",
            {},
        )

        content = message.get(
            "content",
            [],
        )

        has_thinking = False

        for item in content:

            item_type = item.get(
                "type"
            )

            if item_type in (
                "thinking",
                "thinking_delta",
            ):

                has_thinking = True

                continue

            if item_type == "text":

                text = item.get(
                    "text",
                    "",
                )

                if text:

                    print(
                        f"\n[Claude] {text}",
                        flush=True,
                    )

                continue

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

                    file_path = (
                        tool_input.get(
                            "file_path"
                        )
                    )

                    if file_path:

                        print(
                            f"       {file_path}",
                            flush=True,
                        )

                elif tool_name == "Bash":

                    command = (
                        tool_input.get(
                            "command",
                            "",
                        )
                    )

                    if command:

                        print(
                            f"       $ {command}",
                            flush=True,
                        )

                elif tool_name == "Glob":

                    pattern = (
                        tool_input.get(
                            "pattern",
                            "",
                        )
                    )

                    if pattern:

                        print(
                            f"       {pattern}",
                            flush=True,
                        )

                elif tool_name == "Grep":

                    pattern = (
                        tool_input.get(
                            "pattern",
                            "",
                        )
                    )

                    if pattern:

                        print(
                            f"       {pattern}",
                            flush=True,
                        )

        if has_thinking:

            print(
                "[Claude] thinking...",
                flush=True,
            )

    # --------------------------------------------------------
    # user
    # --------------------------------------------------------

    elif event_type == "user":

        message = event.get(
            "message",
            {},
        )

        content = message.get(
            "content",
            [],
        )

        for item in content:

            if item.get(
                "type"
            ) == "tool_result":

                print(
                    "[Tool] 完了",
                    flush=True,
                )

    # --------------------------------------------------------
    # result
    # --------------------------------------------------------

    elif event_type == "result":

        subtype = event.get(
            "subtype"
        )

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

    # --------------------------------------------------------
    # system
    # --------------------------------------------------------

    elif event_type == "system":

        subtype = event.get(
            "subtype"
        )

        if subtype:

            print(
                f"[Claude] {subtype}",
                flush=True,
            )

    return session_id


# ============================================================
# 使用量制限判定
# ============================================================

def is_credit_limit_message(
    text,
):
    """
    Claude出力が使用量・クレジット制限を
    示しているか判定する。
    """

    lowered = text.lower()

    return any(
        keyword in lowered
        for keyword in CREDIT_LIMIT_KEYWORDS
    )


# ============================================================
# Claudeプロセス終了
# ============================================================

def terminate_process_tree(
    process,
):
    """
    Claude Codeと子プロセスを終了する。
    """

    if process is None:
        return

    if process.poll() is not None:
        return

    try:

        if os.name != "nt":

            try:

                os.killpg(
                    os.getpgid(
                        process.pid
                    ),
                    signal.SIGTERM,
                )

            except ProcessLookupError:

                return

            try:

                process.wait(
                    timeout=5
                )

            except subprocess.TimeoutExpired:

                try:

                    os.killpg(
                        os.getpgid(
                            process.pid
                        ),
                        signal.SIGKILL,
                    )

                except ProcessLookupError:
                    pass

                try:

                    process.wait(
                        timeout=2
                    )

                except subprocess.TimeoutExpired:
                    pass

        else:

            process.terminate()

            try:

                process.wait(
                    timeout=5
                )

            except subprocess.TimeoutExpired:

                process.kill()

                try:

                    process.wait(
                        timeout=2
                    )

                except subprocess.TimeoutExpired:
                    pass

    except Exception as e:

        print(
            "[Runner] Claude Code終了処理でエラー: "
            f"{e}",
            file=sys.stderr,
            flush=True,
        )


# ============================================================
# 親死亡シグナル
# ============================================================

def setup_parent_death_signal():
    """
    Linux/WSL2で親プロセス終了時に
    Claude CodeへSIGTERMを送る。
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
            "[Runner] 親プロセス終了監視を"
            f"設定できません: {e}",
            file=sys.stderr,
            flush=True,
        )


# ============================================================
# シグナル
# ============================================================

def handle_runner_signal(
    signum,
    frame,
):
    """
    runner終了時にClaude Codeも終了する。
    """

    global CURRENT_PROCESS

    signal_name = signal.Signals(
        signum
    ).name

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

    raise SystemExit(
        128 + signum
    )


# ============================================================
# report検索
# ============================================================

def get_report_files(
    tasks_dir,
):
    """
    tasks内のreportファイルを新しい順で取得する。
    """

    reports = list(
        tasks_dir.glob(
            "report_*.log"
        )
    )

    reports.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    return reports


def find_session_id_from_reports(
    tasks_dir,
    task_number,
    task_file_name,
):
    """
    過去のreportから指定taskのsession_idを探す。

    最新の失敗レポートを優先する。
    """

    task_marker = (
        f"TASK_NUMBER: {task_number}"
    )

    file_marker = (
        f"TASK_FILE: {task_file_name}"
    )

    for report_path in get_report_files(
        tasks_dir
    ):

        try:

            text = report_path.read_text(
                encoding="utf-8"
            )

        except OSError:
            continue

        # task番号とtaskファイルの両方が
        # 同じセクションに存在するか確認する。

        sections = re.split(
            r"\n={10,}\n",
            text,
        )

        for section in reversed(
            sections
        ):

            if task_marker not in section:
                continue

            if file_marker not in section:
                continue

            match = re.search(
                r"SESSION_ID:\s*(\S+)",
                section,
            )

            if match:

                session_id = (
                    match.group(1)
                    .strip()
                )

                if session_id:
                    return session_id

    return None


# ============================================================
# Claude実行
# ============================================================
def run_claude(
    claude,
    task_file,
    task_number,
    task,
    settings,
    index,
    total,
    report_path,
    resume_session_id=None,
):
    """
    Claude Codeを実行する。

    resume_session_idが指定されている場合、
    既存セッションをresumeする。

    成功・失敗判定:
        1. Claude Codeのresultイベントを最優先する
        2. result.subtype == "success" → 成功
        3. resultイベントがsuccess以外 → 失敗
        4. resultイベントを取得できなかった場合のみ
           プロセス終了コードをフォールバックとして使用
        5. Claudeの通常出力に含まれる文字列から
           成功・失敗を判定しない

    これにより、Claudeの出力中に
    "credit", "quota", "exceeded" 等の文字列が
    含まれていても、正常終了を誤って失敗扱いしない。
    """

    global CURRENT_PROCESS
    global CURRENT_SESSION_ID
    global CURRENT_TASK_NAME

    validate_task(
        task,
        task_file,
    )

    directory = Path(
        str(
            task["directory"]
        )
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

    CURRENT_SESSION_ID = (
        resume_session_id
    )

    CURRENT_TASK_NAME = (
        task_file.name
    )

    # --------------------------------------------------------
    # コンソール表示
    # --------------------------------------------------------

    print()
    print(
        "=" * 70
    )

    print(
        f"TASK {index}/{total}"
    )

    print(
        f"タスク番号           : {task_number}"
    )

    print(
        f"ファイル             : {task_file.name}"
    )

    print(
        f"モデル               : {model}"
    )

    print(
        f"作業ディレクトリ     : {directory}"
    )

    print(
        "permission            : "
        f"{permission if permission else '(Claude Code default)'}"
    )

    if resume_session_id:

        print(
            "resume session        : "
            f"{resume_session_id}"
        )

    else:

        print(
            "resume session        : (new)"
        )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # プロンプト表示
    # --------------------------------------------------------

    print()

    print(
        "------------------------------ PROMPT -------------------------------"
    )

    print()

    if resume_session_id:

        actual_prompt = (
            "前回のセッションで中断したタスクを続行してください。"
            "\n\n"
            "現在の作業状態を確認し、"
            "前回完了していない作業から再開してください。"
            "\n"
            "既に完了している作業を最初からやり直す必要はありません。"
        )

    else:

        actual_prompt = prompt

    print(
        actual_prompt,
        flush=True,
    )

    print()

    print(
        "---------------------------- END PROMPT -----------------------------"
    )

    print()

    # --------------------------------------------------------
    # 作業ディレクトリ確認
    # --------------------------------------------------------

    if not directory.is_dir():

        raise RuntimeError(
            f"作業ディレクトリが存在しません: "
            f"{directory}"
        )

    # --------------------------------------------------------
    # タスク確認
    # --------------------------------------------------------

    if settings[
        "confirm_each_task"
    ]:

        answer = input(
            f"{task_file.name} "
            "を実行しますか？ [y/N]: "
        ).strip().lower()

        if answer not in (
            "y",
            "yes",
        ):

            print(
                "スキップしました。"
            )

            write_report(
                report_path,
                "\n"
                f"TASK_NUMBER: {task_number}\n"
                f"TASK_FILE: {task_file.name}\n"
                "RESULT: SKIPPED\n",
            )

            return {
                "success": True,
                "skipped": True,
                "session_id": None,
                "return_code": None,
                "cause": None,
                "credit_limit": False,
            }

    # --------------------------------------------------------
    # Claude Codeコマンド
    # --------------------------------------------------------

    command = [
        claude,
    ]

    if resume_session_id:

        command.extend(
            [
                "--resume",
                resume_session_id,
            ]
        )

    command.extend(
        [
            "--model",
            model,
        ]
    )

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
            actual_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
    )

    # --------------------------------------------------------
    # report開始記録
    # --------------------------------------------------------

    report_section(
        report_path,
        f"TASK {task_number}: {task_file.name}",
    )

    write_report(
        report_path,
        f"TASK_NUMBER: {task_number}\n"
        f"TASK_FILE: {task_file.name}\n"
        f"MODEL: {model}\n"
        f"DIRECTORY: {directory}\n"
        f"PERMISSION: "
        f"{permission if permission else '(Claude Code default)'}\n"
        f"RESUME_REQUESTED: "
        f"{'true' if resume_session_id else 'false'}\n"
        f"SESSION_ID: "
        f"{resume_session_id if resume_session_id else '(new)'}\n"
        f"START_TIME: "
        f"{datetime.now().isoformat(timespec='seconds')}\n"
        "\n"
        "PROMPT:\n"
        "--------------------\n"
        f"{actual_prompt}\n"
        "--------------------\n",
    )

    print(
        "Claude Codeを実行します。",
        flush=True,
    )

    # --------------------------------------------------------
    # Popen設定
    # --------------------------------------------------------

    popen_kwargs = {
        "cwd": directory,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }

    if os.name != "nt":

        popen_kwargs[
            "start_new_session"
        ] = True

        popen_kwargs[
            "preexec_fn"
        ] = setup_parent_death_signal

    else:

        popen_kwargs[
            "creationflags"
        ] = subprocess.CREATE_NEW_PROCESS_GROUP

    # --------------------------------------------------------
    # Claude起動
    # --------------------------------------------------------

    process = subprocess.Popen(
        command,
        **popen_kwargs,
    )

    CURRENT_PROCESS = process

    # --------------------------------------------------------
    # 成功・失敗判定用状態
    #
    # 重要:
    # 出力文字列そのものから成功・失敗を判定しない。
    # --------------------------------------------------------

    result_event_received = False

    result_event_success = False

    result_event_subtype = None

    session_id = (
        resume_session_id
    )

    output_buffer = []

    try:

        if process.stdout is not None:

            for line in process.stdout:

                line = line.rstrip()

                if not line:
                    continue

                # ------------------------------------------------
                # 生ログも保持
                #
                # ただし、これを成功・失敗判定には使用しない。
                # ------------------------------------------------

                output_buffer.append(
                    line
                )

                # ------------------------------------------------
                # JSONイベントとして解析
                # ------------------------------------------------

                try:

                    event = json.loads(
                        line
                    )

                except json.JSONDecodeError:

                    print(
                        line,
                        flush=True,
                    )

                    write_report(
                        report_path,
                        f"[Claude raw] {line}\n",
                    )

                    continue

                # ------------------------------------------------
                # session_id取得
                # ------------------------------------------------

                event_session_id = (
                    print_claude_event(
                        event
                    )
                )

                if event_session_id:

                    session_id = (
                        event_session_id
                    )

                    CURRENT_SESSION_ID = (
                        event_session_id
                    )

                event_type = event.get(
                    "type"
                )

                # ------------------------------------------------
                # resultイベント
                #
                # ここを成功・失敗判定の最優先情報とする。
                # ------------------------------------------------

                if event_type == "result":

                    result_event_received = True

                    result_event_subtype = (
                        event.get(
                            "subtype"
                        )
                    )

                    result_event_success = (
                        result_event_subtype
                        == "success"
                    )

                    if result_event_success:

                        print(
                            "\n[Claude] 完了",
                            flush=True,
                        )

                    else:

                        print(
                            "\n[Claude] 終了: "
                            f"{result_event_subtype}",
                            flush=True,
                        )

                    # ------------------------------------------------
                    # resultイベントそのものをreportに保存
                    # ------------------------------------------------

                    write_report(
                        report_path,
                        "RESULT_EVENT:\n"
                        + json.dumps(
                            event,
                            ensure_ascii=False,
                        )
                        + "\n",
                    )

                # ------------------------------------------------
                # system / assistant等は
                # print_claude_event()側で表示する。
                # ------------------------------------------------

        return_code = (
            process.wait()
        )

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

    # --------------------------------------------------------
    # session_id記録
    # --------------------------------------------------------

    if session_id:

        CURRENT_SESSION_ID = (
            session_id
        )

        write_report(
            report_path,
            f"SESSION_ID: {session_id}\n",
        )

    # ========================================================
    # 最終的な成功・失敗判定
    #
    # 優先順位:
    #
    # 1. resultイベント
    # 2. プロセス終了コード
    #
    # Claudeの出力本文は判定に使用しない。
    # ========================================================

    if result_event_received:

        # ----------------------------------------------------
        # Claude Code自身が返したresultを最優先
        # ----------------------------------------------------

        if result_event_success:

            success = True

            cause = None

            result = "SUCCESS"

        else:

            success = False

            cause = (
                "Claude Codeのresultイベントが"
                f"success以外でした: "
                f"{result_event_subtype}"
            )

            result = "FAILED"

    else:

        # ----------------------------------------------------
        # resultイベントを取得できなかった場合のみ、
        # プロセス終了コードをフォールバックとして使用。
        # ----------------------------------------------------

        if return_code == 0:

            success = True

            cause = None

            result = "SUCCESS"

        else:

            success = False

            cause = (
                f"Claude Codeが終了コード "
                f"{return_code} で終了しました。"
            )

            result = "FAILED"

    # --------------------------------------------------------
    # クレジット不足の判定
    #
    # 重要:
    # これは成功・失敗判定には使用しない。
    #
    # あくまで「失敗した場合の原因分類」にのみ使用する。
    # --------------------------------------------------------

    output_text = "\n".join(
        output_buffer
    )

    credit_limit = False

    if not success:

        credit_limit = (
            is_credit_limit_message(
                output_text
            )
        )

        if credit_limit:

            cause = (
                "Claude Codeの出力から、"
                "使用量制限・クレジット不足・"
                "rate limit等による停止と"
                "判断されました。"
                f"\n"
                f"元の終了情報: {cause}"
            )

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    write_report(
        report_path,
        f"RESULT: {result}\n"
        f"RETURN_CODE: {return_code}\n"
        f"RESULT_EVENT_RECEIVED: "
        f"{'true' if result_event_received else 'false'}\n"
        f"RESULT_SUBTYPE: "
        f"{result_event_subtype if result_event_subtype else '(none)'}\n"
        f"END_TIME: "
        f"{datetime.now().isoformat(timespec='seconds')}\n"
        f"CAUSE: "
        f"{cause if cause else '(none)'}\n"
        f"CREDIT_LIMIT_DETECTED: "
        f"{'true' if credit_limit else 'false'}\n",
    )

    # --------------------------------------------------------
    # 失敗時
    # --------------------------------------------------------

    if not success:

        write_report(
            report_path,
            "\n"
            "RESUME_INFORMATION:\n"
            "--------------------\n"
            f"SESSION_ID: "
            f"{session_id if session_id else '(取得できませんでした)'}\n",
        )

        if session_id:

            resume_command = (
                f"python {Path(__file__).name} "
                f"--tasks \"{report_path.parent}\" "
                f"--start-task {task_number} "
                "--resume"
            )

            write_report(
                report_path,
                "RESUME_COMMAND:\n"
                f"{resume_command}\n",
            )

        else:

            new_session_command = (
                f"python {Path(__file__).name} "
                f"--tasks \"{report_path.parent}\" "
                f"--start-task {task_number}"
            )

            write_report(
                report_path,
                "RESUME_COMMAND:\n"
                "session_idを取得できなかったため、"
                "既存セッションのresumeは利用できません。\n"
                "\n"
                "新規セッションで再実行する場合:\n"
                f"{new_session_command}\n",
            )

        write_report(
            report_path,
            "--------------------\n",
        )

    return {
        "success": success,
        "skipped": False,
        "session_id": session_id,
        "return_code": return_code,
        "cause": cause,
        "credit_limit": credit_limit,
        "result_event_received": (
            result_event_received
        ),
        "result_event_success": (
            result_event_success
        ),
        "result_event_subtype": (
            result_event_subtype
        ),
    }


# ============================================================
# メイン
# ============================================================

def main():

    global CURRENT_PROCESS
    global CURRENT_REPORT

    signal.signal(
        signal.SIGINT,
        handle_runner_signal,
    )

    if hasattr(
        signal,
        "SIGTERM",
    ):

        signal.signal(
            signal.SIGTERM,
            handle_runner_signal,
        )

    args = parse_args()

    tasks_dir = get_tasks_dir(
        args
    )

    if not tasks_dir.is_dir():

        print(
            f"ERROR: tasksフォルダが存在しません: "
            f"{tasks_dir}",
            file=sys.stderr,
        )

        return 1

    report_path = create_report(
        tasks_dir
    )

    CURRENT_REPORT = (
        report_path
    )

    write_report(
        report_path,
        "=" * 70
        + "\n"
        + "Claude Code Workflow Runner Report\n"
        + "=" * 70
        + "\n"
        + f"START_TIME: "
        f"{datetime.now().isoformat(timespec='seconds')}\n"
        + f"TASKS_DIR: {tasks_dir}\n"
        + f"COMMAND: {' '.join(sys.argv)}\n"
        + f"START_TASK: "
        f"{args.start_task if args.start_task is not None else '(first)'}\n"
        + f"RESUME: "
        f"{'true' if args.resume else 'false'}\n",
    )

    print(
        "=" * 70
    )

    print(
        "Claude Code Workflow Runner"
    )

    print(
        "=" * 70
    )

    print(
        f"Runner : {BASE_DIR}"
    )

    print(
        f"Tasks  : {tasks_dir}"
    )

    print(
        f"Report : {report_path}"
    )

    if args.start_task is not None:

        print(
            f"Start  : TASK {args.start_task}"
        )

    if args.resume:

        print(
            "Resume : ON"
        )

    print()

    # --------------------------------------------------------
    # Claude
    # --------------------------------------------------------

    try:

        claude = find_claude()

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        write_report(
            report_path,
            f"RUNNER_ERROR: {e}\n"
            "OVERALL_RESULT: FAILED\n",
        )

        return 1

    print(
        f"Claude : {claude}"
    )

    # --------------------------------------------------------
    # settings
    # --------------------------------------------------------

    try:

        settings = load_settings(
            tasks_dir
        )

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        write_report(
            report_path,
            f"RUNNER_ERROR: {e}\n"
            "OVERALL_RESULT: FAILED\n",
        )

        return 1

    # --------------------------------------------------------
    # task files
    # --------------------------------------------------------

    try:

        task_entries = get_task_files(
            tasks_dir
        )

    except RuntimeError as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr,
        )

        write_report(
            report_path,
            f"RUNNER_ERROR: {e}\n"
            "OVERALL_RESULT: FAILED\n",
        )

        return 1

    if not task_entries:

        print(
            "実行するタスクファイルがありません。"
        )

        write_report(
            report_path,
            "OVERALL_RESULT: NO_TASKS\n",
        )

        return 0

    # --------------------------------------------------------
    # 開始タスク
    # --------------------------------------------------------

    start_position = 0

    if args.start_task is not None:

        found = False

        for position, (
            task_number,
            task_file,
        ) in enumerate(
            task_entries
        ):

            if task_number == args.start_task:

                start_position = (
                    position
                )

                found = True

                break

        if not found:

            message = (
                f"TASK {args.start_task} "
                "が見つかりません。"
            )

            print(
                f"ERROR: {message}",
                file=sys.stderr,
            )

            write_report(
                report_path,
                f"RUNNER_ERROR: {message}\n"
                "OVERALL_RESULT: FAILED\n",
            )

            return 1

    # --------------------------------------------------------
    # 実行順
    # --------------------------------------------------------

    print(
        f"Tasks  : {len(task_entries)}"
    )

    print()

    print(
        "実行順:"
    )

    for position, (
        task_number,
        task_file,
    ) in enumerate(
        task_entries[
            start_position:
        ],
        start=start_position + 1,
    ):

        print(
            f"  {task_number}. "
            f"{task_file.name}"
        )

    write_report(
        report_path,
        "\n"
        + "EXECUTION_TASKS:\n"
        + "\n".join(
            f"{number}: {path.name}"
            for number, path in task_entries[
                start_position:
            ]
        )
        + "\n",
    )

    # --------------------------------------------------------
    # タスク実行
    # --------------------------------------------------------

    overall_success = True

    failed_task_number = None

    failed_task_file = None

    failed_cause = None

    for position in range(
        start_position,
        len(task_entries),
    ):

        task_number, task_file = (
            task_entries[position]
        )

        relative_index = (
            position
            - start_position
            + 1
        )

        execution_total = (
            len(task_entries)
            - start_position
        )

        print()

        print(
            "#" * 70
        )

        print(
            f"# TASK "
            f"{relative_index}/"
            f"{execution_total}: "
            f"{task_file.name}"
        )

        print(
            "#" * 70
        )

        try:

            task = load_yaml(
                task_file
            )

            # ------------------------------------------------
            # resume session
            # ------------------------------------------------

            resume_session_id = None

            if (
                args.resume
                and position == start_position
            ):

                resume_session_id = (
                    find_session_id_from_reports(
                        tasks_dir,
                        task_number,
                        task_file.name,
                    )
                )

                if not resume_session_id:

                    raise RuntimeError(
                        "resumeが指定されていますが、"
                        f"TASK {task_number} "
                        f"({task_file.name}) の"
                        "過去レポートからsession_idを"
                        "取得できませんでした。\n"
                        "通常実行する場合は --resume を"
                        "外してください。"
                    )

                print(
                    "[Runner] resume対象session: "
                    f"{resume_session_id}",
                    flush=True,
                )

                write_report(
                    report_path,
                    f"TASK {task_number}: "
                    "RESUME_SESSION_FOUND\n"
                    f"SESSION_ID: "
                    f"{resume_session_id}\n",
                )

            result = run_claude(
                claude=claude,
                task_file=task_file,
                task_number=task_number,
                task=task,
                settings=settings,
                index=relative_index,
                total=execution_total,
                report_path=report_path,
                resume_session_id=resume_session_id,
            )

            if not result[
                "success"
            ]:

                overall_success = False

                failed_task_number = (
                    task_number
                )

                failed_task_file = (
                    task_file
                )

                failed_cause = (
                    result["cause"]
                )

                print()
                print(
                    "=" * 70
                )

                print(
                    "TASK FAILED"
                )

                print(
                    "=" * 70
                )

                print(
                    f"タスク: {task_file.name}"
                )

                print(
                    f"原因  : {result['cause']}"
                )

                if result[
                    "session_id"
                ]:

                    print(
                        "再開する場合:"
                    )

                    print(
                        f"  python "
                        f"{Path(__file__).name} "
                        f"--tasks \"{tasks_dir}\" "
                        f"--start-task "
                        f"{task_number} "
                        "--resume"
                    )

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

                break

        except Exception as e:

            overall_success = False

            failed_task_number = (
                task_number
            )

            failed_task_file = (
                task_file
            )

            failed_cause = str(e)

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

            write_report(
                report_path,
                f"TASK_NUMBER: {task_number}\n"
                f"TASK_FILE: {task_file.name}\n"
                "RESULT: FAILED\n"
                f"CAUSE: {e}\n",
            )

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

            break

    # --------------------------------------------------------
    # 全体結果
    # --------------------------------------------------------

    report_section(
        report_path,
        "OVERALL RESULT",
    )

    if overall_success:

        print(
            "=" * 70
        )

        print(
            "すべてのタスクが完了しました。"
        )

        print(
            "=" * 70
        )

        write_report(
            report_path,
            "OVERALL_RESULT: SUCCESS\n"
            f"END_TIME: "
            f"{datetime.now().isoformat(timespec='seconds')}\n",
        )

        return 0

    print(
        "=" * 70
    )

    print(
        "ワークフローが失敗しました。"
    )

    print(
        "=" * 70
    )

    write_report(
        report_path,
        "OVERALL_RESULT: FAILED\n"
        f"FAILED_TASK_NUMBER: "
        f"{failed_task_number}\n"
        f"FAILED_TASK_FILE: "
        f"{failed_task_file.name if failed_task_file else '(unknown)'}\n"
        f"FAILED_CAUSE: "
        f"{failed_cause}\n"
        f"END_TIME: "
        f"{datetime.now().isoformat(timespec='seconds')}\n",
    )

    if (
        failed_task_number is not None
        and failed_task_file is not None
    ):

        resume_session_id = (
            find_session_id_from_reports(
                tasks_dir,
                failed_task_number,
                failed_task_file.name,
            )
        )

        if resume_session_id:

            resume_command = (
                f"python {Path(__file__).name} "
                f'--tasks "{tasks_dir}" '
                f"--start-task "
                f"{failed_task_number} "
                "--resume"
            )

            print()
            print(
                "再開コマンド:"
            )

            print(
                f"  {resume_command}"
            )

            write_report(
                report_path,
                "\n"
                "RESUME_COMMAND:\n"
                f"{resume_command}\n",
            )

    return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )