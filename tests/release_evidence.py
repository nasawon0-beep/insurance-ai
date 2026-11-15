"""Per-process collection/result evidence; no identity injection."""
import json
import os
from pathlib import Path

collected = []
passed = []
skipped = []
failed = []


def pytest_collection_finish(session):
    collected.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    if report.skipped:
        skipped.append(report.nodeid)
    if report.failed:
        failed.append(report.nodeid)
    if report.when == 'call' and report.passed:
        passed.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ['RELEASE_TEST_EVIDENCE']).write_text(json.dumps({'collected':collected,'passed':passed,'skipped':skipped,'failed':failed,'exitstatus':int(exitstatus)},indent=2),encoding='utf-8')
