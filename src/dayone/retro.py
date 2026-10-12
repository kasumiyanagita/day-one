"""レトロな見た目での起動(Windows Terminal)。

フォント・色味・ブラウン管風の効果・カーソルの細かな太さは、アプリではなく端末の側で決まる。
そこで Windows Terminal に「day-one」というプロファイルを 1 つ用意し、`dayone --retro` と打たれたら
そのプロファイルで新しいタブを開いて、そちらでアプリを動かす。`dayone` だけなら、いまの端末で通常のデザインのまま動く。

プロファイルの中身(フォント、配色など)は、レトロのデザイン(designs/retro.py)の TERMINAL に書いてある。
ここは、それを Windows Terminal の設定ファイルへ書き込む役だけを受け持つ。

プロファイルの追加と削除は `dayone --retro-install` / `dayone --retro-remove` で行う。
プロファイルを追加していない環境や、Windows Terminal のない環境では、タブを開けないので、
いまの端末のままレトロのデザイン(色と記号)だけで動かす。
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import subprocess

from dayone import design
from dayone.designs import retro as retro_design

NAME = "day-one"
GUID = "{d5a7c0de-da71-4e5e-9a10-0000da701e00}"
SCHEME = "day-one retro"
SETTINGS = "/mnt/c/Users/*/AppData/Local/Packages/Microsoft.WindowsTerminal*/LocalState/settings.json"
# レトロなタブの中で動いている印。これがあれば、もう一度タブを開き直さず、レトロのデザインで描く
INSIDE_VAR = design.RETRO_VAR

def _settings_path() -> str | None:
    found = glob.glob(SETTINGS)
    return found[0] if found else None


def _command(arguments: list[str]) -> list[str]:
    """レトロなタブの中で dayone を起動するコマンド(wsl.exe から始まる)。"""
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    inner = shlex.join(["dayone", *arguments])
    return ["wsl.exe", "-d", distro, "--cd", "~", "--", "env", f"{INSIDE_VAR}=1", "bash", "-lc", inner]


def _profile() -> dict:
    return {
        "guid": GUID,
        "name": NAME,
        "commandline": subprocess.list2cmdline(_command([])),
        "colorScheme": SCHEME,
        "hidden": False,
        # フォント、カーソル、ブラウン管風の効果は、レトロのデザインが決める
        **{key: value for key, value in retro_design.TERMINAL.items() if key != "colors"},
    }


def _scheme() -> dict:
    return {"name": SCHEME, **retro_design.TERMINAL["colors"]}


def update_profile(install: bool) -> str:
    """Windows Terminal の設定に、day-one 用のプロファイルと配色を足す(install=False なら取り除く)。

    ほかのプロファイルは変えない。初めて書き換える前に、同じ場所に控えを残す。結果を知らせる文を返す。
    """
    path = _settings_path()
    if path is None:
        return "Windows Terminal の設定ファイルが見つかりません。"
    with open(path, encoding="utf-8-sig") as f:
        settings = json.load(f)
    backup = path + ".before-day-one"
    if not os.path.exists(backup):
        shutil.copy(path, backup)

    profiles = settings.setdefault("profiles", {}).setdefault("list", [])
    schemes = settings.setdefault("schemes", [])
    profiles[:] = [p for p in profiles if p.get("guid") != GUID]
    schemes[:] = [s for s in schemes if s.get("name") != SCHEME]
    if install:
        profiles.append(_profile())
        schemes.append(_scheme())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)
        f.write("\n")
    done = "レトロな見た目のプロファイルを追加しました。" if install else "レトロな見た目のプロファイルを取り除きました。"
    return f"{done}\n設定ファイル: {path}\n変更前の控え: {backup}"


def inside_tab() -> bool:
    """すでにレトロなタブの中で動いているか。"""
    return bool(os.environ.get(INSIDE_VAR))


def can_open_tab() -> bool:
    """レトロなタブを開けるか(Windows Terminal があり、day-one のプロファイルが追加してあるか)。"""
    path = _settings_path()
    if path is None or shutil.which("wt.exe") is None:
        return False
    try:
        with open(path, encoding="utf-8-sig") as f:
            return GUID in f.read()
    except OSError:
        return False


def open_tab(arguments: list[str]) -> bool:
    """day-one のプロファイルで新しいタブを開き、そこで dayone を起動する。開けたら True。"""
    try:
        # -w 0: いま使っているウィンドウに、タブとして開く
        result = subprocess.run(
            ["wt.exe", "-w", "0", "new-tab", "-p", NAME, "--", *_command(arguments)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
