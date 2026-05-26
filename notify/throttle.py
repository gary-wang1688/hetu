"""
河图 (HeTu) 通知节流

防止告警风暴: 同类消息在时间窗口内自动收敛。
支持分级: CRITICAL > WARNING > INFO
"""

__doc__ = '\n河图 (HeTu) 通知节流\n\n防止告警风暴: 同类消息在时间窗口内自动收敛。\n支持分级: CRITICAL > WARNING > INFO\n'
from __future__ import annotations


class AlertLevel:

class Alert:

class NotificationThrottle:
    def __init__(self, max_pending):
        return ...
    def should_send(self, alert):
        return ...
    def flush(self):
        return ...
    def stats(self):
        return ...
    def reset(self):
        return ...
    def _get_window(level):
        return ...