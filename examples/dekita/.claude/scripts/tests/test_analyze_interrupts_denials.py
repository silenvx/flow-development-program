#!/usr/bin/env python3
"""
analyze-interrupts.py のツール拒否検出機能のテスト

テスト対象:
- find_denials(): フック/ユーザーによる拒否検出
- _find_tool_name_for_id(): tool_use_idからツール名を探す
"""

import importlib.util
import sys
from pathlib import Path

# テスト対象モジュールのパスを追加
scripts_dir = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "analyze_interrupts", scripts_dir / "analyze_interrupts.py"
)
if spec is None or spec.loader is None:
    raise ImportError("Cannot load analyze-interrupts.py")
analyze_interrupts = importlib.util.module_from_spec(spec)
sys.modules["analyze_interrupts"] = analyze_interrupts
spec.loader.exec_module(analyze_interrupts)

find_denials = analyze_interrupts.find_denials
_find_tool_name_for_id = analyze_interrupts._find_tool_name_for_id


class TestFindDenials:
    """find_denials() のテスト"""

    def test_hook_denial_detection(self):
        """フックによる拒否を検出"""
        events = [
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "is_error": True,
                            "content": "Hook PreToolUse:Bash denied this tool",
                        }
                    ]
                },
            }
        ]
        denials = find_denials(events)

        assert len(denials) == 1
        assert denials[0]["tool_name"] == "Bash"
        assert denials[0]["denial_source"] == "hook"
        assert denials[0]["session_id"] == "test-session"

    def test_hook_denial_with_hyphenated_tool_name(self):
        """ハイフンを含むツール名でのフック拒否を検出"""
        events = [
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "is_error": True,
                            "content": "Hook PreToolUse:Web-Search denied this tool",
                        }
                    ]
                },
            }
        ]
        denials = find_denials(events)

        assert len(denials) == 1
        assert denials[0]["tool_name"] == "Web-Search"

    def test_user_denial_detection(self):
        """ユーザーによる拒否を検出"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_456",
                            "name": "Edit",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_456",
                            "is_error": True,
                            "content": "User rejected this operation",
                        }
                    ]
                },
            },
        ]
        denials = find_denials(events)

        assert len(denials) == 1
        assert denials[0]["tool_name"] == "Edit"
        assert denials[0]["denial_source"] == "user"

    def test_no_denial_when_not_error(self):
        """is_error=Falseの場合は検出しない"""
        events = [
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "is_error": False,
                            "content": "Hook PreToolUse:Bash denied this tool",
                        }
                    ]
                },
            }
        ]
        denials = find_denials(events)

        assert len(denials) == 0

    def test_no_denial_for_non_user_event(self):
        """user以外のイベントは無視"""
        events = [
            {
                "type": "assistant",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "is_error": True,
                            "content": "Hook PreToolUse:Bash denied this tool",
                        }
                    ]
                },
            }
        ]
        denials = find_denials(events)

        assert len(denials) == 0

    def test_empty_events(self):
        """空のイベントリスト"""
        denials = find_denials([])
        assert len(denials) == 0

    def test_malformed_content(self):
        """不正な形式のcontent"""
        events = [
            {
                "type": "user",
                "message": {
                    "content": "not a list",
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        "string instead of dict",
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "not a tool_result",
                        },
                    ],
                },
            },
        ]
        denials = find_denials(events)
        assert len(denials) == 0

    def test_non_string_result_content(self):
        """tool_resultのcontentが文字列以外"""
        events = [
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "is_error": True,
                            "content": ["list", "content"],
                        }
                    ]
                },
            }
        ]
        denials = find_denials(events)
        assert len(denials) == 0

    def test_multiple_denials(self):
        """複数の拒否を検出"""
        events = [
            {
                "type": "user",
                "timestamp": "2025-12-18T10:00:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_1",
                            "is_error": True,
                            "content": "Hook PreToolUse:Bash denied this tool",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2025-12-18T10:01:00Z",
                "sessionId": "test-session",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_2",
                            "is_error": True,
                            "content": "Hook PreToolUse:Edit denied this tool",
                        }
                    ]
                },
            },
        ]
        denials = find_denials(events)

        assert len(denials) == 2
        assert denials[0]["tool_name"] == "Bash"
        assert denials[1]["tool_name"] == "Edit"


class TestFindToolNameForId:
    """_find_tool_name_for_id() のテスト"""

    def test_find_matching_tool(self):
        """マッチするtool_useを発見"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_abc",
                            "name": "Read",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        tool_name = _find_tool_name_for_id(events, 1, "tool_abc")
        assert tool_name == "Read"

    def test_return_unknown_when_no_match(self):
        """マッチなし時は"unknown"を返す"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_abc",
                            "name": "Read",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        tool_name = _find_tool_name_for_id(events, 1, "tool_xyz")
        assert tool_name == "unknown"

    def test_return_unknown_for_empty_tool_use_id(self):
        """空のtool_use_idは"unknown"を返す"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "",
                            "name": "Read",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        tool_name = _find_tool_name_for_id(events, 1, "")
        assert tool_name == "unknown"

    def test_search_backwards(self):
        """イベントリストを後方から検索"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_1",
                            "name": "Bash",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_2",
                            "name": "Edit",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        # インデックス3から検索
        tool_name = _find_tool_name_for_id(events, 3, "tool_2")
        assert tool_name == "Edit"

    def test_handle_malformed_events(self):
        """不正な形式のイベントを処理"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": "not a list",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        "string item",
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "not tool_use",
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        tool_name = _find_tool_name_for_id(events, 3, "tool_xyz")
        assert tool_name == "unknown"

    def test_missing_name_field(self):
        """nameフィールドがない場合"""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_abc",
                            # name field missing
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": []},
            },
        ]
        tool_name = _find_tool_name_for_id(events, 1, "tool_abc")
        assert tool_name == "unknown"
