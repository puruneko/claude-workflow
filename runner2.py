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

を指定できる。

tasksフォルダにはsettings.yamlを置くことができ、
全タスク共通のプロンプトやプロンプト変数を定義できる。


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


■ タスクファイルの命名規則
------------------------------------------------------------

以下の形式のYAMLファイルをタスクとして認識する。

    NNN_title.yaml
    NNN_title.yml

NNNは数字の連番。

例:

    001_analyze.yaml
    002_implement.yaml
    010_test.yaml

実行順はファイル名の数字部分で決定する。

settings.yamlはタスクとして実行しない。


■ タスクファイルの仕様
------------------------------------------------------------

各タスクには以下の4項目を指定できる。

    directory:
        Claude Codeを実行する作業ディレクトリ。

    model:
        Claude Codeで使用するモデル。

    prompt:
        そのタスク固有のプロンプト。

    permission:
        Claude Codeの権限モード。
        settings.yamlの共通permissionより優先される。
        省略時はsettings.yamlのpermissionを使用する。

例:

    directory: ~/workspace/my-project
    model: sonnet

    prompt: |
      issue #123を調査して実装してください。
      必要なテストも実行してください。

    permission: bypassPermissions


■ settings.yaml
------------------------------------------------------------

tasks/settings.yamlで全タスク共通設定を指定する。

利用できる設定:

    prompt_prefix:
        全タスクのプロンプトの先頭に追加する文章。

    prompt_suffix:
        全タスクのプロンプトの末尾に追加する文章。

    variables:
        全タスクから利用できるプロンプト変数。

    continue_on_error:
        タスク失敗時に後続タスクを実行するか。

        false:
            エラー発生時にワークフローを停止する。
            デフォルト。

        true:
            エラーが発生しても次のタスクへ進む。

    confirm_each_task:
        タスク実行前に確認するか。

        false:
            自動実行。
            デフォルト。

        true:
            各タスク実行前に確認を求める。

    permission:
        Claude Codeの権限モード。
        タスク側で指定したpermissionが優先される。

        default:
            通常の権限確認。

        acceptEdits:
            ファイル編集を自動許可。

        plan:
            読み取り・調査のみ。編集しない。

        dontAsk:
            権限確認を行わず、許可されていない操作は拒否。

        bypassPermissions:
            権限確認を完全にスキップ。
            Claude Codeの--dangerously-skip-permissions相当。
            デフォルトはdefault。


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


■ プロンプト変数
------------------------------------------------------------

settings.yamlのvariablesに定義した値を、
タスクのpromptから以下の形式で参照できる。

    {{変数名}}

例:

    variables:
      project_name: "Markdown Editor"

タスク:

    prompt: |
      {{project_name}}の実装を行ってください。

実際にClaudeへ渡されるプロンプト:

    Markdown Editorの実装を行ってください。


■ 環境変数
------------------------------------------------------------

OSの環境変数もプロンプト内から参照できる。

形式:

    {{ENV:環境変数名}}

例:

    {{ENV:HOME}}

環境変数が存在しない場合はエラーとしてタスクを実行しない。


■ プロンプトの構成
------------------------------------------------------------

Claude Codeへ渡す最終的なプロンプトは、

    prompt_prefix
    ↓
    タスク固有prompt
    ↓
    prompt_suffix

の順で構成する。

それぞれの部分にプロンプト変数を使用できる。


■ 未定義変数
------------------------------------------------------------

存在しない変数を使用した場合はエラーとする。

例:

    {{unknown_variable}}

この場合、誤ったプロンプトをClaude Codeへ渡さず、
そのタスクをエラーとして扱う。


■ Claude Codeの実行方式
------------------------------------------------------------

Claude Codeは非対話型で実行する。

概念的には以下の形式。

    claude \
      --model <model> \
      -p "<prompt>" \
      --output-format stream-json \
      --verbose

-pを使用するため、Claude Codeから人間への対話的な質問待ちは
想定しない。

そのため、このrunnerは完全自動実行を前提とする。


■ リアルタイム進捗表示
------------------------------------------------------------

Claude Codeの出力形式にはstream-jsonを使用する。

Python側でClaude Codeの標準出力をPopenで受け取り、
JSONイベントを1行ずつリアルタイム処理する。

主に以下を表示する。

    - Claudeのテキスト出力
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


■ Claude Codeの検索方法
------------------------------------------------------------

Claude Codeの実行ファイルはPATHから検索する。

Node.jsのインストール場所やバージョンは決め打ちしない。

内部では概念的に、

    shutil.which("claude")

を使用する。

そのため、WSL2側でPATHが正しく設定されていれば、
Node.jsのバージョン管理方法に依存しない。


■ タスクの実行順
------------------------------------------------------------

tasksフォルダ内のタスクファイルを数字部分でソートする。

例:

    001_a.yaml
    002_b.yaml
    010_c.yaml

の場合、

    001_a
      ↓
    002_b
      ↓
    010_c

の順番で実行する。


■ タスク失敗時の動作
------------------------------------------------------------

continue_on_error=falseの場合:

    001 成功
      ↓
    002 失敗
      ↓
    ワークフロー停止


continue_on_error=trueの場合:

    001 成功
      ↓
    002 失敗
      ↓
    003 実行
      ↓
    004 実行


