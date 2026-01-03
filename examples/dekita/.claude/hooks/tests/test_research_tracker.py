#!/usr/bin/env python3
"""Tests for research-tracker.py hook."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def load_module_with_hyphen(module_name: str, file_name: str):
    """Load a module with hyphen in filename."""
    module_path = Path(__file__).parent.parent / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestResearchTracker:
    """Tests for research-tracker.py functions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.research_file = self.temp_path / "research-activity-test.json"

        # Import common module and patch SESSION_DIR
        import common as common_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()

        # Load research-tracker module
        self.module = load_module_with_hyphen("research_tracker", "research-tracker.py")
        # Patch the module's SESSION_DIR and get_research_activity_file function
        self.module_patcher = patch.object(self.module, "SESSION_DIR", self.temp_path)
        self.module_patcher.start()
        # Patch the function to return test-specific file path
        self.file_patcher = patch.object(
            self.module, "get_research_activity_file", return_value=self.research_file
        )
        self.file_patcher.start()

    def teardown_method(self):
        """Clean up test fixtures."""
        self.file_patcher.stop()
        self.module_patcher.stop()
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_empty_research_data(self):
        """Should return empty structure when no file exists."""
        data = self.module.load_research_data()
        assert data["activities"] == []
        assert "session_start" in data

    def test_record_research_activity(self):
        """Should record research activity to file."""
        self.module.record_research_activity("WebSearch", "test query")

        data = self.module.load_research_data()
        assert len(data["activities"]) == 1
        assert data["activities"][0]["tool"] == "WebSearch"
        assert data["activities"][0]["query"] == "test query"

    def test_record_multiple_activities(self):
        """Should record multiple research activities."""
        self.module.record_research_activity("WebSearch", "query 1")
        self.module.record_research_activity("WebFetch", "https://example.com")

        data = self.module.load_research_data()
        assert len(data["activities"]) == 2
        assert data["activities"][0]["tool"] == "WebSearch"
        assert data["activities"][1]["tool"] == "WebFetch"

    def test_query_truncation(self):
        """Should truncate long queries to 200 chars."""
        long_query = "x" * 300
        self.module.record_research_activity("WebSearch", long_query)

        data = self.module.load_research_data()
        assert len(data["activities"][0]["query"]) == 200

    def test_extract_query_websearch(self):
        """Should extract query from WebSearch input."""
        input_data = {
            "tool_name": "WebSearch",
            "tool_input": {"query": "test search query"},
        }
        result = self.module.extract_query(input_data)
        assert result == "test search query"

    def test_extract_query_webfetch(self):
        """Should extract URL from WebFetch input."""
        input_data = {
            "tool_name": "WebFetch",
            "tool_input": {"url": "https://example.com/page"},
        }
        result = self.module.extract_query(input_data)
        assert result == "https://example.com/page"


class TestResearchTrackerMain:
    """Tests for main function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.research_file = self.temp_path / "research-activity-test.json"

        import common as common_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()

        self.module = load_module_with_hyphen("research_tracker", "research-tracker.py")
        self.module_patcher = patch.object(self.module, "SESSION_DIR", self.temp_path)
        self.module_patcher.start()
        # Patch the function to return test-specific file path
        self.file_patcher = patch.object(
            self.module, "get_research_activity_file", return_value=self.research_file
        )
        self.file_patcher.start()

    def teardown_method(self):
        """Clean up test fixtures."""
        self.file_patcher.stop()
        self.module_patcher.stop()
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_main_websearch(self):
        """Should record WebSearch and continue."""
        input_data = {
            "tool_name": "WebSearch",
            "tool_input": {"query": "test query"},
        }

        with patch("sys.stdin", create=True) as mock_stdin:
            mock_stdin.read.return_value = json.dumps(input_data)
            mock_stdin.__iter__ = lambda self: iter([json.dumps(input_data)])

            with patch.object(sys, "stdin", __class__=type(sys.stdin)):
                # Use StringIO for stdin
                import io

                sys.stdin = io.StringIO(json.dumps(input_data))

                with patch("builtins.print") as mock_print:
                    self.module.main()
                    mock_print.assert_called()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output.get("continue", False)

    def test_main_non_research_tool(self):
        """Should not record non-research tools."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            mock_print.assert_called()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)

        # No research should be recorded
        data = self.module.load_research_data()
        assert len(data["activities"]) == 0
