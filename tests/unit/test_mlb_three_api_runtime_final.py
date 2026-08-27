from scripts.verify_mlb_no_bbd_runtime import verify_files


def test_current_mlb_provider_boundary_final():
    assert verify_files() == []
