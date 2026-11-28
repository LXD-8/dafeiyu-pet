# -*- coding: utf-8 -*-
"""平台差异集中处理：字体族与开机自启。

桌宠主程序只关心「要做什么」，这里负责「每个平台怎么做」：

- Windows：启动目录里的 ``.lnk``（WScript.Shell 建快捷方式），字体 Microsoft YaHei UI；
- macOS：``~/Library/LaunchAgents/<label>.plist``（LaunchAgent），字体 PingFang SC。

这样 ``桌宠.py`` 里不再出现 ``ctypes.windll`` / ``os.environ["APPDATA"]`` 这类
只在一个平台上成立的代码，也方便在没有 GUI 的环境里单测。
"""
import logging
import os
import plistlib
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

AUTOSTART_LABEL = "com.dafeiyu.pet"
AUTOSTART_NAME = "大肥鱼桌宠"


# ---------- 字体 ----------
def ui_font_family():
    """界面正文字体族。"""
    if IS_MACOS:
        return "PingFang SC"
    if IS_WINDOWS:
        return "Microsoft YaHei UI"
    return "Sans Serif"


def emoji_font_family():
    """彩色 emoji 字体族（喂食面板的 🐟🍰🍭🍡💎）。"""
    if IS_MACOS:
        return "Apple Color Emoji"
    if IS_WINDOWS:
        return "Segoe UI Emoji"
    return "Sans Serif"


# ---------- macOS：LaunchAgent ----------
def macos_autostart_path():
    return os.path.expanduser("~/Library/LaunchAgents/{}.plist".format(AUTOSTART_LABEL))


def macos_launch_agent_plist(program_args, working_dir, log_path, label=AUTOSTART_LABEL):
    """生成 LaunchAgent plist（bytes），单独拆出来是为了能直接单测。"""
    payload = {
        "Label": label,
        "ProgramArguments": [str(a) for a in program_args],
        "WorkingDirectory": str(working_dir),
        "RunAtLoad": True,
        "KeepAlive": False,
        # Interactive：登录后在 Aqua 会话里起，窗口才能连上 WindowServer
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "LimitLoadToSessionType": "Aqua",
    }
    return plistlib.dumps(payload)


def _macos_launchctl(*args):
    """best-effort：launchctl 缺失或调用失败都不影响自启项本身（写/删 plist）。"""
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True)
    except OSError:
        logging.warning("launchctl 不可用，跳过: %s", " ".join(args))
        return None


def _macos_service_target():
    return "gui/{}/{}".format(os.getuid(), AUTOSTART_LABEL)


def _macos_install(program_args, working_dir, log_path):
    """写 LaunchAgent plist；launchd 在下次登录时加载它。

    这里刻意不 ``bootstrap`` / ``bootout``：

    - ``bootstrap`` 会按 ``RunAtLoad`` 立刻再拉起一份桌宠（勾选时本进程正在跑），
      菜单栏会多一个图标、日志和 config 两个进程抢着写；
    - ``bootout`` 会把 job 拥有的进程一起杀掉——如果桌宠正是登录时由这个 job 拉起的，
      取消自启就等于把桌宠自己杀掉。

    写进 ``~/Library/LaunchAgents`` 本身就是「下次登录启动」的声明，``enable`` 只是清掉
    可能残留的 disable 位。
    """
    path = macos_autostart_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _macos_launchctl("enable", _macos_service_target())
    with open(path, "wb") as f:
        f.write(macos_launch_agent_plist(program_args, working_dir, log_path))


def _macos_remove():
    """删掉 plist 即取消自启；``disable`` 兜底，避免被 bootstrap 过的 job 清理后又被拉起。"""
    path = macos_autostart_path()
    _macos_launchctl("disable", _macos_service_target())
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _macos_enabled():
    return os.path.exists(macos_autostart_path())


# ---------- Windows：启动目录快捷方式 ----------
def windows_startup_lnk():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("找不到 APPDATA 环境变量，无法定位启动目录")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", AUTOSTART_NAME + ".lnk")


def _ps_quote(text):
    return str(text).replace("'", "''")


def _windows_install(program_args, working_dir, _log_path):
    lnk = windows_startup_lnk()
    target = program_args[0]
    args = " ".join('"{}"'.format(a) for a in program_args[1:])
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
          "$s.TargetPath='{}';$s.Arguments='{}';$s.WorkingDirectory='{}';$s.Save()"
          .format(_ps_quote(lnk), _ps_quote(target), _ps_quote(args),
                  _ps_quote(working_dir)))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                   check=True)


def _windows_remove():
    lnk = windows_startup_lnk()
    if os.path.exists(lnk):
        os.remove(lnk)


def _windows_enabled():
    try:
        return os.path.exists(windows_startup_lnk())
    except RuntimeError:
        return False


# ---------- 对外统一 API ----------
def install_autostart(program_args, working_dir, log_path):
    """开启开机自启；program_args 是完整命令行（[解释器/可执行文件, 脚本...]）。"""
    if IS_MACOS:
        return _macos_install(program_args, working_dir, log_path)
    if IS_WINDOWS:
        return _windows_install(program_args, working_dir, log_path)
    raise RuntimeError("当前平台不支持开机自启")


def remove_autostart():
    """关闭开机自启；没装过也不报错。"""
    if IS_MACOS:
        return _macos_remove()
    if IS_WINDOWS:
        return _windows_remove()
    raise RuntimeError("当前平台不支持开机自启")


def autostart_enabled():
    """只判断自启项文件是否存在，不真正探测 launchd / 启动目录状态。"""
    if IS_MACOS:
        return _macos_enabled()
    if IS_WINDOWS:
        return _windows_enabled()
    return False
