from pathlib import Path


def _trainer_block() -> str:
    text = Path("template.yaml").read_text()
    start = text.index("  MLBMLTrainingFunction:")
    end = text.index("\n  SoccerSchedulerFunction:", start)
    return text[start:end]


def test_trainer_receives_canonical_lock_and_label_tables():
    block = _trainer_block()
    assert "SNAPSHOTS_TABLE: !Ref SnapshotsTable" in block
    assert "OUTCOMES_TABLE: !Ref OutcomesTable" in block


def test_trainer_can_write_canonical_labels_but_preserves_table_scope():
    block = _trainer_block()
    assert "- DynamoDBCrudPolicy:\n            TableName: !Ref SnapshotsTable" in block
    assert "- DynamoDBCrudPolicy:\n            TableName: !Ref OutcomesTable" in block
    assert "- DynamoDBReadPolicy:\n            TableName: !Ref OutcomesTable" not in block
