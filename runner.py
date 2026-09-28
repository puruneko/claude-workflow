#!/usr/bin/env python3

"""
Claude Code Workflow Runner
===========================

概要
----
tasksフォルダ内の NNN_name.yaml / NNN_name.yml を実行する。

実行管理
--------
同じNNNのタスクは並列実行する。

例:

    001_a.yaml
    001_b.yaml
    002_a.yaml
    003_a.yaml
    003_b.yaml

実行:

    001_a ─┐
    001_b ─┘
        ↓
    両方終了
        ↓
    002_a
        ↓
    002終了
        ↓
    003_a ─┐
    003_b ─┘
        ↓
    両方終了

同じNNNのタスクの一部が失敗しても、
そのNNNに属する他のタスクは最後まで実行する。

そのNNNの全タスク終了後、

    continue_on_error=false
        ↓
    そのNNNに失敗があればワークフロー停止

    continue_on_error=true
        ↓
    次のNNNへ進む


タスクファイル
--------------
    NNN_name.yaml

例:

    001_analyze.yaml
    001_implement.yaml
    002_test.yaml


タスクYAML
----------
    directory: ~/workspace/project
    model: sonnet
    permission: bypassPermissions

    prompt: |
      実装してください。


settings.yaml
-------------
共通設定:

    prompt_prefix: |
      あなたは開発担当者です。

    prompt_suffix: |
      作業結果を報告してください。

    variables:
      project_name: "Markdown Editor"

    permission: bypassPermissions

    continue_on_error: false
    confirm_each_task: false

permission
----------
settings.yamlのpermissionを全タスク共通設定として使用できる。

タスクYAMLにpermissionを指定した場合は、
タスク側の設定を優先する。

例:

settings.yaml:

    permission: bypassPermissions

001_a.yaml:

    permission: acceptEdits

この場合001_a.yamlだけacceptEditsになる。


コマンドライン
--------------
通常:

    python runner.py

tasks指定:

    python runner.py --tasks ~/workspace/foo/tasks

再開番号:

    python runner.py --start-task 3

resume:

    python runner.py --start-task 3 --resume

--resumeだけ指定:

    python runner.py --resume

--start-task N は、NNN番号がN以上のグループから開始する。

--resume は、各タスクの保存済みセッションIDを使用して再開する。


report
------
実行開始時にtasksフォルダへ、

    report_YYYYMMDDHHMMSS.log

を作成する。

全タスクの実行結果、失敗原因、resume情報を保存する。


成功・失敗判定
--------------
Claude Codeのresultイベントを最優先する。

    result.subtype == "success"
        → 成功

それ以外
        → 失敗

resultイベントを取得できなかった場合のみ
プロセス終了コードを使用する。

出力本文に
    credit
    quota
    exceeded
などの文字が含まれていることだけでは、
成功・失敗を判定しない。


ターミナル表示
--------------
全Claudeプロセスの出力をrunner.pyの標準出力へ集約する。

各行には必ずタスク名を付ける。

    <001_a.yaml> Claudeの出力
    <001_b.yaml> Claudeの出力

並列実行時もリアルタイム表示する。


Python終了時
------------
runner.pyがCtrl+C等で停止した場合、
実行中のClaudeプロセスをすべて終了する。
"""


import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


import yaml


# ============================================================
# グローバル
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

TASK_FILE_PATTERN = re.compile(
    r"^(\d+)_.*\.ya?ml$",
    re.IGNORECASE,
)

VARIABLE_PATTERN = re.compile(
    r"\{\{([^{}]+)\}\}"
)

PRINT_LOCK = threading.Lock()

PROCESS_LOCK = threading.Lock()

ACTIVE_PROCESSES = set()

STOP_REQUESTED = False


# ============================================================
# ターミナル出力
# ============================================================

def terminal_print(
    task_name,
    message="",
):
    """
    runner.pyのターミナルへ一元出力する。

    task_nameが指定されている場合:

        <001_test.yaml> message

    となる。
    """

    with PRINT_LOCK:

        if task_name:

            prefix = f"<{task_name}> "

        else:

            prefix = ""

        print(
            prefix + str(message),
            flush=True,
        )


def terminal_block(
    task_name,
    lines,
):
    """
    複数行をタスク名付きで出力する。
    """

    for line in lines.splitlines():

        terminal_print(
            task_name,
            line,
        )


# ============================================================
# report
# ============================================================

def write_report(
    report_path,
    text,
):
    """
    reportファイルへ追記する。
    """

    with PRINT_LOCK:

        with report_path.open(
            "a",
            encoding="utf-8",
        ) as f:

            f.write(text)

            if not text.endswith("\n"):

                f.write("\n")


def create_report(tasks_dir):
    """
    実行単位のreportファイルを作成する。
    """

    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    report_path = (
        tasks_dir
        / f"report_{timestamp}.log"
    )

    report_path.touch(
        exist_ok=False
    )

    return report_path


