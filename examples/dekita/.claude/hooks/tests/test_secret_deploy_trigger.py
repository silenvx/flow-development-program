"""secret-deploy-trigger.py のテスト"""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Import hook module with dynamic loading due to hyphens in filename
sys.path.insert(0, str(Path(__file__).parent.parent))
HOOK_PATH = Path(__file__).parent.parent / "secret-deploy-trigger.py"
_spec = importlib.util.spec_from_file_location("secret_deploy_trigger", HOOK_PATH)
secret_deploy_trigger = importlib.util.module_from_spec(_spec)
sys.modules["secret_deploy_trigger"] = secret_deploy_trigger
_spec.loader.exec_module(secret_deploy_trigger)


class TestSecretDeployTrigger:
    """フロントエンドシークレット更新追跡フックのテスト"""

    def setup_method(self):
        """テスト前にトラッキングファイルを削除"""
        if secret_deploy_trigger.TRACKING_FILE.exists():
            secret_deploy_trigger.TRACKING_FILE.unlink()

    def teardown_method(self):
        """テスト後にトラッキングファイルをクリーンアップ"""
        if secret_deploy_trigger.TRACKING_FILE.exists():
            secret_deploy_trigger.TRACKING_FILE.unlink()

    def test_load_tracking_data_empty_file(self):
        """トラッキングファイルがない場合、空のデータを返す"""
        data = secret_deploy_trigger.load_tracking_data()
        assert data == {"secrets": [], "updated_at": None}

    def test_load_tracking_data_with_existing_data(self):
        """既存のトラッキングデータを読み込む"""
        test_data = {"secrets": ["VITE_API_KEY"], "updated_at": "2025-01-01T00:00:00"}
        secret_deploy_trigger.TRACKING_FILE.write_text(json.dumps(test_data))

        data = secret_deploy_trigger.load_tracking_data()
        assert data == test_data

    def test_save_tracking_data(self):
        """トラッキングデータを保存する"""
        test_data = {"secrets": ["VITE_NEW_SECRET"], "updated_at": "2025-01-01T00:00:00"}
        secret_deploy_trigger.save_tracking_data(test_data)

        saved_data = json.loads(secret_deploy_trigger.TRACKING_FILE.read_text())
        assert saved_data == test_data

    def test_main_ignores_non_secret_commands(self):
        """gh secret set 以外のコマンドは無視する"""
        input_data = json.dumps(
            {
                "tool_input": {"command": "gh issue list"},
                "tool_result": {"exit_code": 0},
            }
        )

        with patch("sys.stdin", io.StringIO(input_data)):
            with patch("builtins.print") as mock_print:
                secret_deploy_trigger.main()
                mock_print.assert_called()
                output = json.loads(mock_print.call_args[0][0])
                assert output.get("continue")

    def test_main_ignores_failed_commands(self):
        """失敗したコマンドは無視する"""
        input_data = json.dumps(
            {
                "tool_input": {"command": "gh secret set VITE_API_KEY"},
                "tool_result": {"exit_code": 1},
            }
        )

        with patch("sys.stdin", io.StringIO(input_data)):
            with patch("builtins.print"):
                secret_deploy_trigger.main()
                # トラッキングファイルは作成されない
                assert not secret_deploy_trigger.TRACKING_FILE.exists()

    def test_main_ignores_non_vite_secrets(self):
        """VITE_ プレフィックスのないシークレットは無視する"""
        input_data = json.dumps(
            {
                "tool_input": {"command": "gh secret set OTHER_SECRET"},
                "tool_result": {"exit_code": 0},
            }
        )

        with patch("sys.stdin", io.StringIO(input_data)):
            with patch("builtins.print"):
                secret_deploy_trigger.main()
                # トラッキングファイルは作成されない
                assert not secret_deploy_trigger.TRACKING_FILE.exists()
