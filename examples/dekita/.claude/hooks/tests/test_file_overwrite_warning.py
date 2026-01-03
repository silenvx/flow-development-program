#!/usr/bin/env python3
"""Tests for file_overwrite_warning.py hook."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import file_overwrite_warning as hook


class TestExtractRedirectTargets:
    """Tests for extract_redirect_targets function."""

    def test_cat_redirect(self):
        """Should detect cat > file."""
        result = hook.extract_redirect_targets("cat > output.txt")
        assert "output.txt" in result

    def test_cat_append(self):
        """Should detect cat >> file."""
        result = hook.extract_redirect_targets("cat >> output.txt")
        assert "output.txt" in result

    def test_echo_redirect(self):
        """Should detect echo \"...\" > file."""
        result = hook.extract_redirect_targets('echo "hello" > output.txt')
        assert "output.txt" in result

    def test_echo_append(self):
        """Should detect echo \"...\" >> file."""
        result = hook.extract_redirect_targets('echo "hello" >> output.txt')
        assert "output.txt" in result

    def test_heredoc_redirect(self):
        """Should detect cat << 'EOF' > file."""
        result = hook.extract_redirect_targets("cat << 'EOF' > output.txt")
        assert "output.txt" in result

    def test_heredoc_quoted(self):
        """Should detect cat << \"EOF\" > file."""
        result = hook.extract_redirect_targets('cat << "EOF" > output.txt')
        assert "output.txt" in result

    def test_printf_redirect(self):
        """Should detect printf > file."""
        result = hook.extract_redirect_targets('printf "%s\\n" "hello" > output.txt')
        assert "output.txt" in result

    def test_tee_overwrite(self):
        """Should detect | tee file (overwrites)."""
        result = hook.extract_redirect_targets("echo hello | tee output.txt")
        assert "output.txt" in result

    def test_tee_append_not_detected(self):
        """Should NOT detect | tee -a file (append mode)."""
        result = hook.extract_redirect_targets("echo hello | tee -a output.txt")
        assert "output.txt" not in result

    def test_tee_combined_option_with_a_not_detected(self):
        """Should NOT detect | tee -ai file (combined options with -a)."""
        # Issue #1037: -ai should be recognized as append mode
        result = hook.extract_redirect_targets("echo hello | tee -ai output.txt")
        assert "output.txt" not in result

    def test_tee_combined_option_ia_not_detected(self):
        """Should NOT detect | tee -ia file (combined options with -a)."""
        result = hook.extract_redirect_targets("echo hello | tee -ia output.txt")
        assert "output.txt" not in result

    def test_tee_long_option_append_not_detected(self):
        """Should NOT detect | tee --append file."""
        result = hook.extract_redirect_targets("echo hello | tee --append output.txt")
        assert "output.txt" not in result

    def test_tee_multiple_files(self):
        """Should detect multiple files in | tee file1 file2."""
        result = hook.extract_redirect_targets("echo hello | tee file1.txt file2.txt")
        assert "file1.txt" in result
        assert "file2.txt" in result

    def test_tee_file_before_option_all_files(self):
        """tee file -a: After first file, -a is treated as filename, not option."""
        # tee treats -a after filename as another filename, not option
        result = hook.extract_redirect_targets("echo hello | tee file.txt -a")
        assert "file.txt" in result
        assert "-a" in result  # -a after filename is treated as file

    def test_tee_with_double_dash(self):
        """Should handle -- to end option parsing."""
        result = hook.extract_redirect_targets("echo hello | tee -- -a output.txt")
        # After --, -a is treated as filename
        assert "-a" in result
        assert "output.txt" in result

    def test_tee_i_option_not_append(self):
        """Should detect | tee -i file (only -i, not append mode)."""
        result = hook.extract_redirect_targets("echo hello | tee -i output.txt")
        assert "output.txt" in result

    def test_no_redirect(self):
        """Should return empty list for commands without redirect."""
        result = hook.extract_redirect_targets("ls -la")
        assert result == []

    def test_complex_path(self):
        """Should handle complex file paths."""
        result = hook.extract_redirect_targets("cat > /path/to/some/file.py")
        assert "/path/to/some/file.py" in result

    def test_multiple_redirects(self):
        """Should detect multiple redirects in command chain."""
        cmd = "echo a > file1.txt && echo b > file2.txt"
        result = hook.extract_redirect_targets(cmd)
        assert "file1.txt" in result
        assert "file2.txt" in result


