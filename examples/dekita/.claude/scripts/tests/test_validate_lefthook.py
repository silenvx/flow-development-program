#!/usr/bin/env python3
"""Tests for validate_lefthook.py."""

import sys
import unittest
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from validate_lefthook import check_staged_files_in_pre_push


class TestCheckStagedFilesInPrePush(unittest.TestCase):
    """Tests for LEFTHOOK001: pre-push should not use {staged_files}."""

    def test_detects_staged_files_in_pre_push(self):
        """Should detect {staged_files} usage in pre-push command."""
        config = {
            "pre-push": {
                "commands": {
                    "bad-command": {
                        "run": "python3 script.py {staged_files}",
                    }
                }
            }
        }
        content = "run: python3 script.py {staged_files}"
        errors = check_staged_files_in_pre_push(config, content, "test.yml")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "LEFTHOOK001")
        self.assertIn("meaningless", errors[0].message)

    def test_allows_staged_files_in_pre_commit(self):
        """Should allow {staged_files} in pre-commit (not checked by this function)."""
        config = {
            "pre-commit": {
                "commands": {
                    "good-command": {
                        "run": "python3 script.py {staged_files}",
                    }
                }
            }
        }
        content = "run: python3 script.py {staged_files}"
        errors = check_staged_files_in_pre_push(config, content, "test.yml")
        self.assertEqual(len(errors), 0)

    def test_allows_push_files_in_pre_push(self):
        """Should allow {push_files} in pre-push."""
        config = {
            "pre-push": {
                "commands": {
                    "good-command": {
                        "run": "python3 script.py {push_files}",
                    }
                }
            }
        }
        content = "run: python3 script.py {push_files}"
        errors = check_staged_files_in_pre_push(config, content, "test.yml")
        self.assertEqual(len(errors), 0)

    def test_allows_no_files_variable(self):
        """Should allow commands without file variables."""
        config = {
            "pre-push": {
                "commands": {
                    "good-command": {
                        "run": "python3 script.py",
                    }
                }
            }
        }
        content = "run: python3 script.py"
        errors = check_staged_files_in_pre_push(config, content, "test.yml")
        self.assertEqual(len(errors), 0)

    def test_detects_multiple_violations(self):
        """Should detect multiple {staged_files} violations."""
        config = {
            "pre-push": {
                "commands": {
                    "bad-command-1": {
                        "run": "python3 script1.py {staged_files}",
                    },
                    "bad-command-2": {
                        "run": "python3 script2.py {staged_files}",
                    },
                }
            }
        }
        content = "run: python3 script1.py {staged_files}\nrun: python3 script2.py {staged_files}"
        errors = check_staged_files_in_pre_push(config, content, "test.yml")
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
