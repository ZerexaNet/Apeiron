#!/usr/bin/env python3
"""Fill (or refresh) the Qt translation catalogs and compile them to .qm.

Usage::

    python3 tools/build_translations.py            # write .ts and compile .qm
    python3 tools/build_translations.py --check    # fail if any string is untranslated

The catalogue is generated from the QML sources with ``pyside6-lupdate`` and
the Chinese texts below, so the .ts file never drifts from the UI.  Every
source string must have an entry: a missing translation fails the build rather
than silently shipping English text to Chinese users.
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QML_DIR = ROOT / "src" / "apeiron" / "ui" / "qml"
I18N_DIR = ROOT / "src" / "apeiron" / "i18n"

ZH: dict[str, str] = {
    # --- About ---------------------------------------------------------
    "About Apeiron": "关于 Apeiron",
    "Version %1 · %2": "版本 %1 · %2",
    "Apeiron is an independent third-party Minecraft launcher developed and published by %1.": "Apeiron 是由 %1 开发并发布的独立第三方 Minecraft 启动器。",
    "It is not an official Minecraft product and is not affiliated with, endorsed by, or approved by Mojang Studios or Microsoft. Minecraft and related names, logos and assets belong to their respective owners.": "它不是 Minecraft 官方产品，与 Mojang Studios 或 Microsoft 无隶属关系，也未获得其认可或批准。"
    "Minecraft 及相关名称、标识与资源归各自权利人所有。",
    "Contact: %1": "联系方式：%1",
    "Build information": "构建信息",
    "Version": "版本",
    "Platform": "平台",
    "Python": "Python",
    "Qt": "Qt",
    "Credential store": "凭据存储",
    "Data directory": "数据目录",
    "Platform support": "平台支持",
    "Java Edition ✓": "Java 版 ✓",
    "Java Edition ✗": "Java 版 ✗",
    "Bedrock ✓": "基岩版 ✓",
    "Bedrock ✗": "基岩版 ✗",
    "Licensing": "许可",
    "Apeiron is distributed under the Mozilla Public License 2.0. Third-party components and their licences are listed in docs/THIRD_PARTY_LICENSES.md.": "Apeiron 以 Mozilla Public License 2.0 分发。第三方组件及其许可证见 docs/THIRD_PARTY_LICENSES.md。",
    "Apeiron does not distribute Minecraft game files. Game files are downloaded from Mojang's official servers after you supply your own account or local profile.": "Apeiron 不分发 Minecraft 游戏文件。游戏文件在你提供自己的账户或本地档案后，从 Mojang 官方服务器下载。",
    # --- Accounts ------------------------------------------------------
    "Accounts": "账户",
    "Microsoft accounts are verified with Xbox Live. Offline profiles are local identities only.": "Microsoft 账户通过 Xbox Live 验证。离线档案仅为本地身份。",
    "Add an account": "添加账户",
    "Player name (3-16 letters, digits, underscore)": "玩家名（3-16 位字母、数字或下划线）",
    "Create offline profile": "创建离线档案",
    "Sign in with Microsoft": "使用 Microsoft 登录",
    "Offline profiles never prove game ownership and cannot join servers that require authentication.": "离线档案无法证明游戏所有权，也无法加入要求身份验证的服务器。",
    "Microsoft sign-in": "Microsoft 登录",
    "Open the link below and enter this code:": "打开下面的链接并输入此代码：",
    "Open browser": "打开浏览器",
    "Cancel": "取消",
    "Saved accounts": "已保存的账户",
    "No accounts yet.": "尚无账户。",
    "Microsoft account · verified": "Microsoft 账户 · 已验证",
    "Offline profile · unverified local identity": "离线档案 · 未验证的本地身份",
    "Verified": "已验证",
    "Offline": "离线",
    "Use": "使用",
    "Remove": "移除",
    "Credentials": "凭据",
    "Storage backend: %1": "存储后端：%1",
    "Tokens are protected by the operating system credential store.": "令牌由操作系统凭据存储保护。",
    "No OS credential service is available. Tokens are stored in a protected file; this is less secure.": "未检测到系统凭据服务。令牌保存在受保护的文件中，安全性较低。",
    "Apeiron never asks for your Microsoft password and never stores it.": "Apeiron 绝不会索取或保存你的 Microsoft 密码。",
    # --- Content -------------------------------------------------------
    "Import modpack": "导入整合包",
    "Modpacks (*.mrpack *.zip)": "整合包 (*.mrpack *.zip)",
    "All files (*)": "所有文件 (*)",
    "Add file": "添加文件",
    "Mods and packs (*.jar *.zip)": "模组与整合包 (*.jar *.zip)",
    "Content": "内容",
    "Mods, resource packs, shader packs and data packs for the selected instance.": "管理所选实例的模组、资源包、光影包与数据包。",
    "Select an instance": "选择实例",
    "Choose an instance in the library to manage its content.": "在游戏库中选择实例以管理其内容。",
    "Mods": "模组",
    "Resource packs": "资源包",
    "Shaders": "光影",
    "Data packs": "数据包",
    "Nothing installed in this category.": "此分类下尚未安装任何内容。",
    "Mod loader": "模组加载器",
    "Current: %1": "当前：%1",
    "Forge and NeoForge installs run the vendor's official installer after verifying its published checksum.": "安装 Forge 与 NeoForge 时会先校验官方公布的校验值，再运行厂商提供的官方安装器。",
    "Open instance folder": "打开实例目录",
    # --- Downloads -----------------------------------------------------
    "Downloads": "下载",
    "Every file is written to a temporary file, verified, then moved into place atomically.": "每个文件先写入临时文件、校验通过后再原子替换到目标位置。",
    "Refresh": "刷新",
    "Current transfer": "当前传输",
    "Task history": "任务历史",
    "No download tasks": "没有下载任务",
    "Installing a version or importing a modpack creates tasks here.": "安装版本或导入整合包时会在此生成任务。",
    # --- Home ----------------------------------------------------------
    "Welcome to Apeiron": "欢迎使用 Apeiron",
    "An independent launcher for Minecraft: Java Edition, with experimental Bedrock support on Windows.": "独立的 Minecraft Java 版启动器，并在 Windows 上提供实验性的基岩版支持。",
    "Game running": "游戏运行中",
    "Ready": "就绪",
    "Memory": "内存",
    "Installed": "已安装",
    "Not installed": "未安装",
    "Stop": "停止",
    "Launch": "启动",
    "Launch the selected instance": "启动所选实例",
    "Install files": "安装文件",
    "No instance selected": "未选择实例",
    "Create your first instance to download the game files and start playing.": "创建第一个实例以获取游戏文件并开始游戏。",
    "Open library": "打开游戏库",
    "Quick actions": "快捷操作",
    "Create instance": "创建实例",
    "Manage accounts": "管理账户",
    "Recent activity": "最近活动",
    "No launches yet.": "尚无启动记录。",
    "Java Edition": "Java 版",
    "Bedrock": "基岩版",
    # --- Library -------------------------------------------------------
    "Export instance": "导出实例",
    "Apeiron instance (*.zip)": "Apeiron 实例 (*.zip)",
    "Import instance archive": "导入实例压缩包",
    "Instance archive (*.zip)": "实例压缩包 (*.zip)",
    "Game library": "游戏库",
    "Every instance has its own directory, mods, settings and account.": "每个实例都有独立的目录、模组、设置与账户。",
    "New instance": "新建实例",
    "Import": "导入",
    "Your library is empty": "游戏库为空",
    "Create an instance to download Minecraft and start playing.": "创建一个实例以下载 Minecraft 并开始游戏。",
    "Java": "Java",
    "Last played: %1": "上次启动：%1",
    "Never launched": "从未启动",
    "Details": "详情",
    "Memory (MB)": "内存（MB）",
    "JVM arguments": "JVM 参数",
    "Resolution": "分辨率",
    "Save changes": "保存修改",
    "Install / verify files": "安装 / 校验文件",
    "Duplicate": "复制",
    "Backup worlds": "备份存档",
    "Export archive": "导出压缩包",
    "Delete instance": "删除实例",
    "Delete instance?": "删除实例？",
    "The instance directory and everything inside it will be removed. This cannot be undone.": "实例目录及其中的所有内容都将被删除，且无法恢复。",
    "Name": "名称",
    "My world": "我的世界",
    "Minecraft version": "Minecraft 版本",
    "Vanilla": "原版",
    "Loader versions are fetched from the projects' official metadata.": "加载器版本来自各项目的官方元数据。",
    # --- Logs ----------------------------------------------------------
    "Logs & diagnostics": "日志与诊断",
    "Sensitive values such as tokens are masked by the native logging core before they reach disk.": "令牌等敏感信息在写入磁盘前由原生日志核心脱敏。",
    "Export diagnostics": "导出诊断信息",
    "Game console": "游戏控制台",
    "Launcher log": "启动器日志",
    "No game output yet.": "尚无游戏输出。",
    "Log statistics": "日志统计",
    "written: %1 · dropped: %2 · rotated: %3": "已写入：%1 · 丢弃：%2 · 轮转：%3",
    "Open log folder": "打开日志目录",
    # --- Shell ---------------------------------------------------------
    "Apeiron": "Apeiron",
    "Independent launcher": "独立启动器",
    "Stop game": "停止游戏",
    "Toggle theme": "切换主题",
    "No account": "无账户",
    "Home": "首页",
    "Library": "游戏库",
    "Settings": "设置",
    "Logs": "日志",
    "About": "关于",
    # --- Settings ------------------------------------------------------
    "Language, appearance, performance and runtime configuration.": "语言、外观、性能与运行时配置。",
    "Language": "语言",
    "Effective language: %1": "当前生效语言：%1",
    "Effective language: %1 (translation file missing, showing source text)": "当前生效语言：%1（缺少翻译文件，显示源文本）",
    'A manual choice is kept across restarts. Choose "Follow system" to detect the OS language again.': "手动选择会在重启后保留。选择“跟随系统”可重新按系统语言检测。",
    "Appearance": "外观",
    "Follow system": "跟随系统",
    "Light": "浅色",
    "Dark": "深色",
    "Performance": "性能",
    "Default memory (MB)": "默认内存（MB）",
    "Concurrent downloads": "并发下载数",
    "Download timeout (seconds)": "下载超时（秒）",
    "Download retries": "下载重试次数",
    "Java runtime": "Java 运行时",
    "Detected runtimes": "检测到的运行时",
    "No Java runtime found. Install one below.": "未找到 Java 运行时，可在下方安装。",
    "Install Java 17": "安装 Java 17",
    "Install Java 21": "安装 Java 21",
    "Re-scan": "重新扫描",
    "Updates and diagnostics": "更新与诊断",
    "Check for updates": "检查更新",
    "Open data folder": "打开数据目录",
    "Open logs": "打开日志",
    "Version list refresh": "版本列表刷新",
    "Show snapshots": "显示快照版本",
    "Refresh versions": "刷新版本列表",
}

# Strings that are identical in both languages (product names, symbols).
IDENTICAL = {"Apeiron"}


def lupdate() -> None:
    exe = shutil.which("pyside6-lupdate") or shutil.which("lupdate")
    if not exe:
        raise SystemExit("pyside6-lupdate was not found on PATH")
    I18N_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            exe,
            str(QML_DIR),
            "-ts",
            str(I18N_DIR / "apeiron_zh_CN.ts"),
            "-source-language",
            "en_US",
            "-target-language",
            "zh_CN",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout.strip() or result.stderr.strip())


def fill(language: str, mapping: dict[str, str], identity: bool = False) -> int:
    path = I18N_DIR / f"apeiron_{language}.ts"
    tree = ET.parse(path)
    root = tree.getroot()
    missing: list[str] = []
    total = 0
    for message in root.iter("message"):
        source = message.find("source")
        translation = message.find("translation")
        if source is None or translation is None:
            continue
        text = source.text or ""
        total += 1
        if identity:
            translation.text = text
            continue
        translated = mapping.get(text)
        if translated is None and text not in IDENTICAL:
            missing.append(text)
            translation.set("type", "unfinished")
            translation.text = ""
        else:
            translation.attrib.pop("type", None)
            translation.text = translated if translated is not None else text
    tree.write(path, encoding="utf-8", xml_declaration=True)
    if missing:
        print(f"{language}: {len(missing)} untranslated string(s):", file=sys.stderr)
        for item in missing:
            print("  -", html.unescape(item), file=sys.stderr)
        raise SystemExit(1)
    print(f"{language}: {total} strings translated")
    return total


def lrelease() -> None:
    exe = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if not exe:
        raise SystemExit("pyside6-lrelease was not found on PATH")
    for ts in sorted(I18N_DIR.glob("apeiron_*.ts")):
        result = subprocess.run([exe, str(ts)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SystemExit(f"lrelease failed for {ts.name}: {result.stderr}")
        print(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not args.check:
        lupdate()
        fill("zh_CN", ZH)
        # English is the source language: an identity catalogue keeps the
        # language switcher honest instead of reporting "translation missing".
        en_path = I18N_DIR / "apeiron_en_US.ts"
        shutil.copy(I18N_DIR / "apeiron_zh_CN.ts", en_path)
        fill("en_US", ZH, identity=True)
        lrelease()
        return 0

    # check mode: every source string in the QML must have a translation
    missing = []
    for f in QML_DIR.rglob("*.qml"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if 'qsTr("' in line:
                start = line.index('qsTr("') + 6
                end = line.rindex('")')
                text = line[start:end]
                if text not in ZH and text not in IDENTICAL and "%" not in text:
                    missing.append(text)
    if missing:
        print("missing translations:", *sorted(set(missing)), sep="\n  ")
        return 1
    print("translation catalogue is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