# ============================================================
# コマンドライン
# ============================================================
def parse_args():
    """
    コマンドライン引数を取得する。

    優先ルール:
        1. コマンドライン引数が1つでも指定されている
           → settings.yamlは読まない
           → コマンドライン引数だけを使用する

        2. コマンドライン引数が1つも指定されていない
           → runner.pyと同じディレクトリのsettings.yamlを読む
           → settings.yamlをコマンドライン引数として扱う

    CLIとsettings.yamlはマージしない。
    """

    parser = argparse.ArgumentParser(
        description="Claude Code Workflow Runner"
    )

    parser.add_argument(
        "--tasks",
        type=Path,
        default=None,
        help=(
            "tasksフォルダを指定します。"
            "指定しない場合はrunner.py直下のtasksを使用します。"
        ),
    )

    parser.add_argument(
        "--start-task",
        type=int,
        default=None,
        help=(
            "この番号以上のタスクグループから実行します。"
        ),
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "保存済みのClaude Codeセッションをresumeします。"
        ),
    )

    # ========================================================
    # コマンドライン引数が指定されている場合
    # ========================================================
    #
    # settings.yamlは一切読まない。
    #

    if len(sys.argv) > 1:

        return parser.parse_args()

    # ========================================================
    # コマンドライン引数がない場合
    # ========================================================

    settings_file = (
        BASE_DIR
        / "settings.yaml"
    )

    # settings.yamlがなければ
    # argparseのデフォルト値で実行する。

    if not settings_file.is_file():

        return parser.parse_args()

    # ========================================================
    # settings.yamlを読む
    # ========================================================

    try:

        with settings_file.open(
            "r",
            encoding="utf-8",
        ) as f:

            settings_data = yaml.safe_load(f)

    except Exception as e:

        raise RuntimeError(
            f"settings.yamlの読み込みに失敗しました: "
            f"{settings_file}\n"
            f"{e}"
        ) from e

    if settings_data is None:

        settings_data = {}

    if not isinstance(
        settings_data,
        dict,
    ):

        raise RuntimeError(
            "settings.yamlのルートは"
            "オブジェクト形式にしてください。"
        )

    # ========================================================
    # settings.yaml → CLI引数へ変換
    # ========================================================

    cli_args = []

    if settings_data.get(
        "tasks"
    ) is not None:

        cli_args.extend(
            [
                "--tasks",
                str(
                    settings_data["tasks"]
                ),
            ]
        )

    start_task = settings_data.get(
        "start-task",
        settings_data.get(
            "start_task"
        ),
    )

    if start_task is not None:

        cli_args.extend(
            [
                "--start-task",
                str(start_task),
            ]
        )

    if settings_data.get(
        "resume",
        False,
    ):

        cli_args.append(
            "--resume"
        )

    return parser.parse_args(
        cli_args
    )

# ============================================================
# tasksフォルダ
# ============================================================

def get_tasks_dir(args):

    if args.tasks is None:

        return (
            BASE_DIR
            / "tasks"
        )

    return (
        args.tasks
        .expanduser()
        .resolve()
    )


# ============================================================
# Claude
# ============================================================

def find_claude():

    claude = shutil.which(
        "claude"
    )

    if claude is None:

        raise RuntimeError(
            "PATHから 'claude' コマンドが見つかりません。"
        )

    return claude


# ============================================================
# YAML
# ============================================================

def load_yaml(path):

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

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            f"{path.name}: YAMLのルートはオブジェクト形式にしてください。"
        )

    return data


# ============================================================
# settings
# ============================================================
def load_settings(tasks_dir):
    """
    tasks/settings.yamlから共通設定を読み込む。

    共通設定:
        prompt_prefix
        prompt_suffix
        variables
        permission
        continue_on_error
        confirm_each_task
        skip_tasks

    skip_tasks:
        NNN
            → そのNNNに属するタスクをすべて除外

        NNN_name
            → NNN_name.yaml / NNN_name.ymlだけ除外
    """

    settings_file = (
        tasks_dir
        / "settings.yaml"
    )

    if not settings_file.is_file():

        raise RuntimeError(
            f"settings.yamlが存在しません: {settings_file}"
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
        "permission",
        None,
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
        "skip_tasks",
        [],
    )

    if not isinstance(
        settings["variables"],
        dict,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'variables' はオブジェクト形式にしてください。"
        )

    if not isinstance(
        settings["continue_on_error"],
        bool,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'continue_on_error' はtrue / falseにしてください。"
        )

    if not isinstance(
        settings["confirm_each_task"],
        bool,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'confirm_each_task' はtrue / falseにしてください。"
        )

    if not isinstance(
        settings["skip_tasks"],
        list,
    ):

        raise RuntimeError(
            "settings.yaml: "
            "'skip_tasks' は配列形式にしてください。"
        )

    # --------------------------------------------------------
    # skip_tasksの形式を検証
    # --------------------------------------------------------

    for value in settings["skip_tasks"]:

        if not isinstance(
            value,
            str,
        ):

            raise RuntimeError(
                "settings.yaml: "
                "skip_tasksの各項目は文字列にしてください。"
            )

        value = value.strip()

        if not value:

            raise RuntimeError(
                "settings.yaml: "
                "skip_tasksに空の項目があります。"
            )

        # NNN または NNN_name の形式だけ許可
        if not re.fullmatch(
            r"\d+(?:_.+)?",
            value,
        ):

            raise RuntimeError(
                "settings.yaml: "
                f"skip_tasksの指定 '{value}' が不正です。"
                " NNN または NNN_name 形式で指定してください。"
            )

    return settings


