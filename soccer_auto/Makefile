.PHONY: build-%

# SAM invokes one target per soccer_auto Lambda. Preserve the Python package
# directory while excluding every MLB, tennis, legacy soccer, and report path.
build-%:
	mkdir -p "$(ARTIFACTS_DIR)/soccer_auto"
	cp *.py "$(ARTIFACTS_DIR)/soccer_auto/"

