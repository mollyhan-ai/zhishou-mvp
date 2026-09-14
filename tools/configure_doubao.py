"""Local-only setup. Credentials never appear in command history or output."""
import getpass
import os
from pathlib import Path
import tempfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.settings_store import MODEL, BASE_URL, save_config


def main():
    print(f"配置模型：{MODEL}")
    print("密钥只写入本机 .env；此步骤不会调用模型。粘贴时屏幕不显示字符，属正常现象。")
    key = getpass.getpass("请粘贴火山方舟 API Key，然后按回车：")
    path = Path(__file__).resolve().parents[1] / ".env"
    try:
        save_config(path, key)
    except (OSError, ValueError) as exc:
        print(f"配置未完成：{exc}")
        return 1
    print("配置已保存。请重启 python3 run.py，再用短录音验证实际调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