# ============================================================
# タスク取得
# ============================================================
def get_task_files(
    tasks_dir,
    settings,
):
    """
    tasksフォルダから実行対象のtask定義ファイルを取得する。

    skip_tasks:
        NNN
            → NNN全体を除外

        NNN_name
            → そのtaskファイルだけ除外
    """

    if not tasks_dir.is_dir():

        raise RuntimeError(
            f"tasksフォルダが存在しません: {tasks_dir}"
        )

    skip_tasks = {
        str(value).strip()
        for value in settings.get(
            "skip_tasks",
            [],
        )
    }

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

        number_text = match.group(1)

        number = int(
            number_text
        )

        # ----------------------------------------------------
        # 除外判定
        # ----------------------------------------------------

        task_stem = path.stem

        # NNN指定
        #
        # 例:
        #   skip_tasks:
        #     - 001
        #
        #   → 001_a.yaml
        #   → 001_b.yaml
        #   などをすべて除外
        #

        if number_text in skip_tasks:
            continue

        # NNN_name指定
        #
        # 例:
        #   skip_tasks:
        #     - 001_review
        #
        #   → 001_review.yamlだけ除外
        #

        if task_stem in skip_tasks:
            continue

        tasks.append(
            (
                number,
                path,
            )
        )

    tasks.sort(
        key=lambda item: (
            item[0],
            item[1].name.lower(),
        )
    )

    return tasks

def group_task_files(
    task_files,
):
    """
    NNNごとにタスクをグループ化する。

    戻り値:

        [
            (1, [path1, path2]),
            (2, [path3]),
            ...
        ]
    """

    groups = {}

    for number, path in task_files:

        groups.setdefault(
            number,
            [],
        ).append(path)

    return sorted(
        groups.items(),
        key=lambda item: item[0],
    )


# ============================================================
# 変数
# ============================================================
def expand_variables(
    text,
    variables,
    source_name,
):
    """
    変数を展開する。

    通常変数:
        {{project_name}}

    runner.pyが定義する特殊変数:
        {{__NNN}}
        {{__taskname}}
        {{__filename}}
        {{__model}}
        {{__directory}}
        {{__permission}}

    __プレフィックスは特殊変数を識別するためのもの。

    注意:
        __NNN
        __taskname

    のように{{}}で囲まれていない記述は展開しない。
    """

    def replace(match):

        key = match.group(1).strip()

        # ----------------------------------------------------
        # 環境変数
        # ----------------------------------------------------

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
                    f"環境変数 '{env_name}' が"
                    "設定されていません。"
                )

            return value

        # ----------------------------------------------------
        # 変数存在確認
        # ----------------------------------------------------

        if key not in variables:

            raise RuntimeError(
                f"{source_name}: "
                f"未定義の変数 "
                f"'{{{{{key}}}}}' が使用されています。"
            )

        value = variables[key]

        if value is None:

            return ""

        return str(value)

    return VARIABLE_PATTERN.sub(
        replace,
        str(text),
    )


# ============================================================
# prompt
# ============================================================

