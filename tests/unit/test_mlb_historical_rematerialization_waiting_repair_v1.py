import mlb_historical_rematerialization_waiting_repair_v1 as subject


def test_waiting_phase_becomes_eligible_without_removing_existing_phases():
    class Rematerialization:
        ELIGIBLE_PHASES = {"BACKFILLING", "DATA_RANGE_EXHAUSTED"}

    subject.install(Rematerialization)
    assert Rematerialization.ELIGIBLE_PHASES == {
        "BACKFILLING",
        "DATA_RANGE_EXHAUSTED",
        "WAITING_FOR_SETTLED_HORIZON",
    }
    assert Rematerialization.REMATERIALIZATION_WAITING_REPAIR_VERSION == subject.VERSION


def test_install_is_idempotent():
    class Rematerialization:
        ELIGIBLE_PHASES = {"BACKFILLING"}

    subject.install(Rematerialization)
    first = Rematerialization.ELIGIBLE_PHASES
    subject.install(Rematerialization)
    assert Rematerialization.ELIGIBLE_PHASES is first
