# 确保 DISPLAY 环境变量补丁
# 将此内容插入到 cdp_browser.py 的 import 区域后

def _ensure_display_env():
    """确保 DISPLAY 环境变量已设置（用于 Xvfb 虚拟显示器）"""
    import os
    import subprocess
    import logging

    logger_extra = logging.getLogger(__name__)

    if not os.environ.get('DISPLAY'):
        # 检测常见的 Xvfb 显示器
        for disp in [':98', ':99', ':0']:
            try:
                result = subprocess.run(
                    ['xdpyinfo'],
                    env={**os.environ, 'DISPLAY': disp},
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    os.environ['DISPLAY'] = disp
                    logger_extra.info(f"[CDP] Auto-detected DISPLAY={disp}")
                    return
            except Exception as e:
                pass
        logger_extra.warning("[CDP] No X display detected, Chrome may fail to launch")

# 在 CDPBrowser.launch() 方法开头调用：
# _ensure_display_env()
