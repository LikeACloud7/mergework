from __future__ import annotations

from app.config import get_settings, validate_deploy_settings


def main() -> int:
    errors = validate_deploy_settings(get_settings())
    if errors:
        print("Deploy readiness check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Deploy readiness check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
