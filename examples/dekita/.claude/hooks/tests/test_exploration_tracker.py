#!/usr/bin/env python3
"""Tests for exploration-tracker.py hook."""

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


class TestExplorationTracker:
    """Tests for exploration-tracker.py functions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.exploration_file = self.temp_path / "exploration-depth-test.json"

        import common as common_module
        import lib.research as research_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()
        # Patch get_exploration_file in lib.research for get_exploration_depth
        self.lib_research_file_patcher = patch.object(
            research_module, "get_exploration_file", return_value=self.exploration_file
        )
        self.lib_research_file_patcher.start()
        self.common_module = common_module

        self.module = load_module_with_hyphen("exploration_tracker", "exploration-tracker.py")
        self.module_patcher = patch.object(self.module, "SESSION_DIR", self.temp_path)
        self.module_patcher.start()
        # Patch get_exploration_file in module
        self.module_file_patcher = patch.object(
            self.module, "get_exploration_file", return_value=self.exploration_file
        )
        self.module_file_patcher.start()

    def teardown_method(self):
        """Clean up test fixtures."""
        self.module_file_patcher.stop()
        self.module_patcher.stop()
        self.lib_research_file_patcher.stop()
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_empty_exploration_data(self):
        """Should return empty structure when no file exists."""
        data = self.module.load_exploration_data()
        assert data["counts"] == {"Read": 0, "Glob": 0, "Grep": 0}
        assert "session_start" in data

    def test_increment_exploration(self):
        """Should increment exploration count."""
        stats = self.module.increment_exploration("Read")
        assert stats["counts"]["Read"] == 1
        assert stats["total"] == 1

        stats = self.module.increment_exploration("Read")
        assert stats["counts"]["Read"] == 2
        assert stats["total"] == 2

    def test_increment_multiple_tools(self):
        """Should track multiple exploration tools."""
        self.module.increment_exploration("Read")
        self.module.increment_exploration("Glob")
        stats = self.module.increment_exploration("Grep")

        assert stats["counts"]["Read"] == 1
        assert stats["counts"]["Glob"] == 1
        assert stats["counts"]["Grep"] == 1
        assert stats["total"] == 3

    def test_get_exploration_depth(self):
        """Should return exploration depth stats via common module."""
        depth = self.common_module.get_exploration_depth()
        assert depth["total"] == 0
        assert not depth["sufficient"]

        # Add enough exploration to reach threshold
        for _ in range(5):
            self.module.increment_exploration("Read")

        depth = self.common_module.get_exploration_depth()
        assert depth["total"] == 5
        assert depth["sufficient"]

    def test_get_exploration_depth_threshold(self):
        """Should mark sufficient when reaching MIN_EXPLORATION_FOR_BYPASS."""
        for i in range(4):
            self.module.increment_exploration("Read")
            depth = self.common_module.get_exploration_depth()
            assert not depth["sufficient"], f"Should not be sufficient at {i + 1}"

        self.module.increment_exploration("Read")
        depth = self.common_module.get_exploration_depth()
        assert depth["sufficient"], "Should be sufficient at 5"


class TestExplorationTrackerMain:
    """Tests for main function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.exploration_file = self.temp_path / "exploration-depth-test.json"

        import common as common_module
        import lib.research as research_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()
        # Patch get_exploration_file in lib.research for get_exploration_depth
        self.lib_research_file_patcher = patch.object(
            research_module, "get_exploration_file", return_value=self.exploration_file
        )
        self.lib_research_file_patcher.start()
        self.common_module = common_module

        self.module = load_module_with_hyphen("exploration_tracker", "exploration-tracker.py")
        self.module_patcher = patch.object(self.module, "SESSION_DIR", self.temp_path)
        self.module_patcher.start()
        # Patch get_exploration_file in module
        self.module_file_patcher = patch.object(
            self.module, "get_exploration_file", return_value=self.exploration_file
        )
        self.module_file_patcher.start()

    def teardown_method(self):
        """Clean up test fixtures."""
        self.module_file_patcher.stop()
        self.module_patcher.stop()
        self.lib_research_file_patcher.stop()
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_main_read(self):
        """Should track Read tool usage."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            mock_print.assert_called()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)

        depth = self.common_module.get_exploration_depth()
        assert depth["counts"]["Read"] == 1

    def test_main_glob(self):
        """Should track Glob tool usage."""
        input_data = {
            "tool_name": "Glob",
            "tool_input": {"pattern": "*.py"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)

        depth = self.common_module.get_exploration_depth()
        assert depth["counts"]["Glob"] == 1

    def test_main_grep(self):
        """Should track Grep tool usage."""
        input_data = {
            "tool_name": "Grep",
            "tool_input": {"pattern": "search"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)

        depth = self.common_module.get_exploration_depth()
        assert depth["counts"]["Grep"] == 1

    def test_main_non_exploration_tool(self):
        """Should not track non-exploration tools."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)

        depth = self.common_module.get_exploration_depth()
        assert depth["total"] == 0

    def test_main_invalid_json(self):
        """Should handle invalid JSON input."""
        import io

        sys.stdin = io.StringIO("not valid json")

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output.get("continue", False)