■ タスク実行前確認
------------------------------------------------------------

confirm_each_task=trueの場合、
各タスク実行前にユーザーへ確認を求める。

    001_analyze.yaml を実行しますか？ [y/N]:

y / yes:
    実行

それ以外:
    スキップ


■ コマンドライン引数
------------------------------------------------------------

--tasksで使用するtasksフォルダを指定できる。

指定なし:

    python runner.py

    ↓

    runner.py/tasks/


指定あり:

    python runner.py --tasks ~/workspace/foo/tasks

    ↓

    ~/workspace/foo/tasks/


■ 依存関係
------------------------------------------------------------

Python標準ライブラリ:

    argparse
    json
    os
    re
    shutil
    subprocess
    sys
    pathlib

外部パッケージ:

    PyYAML

uvを使用する場合:

    uv add pyyaml

実行:

    uv run python runner.py


■ 設計方針
------------------------------------------------------------

このrunnerはワークフローエンジンとして過度に複雑化させない。

責務は以下に限定する。

    1. タスクファイルを読む
    2. 連番順に並べる
    3. 共通設定を適用する
    4. プロンプト変数を展開する
    5. Claude Codeを指定モデル・指定ディレクトリで実行する
    6. Claude Codeの進捗をリアルタイム表示する
    7. 成功・失敗に応じて次のタスクへ進む
    8. runner終了時にClaude Codeも終了する

複雑な状態管理、リトライ制御、タスク間のデータ受け渡し、
並列実行などはこのrunnerでは扱わない。


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
# タスクファイル
# ============================================================

TASK_FILE_PATTERN = re.compile(
    r"^(\d+)_.*\.ya?ml$",
    re.IGNORECASE,
)


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

    Node.jsのバージョンやインストール先は決め打ちしない。
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
    settings.setdefault("permission", "default")

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

    validate_permission(settings["permission"], "settings.yaml")

    return settings


# ============================================================
# Claude Code permission
# ============================================================

VALID_PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "dontAsk",
    "bypassPermissions",
}


def validate_permission(permission, source_name):
    """Claude Codeのpermissionモードを検証する。"""
    if not isinstance(permission, str):
        raise RuntimeError(
            f"{source_name}: 'permission' は文字列で指定してください。"
        )

    permission = permission.strip()

    if permission not in VALID_PERMISSION_MODES:
        allowed = ", ".join(sorted(VALID_PERMISSION_MODES))
        raise RuntimeError(
            f"{source_name}: 不正なpermission '{permission}' です。"
            f"使用可能: {allowed}"
        )

    return permission


def get_task_permission(task, settings, task_file):
    """タスク固有permissionを優先し、未指定なら共通設定を使用する。"""
    if "permission" in task:
        return validate_permission(task["permission"], task_file.name)

    return validate_permission(
        settings.get("permission", "default"),
        "settings.yaml",
    )


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
    """

    event_type = event.get("type")

    if event_type == "assistant":

        message = event.get("message", {})
        content = message.get("content", [])

        for item in content:

            if item.get("type") == "text":

                text = item.get("text", "")

                if text:
                    print(
                        text,
                        flush=True,
                    )

            elif item.get("type") == "tool_use":

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

    elif event_type == "user":

        message = event.get("message", {})
        content = message.get("content", [])

        for item in content:

            if item.get("type") == "tool_result":

                print(
                    "[Tool] 完了",
                    flush=True,
                )

    elif event_type == "result":

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

    elif event_type == "system":

        subtype = event.get("subtype")

        if subtype:

            print(
                f"[Claude] {subtype}",
                flush=True,
            )


# ============================================================
# Claude親プロセス連動
# ============================================================

def set_parent_death_signal():
    """Linux/WSL2でrunner終了時にClaude Codeも終了させる。"""
    if sys.platform != "linux":
        return

    try:
        import ctypes

        libc = ctypes.CDLL(None)
        PR_SET_PDEATHSIG = 1
        result = libc.prctl(
            PR_SET_PDEATHSIG,
            signal.SIGTERM,
            0,
            0,
            0,
        )

        if result != 0:
            raise OSError("prctl(PR_SET_PDEATHSIG) failed")

    except Exception as e:
        print(
            f"WARNING: 親プロセス終了連動の設定に失敗しました: {e}",
            file=sys.stderr,
            flush=True,
        )


def terminate_process_group(process):
    """Claude Codeとその配下のプロセスをまとめて終了する。"""
    if process.poll() is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception as e:
        print(
            f"WARNING: Claudeプロセスの終了に失敗しました: {e}",
            file=sys.stderr,
            flush=True,
        )


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
    """

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

    permission = get_task_permission(
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
        f"ファイル             : {task_file.name}"
    )

    print(
        f"モデル               : {model}"
    )

    print(
        f"作業ディレクトリ     : {directory}"
    )

    print(
        f"Permission            : {permission}"
    )

    print("=" * 70)
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
        "--permission-mode",
        permission,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
    ]

    print(
        "Claude Codeを実行します。"
    )

    print()

    process = subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        preexec_fn=set_parent_death_signal,
    )

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

    except (KeyboardInterrupt, SystemExit):
        terminate_process_group(process)

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        raise

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

    