class TestParseTeeArguments:
    """Tests for parse_tee_arguments function (Issue #1037)."""

    def test_simple_file(self):
        """Single file should be returned."""
        result = hook.parse_tee_arguments("file.txt")
        assert result == ["file.txt"]

    def test_multiple_files(self):
        """Multiple files should all be returned."""
        result = hook.parse_tee_arguments("file1.txt file2.txt file3.txt")
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_short_option(self):
        """Short -a option should return empty list."""
        result = hook.parse_tee_arguments("-a file.txt")
        assert result == []

    def test_append_long_option(self):
        """Long --append option should return empty list."""
        result = hook.parse_tee_arguments("--append file.txt")
        assert result == []

    def test_combined_option_ai(self):
        """Combined -ai option should detect append mode."""
        result = hook.parse_tee_arguments("-ai file.txt")
        assert result == []

    def test_combined_option_ia(self):
        """Combined -ia option should detect append mode."""
        result = hook.parse_tee_arguments("-ia file.txt")
        assert result == []

    def test_combined_option_pia(self):
        """Combined -pia option should detect append mode."""
        result = hook.parse_tee_arguments("-pia file.txt")
        assert result == []

    def test_i_option_only(self):
        """Only -i option (no -a) should return files."""
        result = hook.parse_tee_arguments("-i file.txt")
        assert result == ["file.txt"]

    def test_p_option_only(self):
        """Only -p option (no -a) should return files."""
        result = hook.parse_tee_arguments("-p file.txt")
        assert result == ["file.txt"]

    def test_file_then_dash_a(self):
        """file -a: After first file, -a is treated as filename."""
        result = hook.parse_tee_arguments("file.txt -a")
        assert result == ["file.txt", "-a"]

    def test_double_dash_then_dash_a(self):
        """-- -a file: After --, -a is treated as filename."""
        result = hook.parse_tee_arguments("-- -a file.txt")
        assert result == ["-a", "file.txt"]

    def test_empty_string(self):
        """Empty string should return empty list."""
        result = hook.parse_tee_arguments("")
        assert result == []

    def test_whitespace_only(self):
        """Whitespace only should return empty list."""
        result = hook.parse_tee_arguments("   ")
        assert result == []

    def test_unknown_long_option(self):
        """Unknown long options should be skipped."""
        result = hook.parse_tee_arguments("--output-error=exit file.txt")
        assert result == ["file.txt"]

    def test_multiple_options_before_file(self):
        """Multiple options before file should all be processed."""
        result = hook.parse_tee_arguments("-i -p file.txt")
        assert result == ["file.txt"]

    def test_append_with_multiple_files(self):
        """Append mode with multiple files should return empty."""
        result = hook.parse_tee_arguments("-a file1.txt file2.txt")
        assert result == []

    # Issue #1052: shlex.split()によるクォート付きファイル名対応
    def test_double_quoted_filename(self):
        """Double-quoted filename with space should be parsed correctly."""
        result = hook.parse_tee_arguments('"file name.txt"')
        assert result == ["file name.txt"]

    def test_single_quoted_filename(self):
        """Single-quoted filename with space should be parsed correctly."""
        result = hook.parse_tee_arguments("'file name.txt'")
        assert result == ["file name.txt"]

    def test_quoted_with_option(self):
        """Quoted filename with option should work."""
        result = hook.parse_tee_arguments('-i "my file.txt"')
        assert result == ["my file.txt"]

    def test_mixed_quoted_and_unquoted(self):
        """Mix of quoted and unquoted filenames should work."""
        result = hook.parse_tee_arguments('file1.txt "file 2.txt" file3.txt')
        assert result == ["file1.txt", "file 2.txt", "file3.txt"]

    def test_unclosed_quote_returns_empty(self):
        """Unclosed quote should return empty list (fail-open)."""
        result = hook.parse_tee_arguments('"unclosed file.txt')
        assert result == []

    def test_append_with_quoted_filename(self):
        """Append mode with quoted filename should return empty."""
        result = hook.parse_tee_arguments('-a "file name.txt"')
        assert result == []


