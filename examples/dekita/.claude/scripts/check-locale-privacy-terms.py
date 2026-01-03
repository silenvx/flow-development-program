#!/usr/bin/env python3
"""プライバシー/利用規約セクションがja.jsonのみに存在するか確認する。

Why:
    プライバシーポリシーと利用規約は日本語のみ対応であり、
    他のロケールファイルに誤って追加されることを防ぐため。

What:
    - main(): ロケールファイルをスキャンしprivacy/termsセクションを検出

Remarks:
    - ja.json以外にprivacy/termsセクションがあればCI失敗
    - 他ロケールはfallbackLng: "ja"でフォールバック

Changelog:
    - silenvx/dekita#1200: ロケールチェック機能を追加
"""

import json
import sys
from pathlib import Path


def main():
    locales_dir = Path(__file__).parent.parent.parent / "frontend/src/i18n/locales"

    if not locales_dir.exists():
        print(f"Error: Locales directory not found: {locales_dir}")
        sys.exit(1)

    errors = []

    for locale_file in locales_dir.glob("*.json"):
        # ja.json is allowed to have privacy/terms
        if locale_file.name == "ja.json":
            continue

        try:
            with open(locale_file, encoding="utf-8") as f:
                data = json.load(f)

            # Check for privacy section (as object, not footer.privacy string)
            if "privacy" in data and isinstance(data["privacy"], dict):
                errors.append(
                    f"{locale_file.name}: has 'privacy' section (should only be in ja.json)"
                )

            # Check for terms section (as object, not footer.terms string)
            if "terms" in data and isinstance(data["terms"], dict):
                errors.append(
                    f"{locale_file.name}: has 'terms' section (should only be in ja.json)"
                )

        except json.JSONDecodeError as e:
            errors.append(f"{locale_file.name}: JSON parse error: {e}")
        except Exception as e:
            errors.append(f"{locale_file.name}: Error: {e}")

    if errors:
        print("❌ Locale privacy/terms check failed:")
        for error in errors:
            print(f"  - {error}")
        print()
        print("Privacy policy and Terms of service should only be in ja.json.")
        print("Other locales use fallback to Japanese (fallbackLng: 'ja').")
        sys.exit(1)

    print("✅ Locale privacy/terms check passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
