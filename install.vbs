Set objShell = CreateObject("WScript.Shell")
objShell.Run "cmd.exe /c chcp 936 && python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt", 1, False
