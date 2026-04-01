"""创建桌面快捷方式"""
import os
import win32com.client

def create_shortcut():
    # 获取当前脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(script_dir, "main.py")
    python_exe = os.sys.executable

    # 桌面目录
    desktop_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")

    # 快捷方式路径和目标
    shortcut_path = os.path.join(desktop_dir, "QQ 农场助手.lnk")

    # 创建快捷方式
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.TargetPath = python_exe
    shortcut.Arguments = main_py
    shortcut.WorkingDirectory = script_dir
    shortcut.IconLocation = python_exe + ",0"
    shortcut.Description = "QQ 农场视觉识别助手"
    shortcut.save()

    print(f"[OK] 桌面快捷方式已创建：{shortcut_path}")

if __name__ == "__main__":
    create_shortcut()
