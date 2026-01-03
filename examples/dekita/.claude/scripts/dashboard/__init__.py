"""Dashboard generation package.

Issue #1367: 開発フローログの可視化
"""

from .data_collector import DashboardDataCollector
from .html_generator import generate_dashboard_html

__all__ = ["DashboardDataCollector", "generate_dashboard_html"]
