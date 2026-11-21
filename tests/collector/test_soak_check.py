"""The soak check (collector/soak_check.py): the verdict on Prometheus' answers."""

from collector.soak_check import queries, verdict

CLEAN = {"scrapes": 1440, "polls": 1438, "failures": 0, "reconnects": 0, "slowest": 4.0,
         "control_polls": 288, "slowest_control": 0.6,   # one control poll per 25 s
         "slowest_state": 0.4, "environment_polls": 144, "optics_polls": 144,   # each group every 50 s
         "slowest_environment": 1.8, "slowest_optics": 1.7}


def test_a_clean_window_passes():
    assert all(passed for _, passed, _ in verdict(CLEAN, 2))


def test_each_condition_fails_on_its_own():
    for name, bad in (("scrapes", 900), ("polls", 1200), ("failures", 1), ("reconnects", 1), ("slowest", 5.2),
                      ("control_polls", 100), ("environment_polls", 70), ("optics_polls", 70),
                      ("slowest_optics", 24)):
        results = {check: passed for check, passed, _ in verdict({**CLEAN, name: bad}, 2)}
        assert list(results.values()).count(False) == 1, name


def test_no_data_fails():
    assert not any(passed for _, passed, _ in verdict({}, 2))


def test_queries_name_the_device_and_window():
    assert all('device="leaf1"' in q and "[7200s]" in q for q in queries("leaf1", 2).values())


def test_without_control_polls_the_base_conditions_alone():
    results = verdict({k: v for k, v in CLEAN.items() if k in ("scrapes", "polls", "failures", "reconnects", "slowest")}, 2,
                      control_interval=0, hardware_interval=0)
    assert len(results) == 5 and all(passed for _, passed, _ in results)


def test_a_slow_control_poll_fails_its_interval_and_the_state_slot():
    results = {check: passed for check, passed, _ in verdict({**CLEAN, "slowest_control": 31}, 2)}
    assert [c for c, ok in results.items() if not ok] == ["control polls under their interval",
                                                          "state slot under the state interval"]