def build_prompt(
    task,
    settings,
    task_file,
):

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

    if "prompt" not in task:

        raise RuntimeError(
            f"{task_file.name}: promptがありません。"
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

    return "\n\n".join(
        parts
    )


# ============================================================
# permission
# ============================================================

def get_permission(
    task,
    settings,
):

    # タスク側を優先
    if "permission" in task:

        value = task["permission"]

    else:

        value = settings.get(
            "permission"
        )

    if value is None:

        return None

    value = str(
        value
    ).strip()

    if not value:

        return None

    return value


# ============================================================
# task検証
# ============================================================

def validate_task(
    task,
    task_file,
):

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

    if not str(
        task["directory"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: directoryが空です。"
        )

    if not str(
        task["model"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: modelが空です。"
        )

    if not str(
        task["prompt"]
    ).strip():

        raise RuntimeError(
            f"{task_file.name}: promptが空です。"
        )


# ============================================================
# Claudeプロセス管理
# ============================================================

def register_process(
    process,
):

    with PROCESS_LOCK:

        ACTIVE_PROCESSES.add(
            process
        )


def unregister_process(
    process,
):

    with PROCESS_LOCK:

        ACTIVE_PROCESSES.discard(
            process
        )


def terminate_process(
    process,
):

    if process is None:
        return

    if process.poll() is not None:
        return

    try:

        if os.name == "nt":

            process.terminate()

        else:

            os.killpg(
                process.pid,
                signal.SIGTERM,
            )

    except (
        ProcessLookupError,
        PermissionError,
    ):

        pass


def terminate_all_processes():

    with PROCESS_LOCK:

        processes = list(
            ACTIVE_PROCESSES
        )

    for process in processes:

        terminate_process(
            process
        )


def signal_handler(
    signum,
    frame,
):

    global STOP_REQUESTED

    STOP_REQUESTED = True

    terminal_print(
        "",
        "Python停止要求を受信しました。"
        "実行中のClaude Codeを停止します。",
    )

    terminate_all_processes()


# ============================================================
# Claudeイベント表示
# ============================================================

def print_claude_event(
    task_name,
    event,
):

    event_type = event.get(
        "type"
    )

    if event_type == "assistant":

        message = event.get(
            "message",
            {},
        )

        content = message.get(
            "content",
            [],
        )

        for item in content:

            item_type = item.get(
                "type"
            )

            if item_type == "text":

                text = item.get(
                    "text",
                    "",
                )

                if text:

                    terminal_block(
                        task_name,
                        text,
                    )

            elif item_type == "tool_use":

                tool_name = item.get(
                    "name",
                    "unknown",
                )

                tool_input = item.get(
                    "input",
                    {},
                )

                terminal_print(
                    task_name,
                    f"[Tool] {tool_name}",
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

                        terminal_print(
                            task_name,
                            f"       {file_path}",
                        )

                elif tool_name == "Bash":

                    command = tool_input.get(
                        "command",
                        "",
                    )

                    if command:

                        terminal_print(
                            task_name,
                            f"       $ {command}",
                        )

                elif tool_name == "Glob":

                    pattern = tool_input.get(
                        "pattern",
                        "",
                    )

                    if pattern:

                        terminal_print(
                            task_name,
                            f"       {pattern}",
                        )

                elif tool_name == "Grep":

                    pattern = tool_input.get(
                        "pattern",
                        "",
                    )

                    if pattern:

                        terminal_print(
                            task_name,
                            f"       {pattern}",
                        )

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

                terminal_print(
                    task_name,
                    "[Tool] 完了",
                )

    elif event_type == "result":

        subtype = event.get(
            "subtype"
        )

        if subtype == "success":

            terminal_print(
                task_name,
                "[Claude] 完了",
            )

        else:

            terminal_print(
                task_name,
                f"[Claude] 終了: {subtype}",
            )

    elif event_type == "system":

        subtype = event.get(
            "subtype"
        )

        if subtype:

            terminal_print(
                task_name,
                f"[Claude] {subtype}",
            )


# ============================================================
# credit / limit原因判定
# ============================================================

def is_credit_limit_message(
    text,
):

    lowered = text.lower()

    patterns = (
        "usage limit",
        "rate limit",
        "too many requests",
        "quota exceeded",
        "credit limit",
        "credits exhausted",
        "usage limit reached",
        "weekly limit",
        "five-hour limit",
        "5-hour limit",
    )

    return any(
        pattern in lowered
        for pattern in patterns
    )


# ============================================================
# Claude実行
# ============================================================

def run_claude(
    claude,
    task_file,
    task,
    settings,
    index,
    total,
    report_path,
    resume_session_id=None,
):

    global STOP_REQUESTED

    task_name = task_file.name

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
    )

    prompt = build_prompt(
        task,
        settings,
        task_file,
    )

    if not directory.is_dir():

        raise RuntimeError(
            f"{task_name}: "
            f"作業ディレクトリが存在しません: "
            f"{directory}"
        )

    # --------------------------------------------------------
    # 開始表示
    # --------------------------------------------------------

    terminal_print(
        task_name,
        "=" * 70,
    )

    terminal_print(
        task_name,
        f"TASK {index}/{total}",
    )

    terminal_print(
        task_name,
        f"モデル: {model}",
    )

    terminal_print(
        task_name,
        f"作業ディレクトリ: {directory}",
    )

    terminal_print(
        task_name,
        "permission: "
        + (
            permission
            if permission
            else "(Claude Code default)"
        ),
    )

    terminal_print(
        task_name,
        "resume: "
        + (
            resume_session_id
            if resume_session_id
            else "(new)"
        ),
    )

    terminal_print(
        task_name,
        "=" * 70,
    )

    # --------------------------------------------------------
    # prompt表示
    # --------------------------------------------------------

    terminal_print(
        task_name,
        "---------------- PROMPT ----------------",
    )

    terminal_block(
        task_name,
        prompt,
    )

    terminal_print(
        task_name,
        "-------------- END PROMPT --------------",
    )

    # --------------------------------------------------------
    # confirm
    # --------------------------------------------------------

    if settings[
        "confirm_each_task"
    ]:

        with PRINT_LOCK:

            answer = input(
                f"<{task_name}> 実行しますか？ [y/N]: "
            ).strip().lower()

        if answer not in (
            "y",
            "yes",
        ):

            terminal_print(
                task_name,
                "スキップしました。",
            )

            write_report(
                report_path,
                f"""
[TASK {task_name}]
RESULT: SKIPPED
""",
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
    # Claude command
    # --------------------------------------------------------

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

    if resume_session_id:

        command.extend(
            [
                "--resume",
                resume_session_id,
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

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    write_report(
        report_path,
        "\n"
        + "=" * 80
        + "\n"
        + f"TASK: {task_name}\n"
        + f"MODEL: {model}\n"
        + f"DIRECTORY: {directory}\n"
        + f"PERMISSION: "
        + (
            permission
            if permission
            else "(Claude Code default)"
        )
        + "\n"
        + f"RESUME: "
        + (
            resume_session_id
            if resume_session_id
            else "(new)"
        )
        + "\n"
        + f"START: "
        + datetime.now().isoformat(
            timespec="seconds"
        )
        + "\n"
        + "\nPROMPT:\n"
        + "-" * 40
        + "\n"
        + prompt
        + "\n"
        + "-" * 40
        + "\n",
    )

    terminal_print(
        task_name,
        "Claude Codeを実行します。",
    )

    # --------------------------------------------------------
    # Popen
    # --------------------------------------------------------

    popen_kwargs = {
        "cwd": directory,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }

    if os.name == "nt":

        popen_kwargs[
            "creationflags"
        ] = subprocess.CREATE_NEW_PROCESS_GROUP

    else:

        popen_kwargs[
            "start_new_session"
        ] = True

    process = subprocess.Popen(
        command,
        **popen_kwargs,
    )

    register_process(
        process
    )

    output_buffer = []

    result_event_received = False

    result_event_success = False

    result_event_subtype = None

    session_id = (
        resume_session_id
    )

    return_code = None

    try:

        if process.stdout is not None:

            for line in process.stdout:

                if STOP_REQUESTED:

                    break

                line = line.rstrip(
                    "\r\n"
                )

                if not line:

                    continue

                output_buffer.append(
                    line
                )

                try:

                    event = json.loads(
                        line
                    )

                except json.JSONDecodeError:

                    terminal_print(
                        task_name,
                        line,
                    )

                    write_report(
                        report_path,
                        f"<{task_name}> {line}\n",
                    )

                    continue

                # session_id
                event_session_id = event.get(
                    "session_id"
                )

                if event_session_id:

                    session_id = (
                        event_session_id
                    )

                print_claude_event(
                    task_name,
                    event,
                )

                if event.get(
                    "type"
                ) == "result":

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

                    write_report(
                        report_path,
                        "RESULT_EVENT:\n"
                        + json.dumps(
                            event,
                            ensure_ascii=False,
                        )
                        + "\n",
                    )

        if STOP_REQUESTED:

            terminate_process(
                process
            )

        return_code = process.wait()

    finally:

        unregister_process(
            process
        )

    # --------------------------------------------------------
    # Python停止
    # --------------------------------------------------------

    if STOP_REQUESTED:

        success = False

        cause = (
            "runner.pyの停止要求により"
            "Claude Codeを停止しました。"
        )

        result = "STOPPED"

    # --------------------------------------------------------
    # 正常な成功・失敗判定
    # --------------------------------------------------------

    elif result_event_received:

        success = (
            result_event_success
        )

        if success:

            result = "SUCCESS"

            cause = None

        else:

            result = "FAILED"

            cause = (
                "Claude Codeのresultイベントが"
                f"success以外でした: "
                f"{result_event_subtype}"
            )

    else:

        if return_code == 0:

            success = True

            result = "SUCCESS"

            cause = None

        else:

            success = False

            result = "FAILED"

            cause = (
                f"Claude Codeが終了コード "
                f"{return_code} で終了しました。"
            )

    # --------------------------------------------------------
    # credit原因
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
                f"{cause}\n"
                "使用量制限・クレジット・"
                "rate limit等に関するメッセージが"
                "出力されています。"
            )

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    write_report(
        report_path,
        f"""
RESULT: {result}
RETURN_CODE: {return_code}
RESULT_EVENT_RECEIVED: {result_event_received}
RESULT_SUBTYPE: {result_event_subtype}
SESSION_ID: {session_id}
END: {datetime.now().isoformat(timespec="seconds")}
CAUSE: {cause if cause else "(none)"}
CREDIT_LIMIT_DETECTED: {credit_limit}
""",
    )

    # --------------------------------------------------------
    # 失敗時のresume情報
    # --------------------------------------------------------

    if not success:

        write_report(
            report_path,
            "\nRESUME INFORMATION\n"
            + "-" * 40
            + "\n"
            + (
                f"SESSION_ID: {session_id}\n"
                if session_id
                else
                "SESSION_ID: (取得できませんでした)\n"
            )
            + f"再開番号: "
            + re.match(
                r"^(\d+)_",
                task_name,
            ).group(1)
            + "\n"
            + (
                "既存セッションを再開する場合:\n"
                f"python {Path(__file__).name} "
                f"--tasks \"{report_path.parent}\" "
                f"--start-task "
                f"{re.match(r'^(\d+)_', task_name).group(1)} "
                "--resume\n"
                if session_id
                else
                "既存セッションのIDを取得できなかったため、"
                "--resumeは利用できません。\n"
            )
            + "\n",
        )

    terminal_print(
        task_name,
        f"終了: {result}",
    )

    if cause:

        terminal_print(
            task_name,
            f"原因: {cause}",
        )

    return {
        "success": success,
        "skipped": False,
        "session_id": session_id,
        "return_code": return_code,
        "cause": cause,
        "credit_limit": credit_limit,
        "result_event_received": result_event_received,
        "result_event_success": result_event_success,
        "result_event_subtype": result_event_subtype,
        "task_file": task_file,
    }


# ============================================================
# タスク1件の実行ラッパー
# ============================================================
def execute_task(
    claude,
    task_file,
    settings,
    task_index,
    total_tasks,
    report_path,
    resume,
    resume_sessions,
):
    task_name = task_file.name

    try:

        task = load_yaml(
            task_file
        )

        # ----------------------------------------------------
        # task定義用の特殊変数
        # ----------------------------------------------------

        match = TASK_FILE_PATTERN.match(
            task_name
        )

        if match is None:

            raise RuntimeError(
                f"{task_name}: "
                "タスクファイル名が"
                "NNN_name.yaml形式ではありません。"
            )

        nnn = match.group(1)

        taskname = task_file.stem[
            len(nnn) + 1:
        ]

        # ----------------------------------------------------
        # 変数を構築
        #
        # 通常変数:
        #   settings.yamlのvariables
        #
        # 特殊変数:
        #   __NNN
        #   __taskname
        #   __filename
        #   __model
        #   __directory
        #   __permission
        # ----------------------------------------------------

        variables = dict(
            settings.get(
                "variables",
                {},
            )
        )

        variables.update(
            {
                "__NNN": nnn,
                "__taskname": taskname,
                "__filename": task_name,
            }
        )

        # ----------------------------------------------------
        # task定義内の値を展開
        #
        # model / directory / permission / promptなど
        # prompt以外もすべて対象。
        # ----------------------------------------------------

        def expand_value(
            value,
            current_variables,
        ):

            if isinstance(
                value,
                str,
            ):

                return expand_variables(
                    value,
                    current_variables,
                    task_name,
                )

            if isinstance(
                value,
                dict,
            ):

                return {
                    key: expand_value(
                        child,
                        current_variables,
                    )
                    for key, child in value.items()
                }

            if isinstance(
                value,
                list,
            ):

                return [
                    expand_value(
                        child,
                        current_variables,
                    )
                    for child in value
                ]

            return value

        # ----------------------------------------------------
        # task自身の項目を特殊変数として登録
        #
        # 例:
        #
        # model: sonnet
        #
        # → {{__model}}
        #
        # directory: ~/workspace/project
        #
        # → {{__directory}}
        # ----------------------------------------------------

        for key, value in task.items():

            if isinstance(
                value,
                (
                    str,
                    int,
                    float,
                    bool,
                ),
            ):

                variables[
                    f"__{key}"
                ] = value

        # ----------------------------------------------------
        # task定義全体を展開
        #
        # これにより、
        #
        # model: "{{model_default}}"
        # directory: "{{project_dir}}"
        #
        # prompt:
        #   {{__model}}
        #
        # のような記述が可能。
        # ----------------------------------------------------

        task = expand_value(
            task,
            variables,
        )

        # ----------------------------------------------------
        # 展開後のtask項目を特殊変数として再登録
        #
        # modelやdirectory自身が
        # {{変数}}を使っていた場合に対応。
        # ----------------------------------------------------

        for key, value in task.items():

            if isinstance(
                value,
                (
                    str,
                    int,
                    float,
                    bool,
                ),
            ):

                variables[
                    f"__{key}"
                ] = value

        # ----------------------------------------------------
        # もう一度展開
        #
        # 例:
        #
        # model: "{{model_default}}"
        # prompt: "{{__model}}で実行"
        # ----------------------------------------------------

        task = expand_value(
            task,
            variables,
        )

        # ----------------------------------------------------
        # resume
        # ----------------------------------------------------

        resume_session_id = None

        if resume:

            resume_session_id = (
                resume_sessions.get(
                    task_name
                )
            )

        result = run_claude(
            claude=claude,
            task_file=task_file,
            task=task,
            settings=settings,
            index=task_index,
            total=total_tasks,
            report_path=report_path,
            resume_session_id=resume_session_id,
        )

        return result

    except Exception as e:

        terminal_print(
            task_name,
            f"ERROR: {e}",
        )

        write_report(
            report_path,
            "\n"
            + "=" * 80
            + "\n"
            + f"TASK: {task_name}\n"
            + "RESULT: FAILED\n"
            + f"CAUSE: {e}\n",
        )

        return {
            "success": False,
            "skipped": False,
            "session_id": None,
            "return_code": None,
            "cause": str(e),
            "credit_limit": False,
            "result_event_received": False,
            "result_event_success": False,
            "result_event_subtype": None,
            "task_file": task_file,
        }


# ============================================================
# main
# ============================================================

def main():

    global STOP_REQUESTED

    signal.signal(
        signal.SIGINT,
        signal_handler,
    )

    if hasattr(
        signal,
        "SIGTERM",
    ):

        signal.signal(
            signal.SIGTERM,
            signal_handler,
        )

    args = parse_args()

    tasks_dir = get_tasks_dir(
        args
    )

    terminal_print(
        "",
        "=" * 70,
    )

    terminal_print(
        "",
        "Claude Code Workflow Runner",
    )

    terminal_print(
        "",
        "=" * 70,
    )

    terminal_print(
        "",
        f"Runner: {BASE_DIR}",
    )

    terminal_print(
        "",
        f"Tasks : {tasks_dir}",
    )

    # --------------------------------------------------------
    # Claude
    # --------------------------------------------------------

    try:

        claude = find_claude()

    except RuntimeError as e:

        terminal_print(
            "",
            f"ERROR: {e}",
        )

        return 1

    terminal_print(
        "",
        f"Claude: {claude}",
    )

    # --------------------------------------------------------
    # settings
    # --------------------------------------------------------

    try:

        settings = load_settings(
            tasks_dir
        )

    except RuntimeError as e:

        terminal_print(
            "",
            f"ERROR: {e}",
        )

        return 1

    # --------------------------------------------------------
    # task取得
    # --------------------------------------------------------

    try:

        task_files = get_task_files(
    tasks_dir,
    settings,
)

    except RuntimeError as e:

        terminal_print(
            "",
            f"ERROR: {e}",
        )

        return 1

    if not task_files:

        terminal_print(
            "",
            "実行するタスクファイルがありません。",
        )

        return 0

    groups = group_task_files(
        task_files
    )

    # --------------------------------------------------------
    # start-task
    # --------------------------------------------------------

    if args.start_task is not None:

        groups = [
            group
            for group in groups
            if group[0] >= args.start_task
        ]

    if not groups:

        terminal_print(
            "",
            "指定された開始番号以降に"
            "実行するタスクがありません。",
        )

        return 0

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    try:

        report_path = create_report(
            tasks_dir
        )

    except Exception as e:

        terminal_print(
            "",
            f"report作成失敗: {e}",
        )

        return 1

    write_report(
        report_path,
        "=" * 80
        + "\n"
        + "Claude Code Workflow Runner\n"
        + f"START: {datetime.now().isoformat(timespec='seconds')}\n"
        + f"TASKS: {tasks_dir}\n"
        + f"RESUME: {args.resume}\n"
        + f"START_TASK: {args.start_task}\n"
        + "=" * 80
        + "\n",
    )

    terminal_print(
        "",
        f"Report: {report_path}",
    )

    # --------------------------------------------------------
    # 実行予定
    # --------------------------------------------------------

    terminal_print(
        "",
        "",
    )

    terminal_print(
        "",
        "実行グループ:",
    )

    for number, files in groups:

        names = ", ".join(
            path.name
            for path in files
        )

        terminal_print(
            "",
            f"  {number:03d}: {names}",
        )

    terminal_print(
        "",
        "",
    )

    # --------------------------------------------------------
    # resume情報
    #
    # reportから直前のSESSION_IDを取得する。
    # --------------------------------------------------------

    resume_sessions = {}

    if args.resume:

        # 現在のreportではなく、
        # tasks内に存在する過去のreportを探す。
        reports = sorted(
            tasks_dir.glob(
                "report_*.log"
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for old_report in reports:

            if old_report == report_path:
                continue

            try:

                text = old_report.read_text(
                    encoding="utf-8"
                )

            except OSError:

                continue

            current_task = None

            for line in text.splitlines():

                if line.startswith(
                    "TASK: "
                ):

                    current_task = line[
                        len("TASK: "):
                    ].strip()

                elif (
                    line.startswith(
                        "SESSION_ID: "
                    )
                    and current_task
                ):

                    session_id = line[
                        len("SESSION_ID: "):
                    ].strip()

                    if (
                        session_id
                        and session_id
                        != "(取得できませんでした)"
                        and session_id
                        != "(none)"
                        and current_task
                        not in resume_sessions
                    ):

                        resume_sessions[
                            current_task
                        ] = session_id

    # --------------------------------------------------------
    # 全タスク数
    # --------------------------------------------------------

    total_tasks = sum(
        len(files)
        for _, files in groups
    )

    task_index_map = {}

    counter = 1

    for _, files in groups:

        for path in files:

            task_index_map[
                path
            ] = counter

            counter += 1

    # --------------------------------------------------------
    # NNN単位で実行
    # --------------------------------------------------------

    workflow_failed = False

    for number, files in groups:

        if STOP_REQUESTED:

            workflow_failed = True

            break

        terminal_print(
            "",
            "",
        )

        terminal_print(
            "",
            "#" * 80,
        )

        terminal_print(
            "",
            f"### TASK GROUP {number:03d}",
        )

        terminal_print(
            "",
            f"### {len(files)} task(s) を並列実行します。",
        )

        terminal_print(
            "",
            "#" * 80,
        )

        write_report(
            report_path,
            "\n"
            + "#" * 80
            + "\n"
            + f"TASK GROUP: {number:03d}\n"
            + f"TASK COUNT: {len(files)}\n"
            + "TASKS:\n"
            + "\n".join(
                f"  - {path.name}"
                for path in files
            )
            + "\n"
            + "#" * 80
            + "\n",
        )

        results = []

        # ----------------------------------------------------
        # 同じNNNを並列実行
        # ----------------------------------------------------

        max_workers = len(
            files
        )

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=(
                f"task-{number:03d}"
            ),
        ) as executor:

            futures = {}

            for task_file in files:

                future = executor.submit(
                    execute_task,
                    claude,
                    task_file,
                    settings,
                    task_index_map[
                        task_file
                    ],
                    total_tasks,
                    report_path,
                    args.resume,
                    resume_sessions,
                )

                futures[
                    future
                ] = task_file

            # ------------------------------------------------
            # 全タスク終了まで待つ
            # ------------------------------------------------

            for future in as_completed(
                futures
            ):

                task_file = futures[
                    future
                ]

                try:

                    result = future.result()

                except Exception as e:

                    result = {
                        "success": False,
                        "task_file": task_file,
                        "cause": str(e),
                    }

                    terminal_print(
                        task_file.name,
                        f"ERROR: {e}",
                    )

                results.append(
                    result
                )

        # ----------------------------------------------------
        # グループ結果
        # ----------------------------------------------------

        failed = [
            result
            for result in results
            if not result.get(
                "success",
                False,
            )
        ]

        succeeded = [
            result
            for result in results
            if result.get(
                "success",
                False,
            )
        ]

        terminal_print(
            "",
            "",
        )

        terminal_print(
            "",
            f"### TASK GROUP {number:03d} 終了",
        )

        terminal_print(
            "",
            f"### 成功: {len(succeeded)}",
        )

        terminal_print(
            "",
            f"### 失敗: {len(failed)}",
        )

        write_report(
            report_path,
            f"\n"
            f"TASK GROUP {number:03d} RESULT\n"
            f"SUCCESS: {len(succeeded)}\n"
            f"FAILED: {len(failed)}\n",
        )

        # ----------------------------------------------------
        # グループ失敗
        # ----------------------------------------------------

        if failed:

            workflow_failed = True

            for result in failed:

                task_file = result.get(
                    "task_file"
                )

                task_name = (
                    task_file.name
                    if task_file
                    else "(unknown)"
                )

                cause = result.get(
                    "cause",
                    "(unknown)",
                )

                terminal_print(
                    task_name,
                    f"失敗原因: {cause}",
                )

            if settings[
                "continue_on_error"
            ]:

                terminal_print(
                    "",
                    "continue_on_error=true "
                    "のため次のNNNへ進みます。",
                )

                continue

            terminal_print(
                "",
                f"NNN={number:03d} に失敗があるため"
                "ワークフローを停止します。",
            )

            break

        terminal_print(
            "",
            f"NNN={number:03d} は全タスク成功。",
        )

    # --------------------------------------------------------
    # 最終結果
    # --------------------------------------------------------

    write_report(
        report_path,
        "\n"
        + "=" * 80
        + "\n"
        + f"WORKFLOW RESULT: "
        + (
            "FAILED"
            if workflow_failed
            else "SUCCESS"
        )
        + "\n"
        + f"END: "
        + datetime.now().isoformat(
            timespec="seconds"
        )
        + "\n"
        + "=" * 80
        + "\n",
    )

    terminal_print(
        "",
        "",
    )

    terminal_print(
        "",
        "=" * 70,
    )

    if workflow_failed:

        terminal_print(
            "",
            "ワークフロー終了: FAILED",
        )

    else:

        terminal_print(
            "",
            "すべてのタスクが完了しました。",
        )

    terminal_print(
        "",
        f"Report: {report_path}",
    )

    terminal_print(
        "",
        "=" * 70,
    )

    return (
        1
        if workflow_failed
        else 0
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        STOP_REQUESTED = True

        terminate_all_processes()

        terminal_print(
            "",
            "runner.pyを停止しました。"
            "実行中のClaude Codeも停止しました。",
        )

        sys.exit(130)

    except Exception as e:

        terminate_all_processes()

        terminal_print(
            "",
            f"致命的エラー: {e}",
        )

        sys.exit(1)