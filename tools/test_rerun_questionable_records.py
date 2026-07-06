from rerun_questionable_records import split_plan_for_partial_copy


def test_split_plan_for_partial_copy_keeps_safe_rows_when_one_image_is_bad():
    plan = [
        {"status": "ready", "original_name": "ok-1.jpg"},
        {"status": "missing_result", "original_name": "bad-corrupt.jpg"},
        {"status": "no_change", "original_name": "ok-2.jpg"},
    ]

    safe, blocked = split_plan_for_partial_copy(plan)

    assert [row["original_name"] for row in safe] == ["ok-1.jpg", "ok-2.jpg"]
    assert [row["original_name"] for row in blocked] == ["bad-corrupt.jpg"]
