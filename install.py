"""一键安装并创建桌面快捷方式"""
import os
import sys
import subprocess
import win32com.client

def check_python():
    """检查 Python 环境"""
    print("=" * 50)
    print("  QQ 农场助手 - 一键安装脚本")
    print("=" * 50)
    print()

    print("[1/5] 检查 Python 环境...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print(f"  [错误] Python 版本过低 ({version.major}.{version.minor})，需要 3.10+")
        print("  下载地址：https://www.python.org/downloads/")
        return False
    print(f"  Python 版本：{version.major}.{version.minor}.{version.micro}")
    return True


def install_dependencies():
    """安装依赖"""
    print()
    print("[2/5] 安装依赖...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    requirements = os.path.join(script_dir, "requirements.txt")

    if not os.path.exists(requirements):
        print(f"  [错误] 找不到 {requirements}")
        return False

    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", requirements])
        print("  依赖安装完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [错误] 依赖安装失败：{e}")
        return False


def create_shortcut():
    """创建桌面快捷方式"""
    print()
    print("[3/5] 创建桌面快捷方式...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(script_dir, "main.py")
    python_exe = sys.executable
    desktop_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")
    shortcut_path = os.path.join(desktop_dir, "QQ 农场助手.lnk")

    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.TargetPath = python_exe
        shortcut.Arguments = main_py
        shortcut.WorkingDirectory = script_dir
        shortcut.IconLocation = python_exe + ",0"
        shortcut.Description = "QQ 农场视觉识别助手"
        shortcut.save()
        print(f"  [OK] 快捷方式已创建：{shortcut_path}")
        return True
    except Exception as e:
        print(f"  [警告] 快捷方式创建失败：{e}")
        return False


def init_config():
    """初始化配置文件"""
    print()
    print("[4/5] 初始化配置...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")

    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{}")
        print("  [OK] 已创建配置文件 config.json")
    else:
        print("  配置文件已存在")
    return True


def check_templates():
    """检查模板目录"""
    print()
    print("[5/5] 检查模板...")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(script_dir, "templates")

    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)
        print(f"  [OK] 已创建模板目录：{templates_dir}")
    else:
        template_count = len([f for f in os.listdir(templates_dir) if f.endswith(".png")])
        if template_count > 0:
            print(f"  模板目录已存在，共 {template_count} 个模板")
        else:
            print("  模板目录为空，请运行模板采集工具")

    return True


def main():
    """主函数"""
    # 检查 Python
    if not check_python():
        print()
        print("安装失败，按任意键退出...")
        input()
        return 1

    # 安装依赖
    if not install_dependencies():
        print()
        print("安装失败，按任意键退出...")
        input()
        return 1

    # 创建快捷方式
    create_shortcut()

    # 初始化配置
    if not init_config():
        print()
        print("安装失败，按任意键退出...")
        input()
        return 1

    # 检查模板
    check_templates()

    print()
    print("=" * 50)
    print("  安装完成！")
    print("=" * 50)
    print()
    print("下一步操作：")
    print("1. 双击桌面上的 'QQ 农场助手' 快捷方式启动程序")
    print("2. 首次使用请运行：python tools\\template_collector.py 采集模板")
    print("3. 或者运行：python tools\\import_seeds.py 导入种子图片")
    print()
    input("按回车键退出...")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EOFError:
        # 后台运行时跳过 input
        sys.exit(0)
