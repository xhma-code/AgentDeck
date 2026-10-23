"""
Windows 防休眠锁（SetThreadExecutionState），防止人离开电脑躺沙发时 PC 自动睡眠断网。
"""

from __future__ import annotations

import ctypes
import sys

from backend.core.logger import log_structured_error, logger


class WindowsAwakeLock:
    """Windows 防休眠锁（SetThreadExecutionState），防止离开电脑躺沙发时 PC 自动睡眠断网。"""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_AWAYMODE_REQUIRED = 0x00000040

    def __init__(self) -> None:
        self.is_active: bool = False

    def acquire(self) -> bool:
        """开启防休眠保护（不强制亮屏，仅保持系统与网络唤醒）。"""
        if sys.platform != "win32":
            return False
        try:
            flags = self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
            res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self.is_active = bool(res)
            if self.is_active:
                logger.info("已启用 Windows 防休眠锁（保持后台网络与 Agent 持续运行，允许显示器熄屏）")
            return self.is_active
        except Exception as exc:
            log_structured_error(
                problem="启用 Windows 防休眠锁失败",
                cause=str(exc),
                fix="可在 Windows 电源设置中手动调整睡眠时间。",
                exc=exc,
            )
            return False

    def release(self) -> None:
        """释放防休眠保护，恢复系统默认电源策略。"""
        if sys.platform != "win32" or not self.is_active:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
            self.is_active = False
            logger.info("已释放 Windows 防休眠锁")
        except Exception as exc:
            log_structured_error(
                problem="释放 Windows 防休眠锁异常",
                cause=str(exc),
                fix="系统将在进程退出后由操作系统自动回收电源锁。",
                exc=exc,
            )
