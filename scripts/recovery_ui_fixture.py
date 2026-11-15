"""Isolated one-shot node failure for recovery browser acceptance; no real inventory/model."""
from unittest.mock import patch
from meditouch.pharmacy_graph import build_pharmacy_graph
from scripts.pharmacy_ui_fixture import main


def run():
    failures = []
    def progress(stage):
        if stage == 'validate' and not failures:
            failures.append(stage)
            raise RuntimeError('Synthetic interruption after inventory checkpoint')
    def build(*args, **kwargs):
        return build_pharmacy_graph(*args, **kwargs, on_progress=progress)
    with patch('meditouch.api.build_pharmacy_graph', side_effect=build):
        main()


if __name__ == '__main__': run()
