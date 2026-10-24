"""
终端二维码与高对比度配对边框渲染工具。
"""

from __future__ import annotations


def render_terminal_qr_box(url: str, title: str = "AgentDeck 手机扫码直连") -> str:
    """
    生成终端高亮配对卡片（若环境装有 qrcode 库则输出紧凑半高块字符二维码，
    否则输出醒目的高对比度边框配对指引与网页二维码入口）。
    """
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        f"║  📱 {title:<61}║",
        "╠══════════════════════════════════════════════════════════════════════╣",
        f"║  直连地址: {url:<58}║",
        "╚══════════════════════════════════════════════════════════════════════╝",
    ]
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        qr_lines: list[str] = []
        for r in range(0, len(matrix), 2):
            row_top = matrix[r]
            row_bot = matrix[r + 1] if r + 1 < len(matrix) else [False] * len(row_top)
            chars: list[str] = []
            for top, bot in zip(row_top, row_bot):
                if top and bot:
                    chars.append("█")
                elif top and not bot:
                    chars.append("▀")
                elif not top and bot:
                    chars.append("▄")
                else:
                    chars.append(" ")
            qr_lines.append("  " + "".join(chars))
        return "\n".join(lines + qr_lines)
    except ImportError:
        return "\n".join(lines)
