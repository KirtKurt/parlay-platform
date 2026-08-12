from pathlib import Path


def test_publisher_preserves_reports_then_cleans_before_branch_switch():
    source = Path(
        ".github/workflows/mlb-v8-autonomous-controller.yml"
    ).read_text()
    publish = source.split(
        "      - name: Publish monotonic latest state", 1
    )[1]

    preserve = publish.index(
        'cp "$CONTEXT_REPORT" "$tmpdir/context.json"'
    )
    loop = publish.index("for attempt in 1 2 3 4 5; do")
    pre_reset = publish.index("git reset --hard HEAD", loop)
    pre_clean = publish.index("git clean -fd", pre_reset)
    fetch = publish.index("git fetch --no-tags origin", pre_clean)
    checkout = publish.index(
        "git checkout -B mlb-v8-autonomy-state", fetch
    )
    restore = publish.index(
        'cp "$tmpdir/context.json" "$CONTEXT_REPORT"', checkout
    )

    assert preserve < loop < pre_reset < pre_clean < fetch < checkout < restore
    assert publish.count("git reset --hard HEAD") == 1
    assert "git push origin HEAD:main && exit 0" in publish
    assert "sleep $((attempt * 3))" in publish
