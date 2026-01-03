# Development setup and utilities

.PHONY: setup

# Setup development environment (run after cloning)
setup:
	@command -v lefthook >/dev/null 2>&1 || { \
		echo "Installing lefthook..."; \
		if command -v brew >/dev/null 2>&1; then \
			brew install lefthook; \
		elif command -v go >/dev/null 2>&1; then \
			go install github.com/evilmartians/lefthook@latest; \
		else \
			echo "Please install lefthook: https://github.com/evilmartians/lefthook"; \
			exit 1; \
		fi; \
	}
	lefthook install
	@echo "Setup complete! Git hooks are now active."