class TestResolvePath:
    """Tests for resolve_path function."""

    def test_absolute_path(self):
        """Should resolve absolute paths."""
        result = hook.resolve_path("/tmp/test.txt")
        # macOSでは /tmp は /private/tmp へのシンボリックリンク
        assert result.name == "test.txt"
        assert "tmp" in str(result)

    def test_home_expansion(self):
        """Should expand ~ to home directory."""
        result = hook.resolve_path("~/test.txt")
        assert str(result).startswith("/")
        assert "~" not in str(result)

    def test_env_var_expansion(self):
        """Should expand environment variables."""
        with patch.dict("os.environ", {"MY_DIR": "/custom/path"}):
            result = hook.resolve_path("$MY_DIR/test.txt")
            assert "/custom/path" in str(result)

    def test_relative_path_with_cd_command(self):
        """Should resolve relative paths using cd target directory."""
        with patch("file_overwrite_warning.get_effective_cwd") as mock_cwd:
            mock_cwd.return_value = Path("/project/subdir")
            result = hook.resolve_path("output.txt", "cd /project/subdir && cat > output.txt")
            mock_cwd.assert_called_with("cd /project/subdir && cat > output.txt")
            assert "subdir" in str(result) or "output.txt" in str(result)


class TestMain:
    """Integration tests for main function."""

    def test_non_bash_tool_approves(self):
        """Should approve non-Bash tools."""
        hook_input = {"tool_name": "Edit", "tool_input": {}}
        with patch("file_overwrite_warning.parse_hook_input", return_value=hook_input):
            with patch("file_overwrite_warning.extract_input_context", return_value={}):
                with patch("builtins.print") as mock_print:
                    hook.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output

    def test_no_redirect_approves(self):
        """Should approve commands without redirects."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        with patch("file_overwrite_warning.parse_hook_input", return_value=hook_input):
            with patch("file_overwrite_warning.extract_input_context", return_value={}):
                with patch("builtins.print") as mock_print:
                    hook.main()
                    output = mock_print.call_args[0][0]
                    assert '"decision": "approve"' in output

    def test_new_file_approves_silently(self):
        """Should approve redirects to new files (no warning)."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "cat > /tmp/new_file_that_does_not_exist.txt"},
        }
        with patch("file_overwrite_warning.parse_hook_input", return_value=hook_input):
            with patch("file_overwrite_warning.extract_input_context", return_value={}):
                with patch.object(Path, "exists", return_value=False):
                    with patch("builtins.print") as mock_print:
                        hook.main()
                        output = mock_print.call_args[0][0]
                        assert '"decision": "approve"' in output
                        assert "message" not in output.lower() or "file-overwrite" not in output

    def test_existing_file_warns(self):
        """Should warn when overwriting existing file."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "cat > /tmp/existing.txt"},
        }
        with patch("file_overwrite_warning.parse_hook_input", return_value=hook_input):
            with patch("file_overwrite_warning.extract_input_context", return_value={}):
                with patch("file_overwrite_warning.resolve_path") as mock_resolve:
                    mock_path = Path("/tmp/existing.txt")
                    mock_resolve.return_value = mock_path
                    with patch.object(Path, "exists", return_value=True):
                        with patch.object(Path, "is_file", return_value=True):
                            with patch("file_overwrite_warning.log_hook_execution"):
                                with patch("builtins.print") as mock_print:
                                    hook.main()
                                    output = mock_print.call_args[0][0]
                                    assert '"decision": "approve"' in output
                                    assert "message" in output
                                    assert "file-overwrite-warning" in output


class TestEdgeCases:
    """Edge case tests."""

    def test_git_heredoc_pattern(self):
        """Should detect git commit heredoc pattern."""
        cmd = """git commit -m "$(cat <<'EOF'
        Message here
        EOF
        )" """
        # This command doesn't write to a file, so should return empty
        result = hook.extract_redirect_targets(cmd)
        # cat << 'EOF' without > should not match
        assert "EOF" not in result

    def test_cat_with_heredoc_and_redirect(self):
        """Should detect cat with heredoc and redirect."""
        cmd = "cat <<'EOF' > script.sh\necho hello\nEOF"
        result = hook.extract_redirect_targets(cmd)
        assert "script.sh" in result

    def test_path_with_spaces_in_quotes(self):
        """Should not crash on quoted paths with spaces (known limitation)."""
        # 現在の実装では引用符もパスに含まれる（既知の制限）
        # クラッシュしないことを確認
        cmd = 'echo "test" > "path with spaces.txt"'
        result = hook.extract_redirect_targets(cmd)
        # 引用符付きパスの正確な処理は将来の改善事項
        assert isinstance(result, list)  # クラッシュせずリストを返す
